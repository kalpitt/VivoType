"""VivoType bounded voice-editing commands (independent of the ML model).

Recognizes a small, literal set of spoken command phrases in the raw ASR text
and either transforms the dictated remainder or signals a structural action
("scratch that") for the macOS client to perform.

Design rules (see docs/next-features-handoff.md Feature 4):
  - Literal phrases only — no fuzzy matching, no ML. A false positive now
    DELETES real user text (scratch), so matching stays conservative.
  - Detection runs on RAW ASR output BEFORE post-processing: promoted-name
    replacements or capitalization could rewrite the phrase itself otherwise.
  - Only standalone or trailing phrases are recognized ("hello world all caps
    that"); leading placement is deliberately NOT a command.
  - Transforms apply AFTER post-processing on the clean remainder (whitespace
    normalization would destroy an earlier-inserted "\n\n").
"""

from __future__ import annotations

import re

# The one signal the Swift client acts on structurally; everything else rides
# inside the returned text. Kept as a module constant so both sides of the
# NDJSON contract reference the same literal.
SCRATCH_SIGNAL = "scratch_that"

# Transform tokens (contract shared with core/tests/test_commands.py).
TRANSFORM_NONE = "none"

# Transform phrases anchor at the END of the utterance (allowing trailing
# sentence punctuation): "hello world all caps that". Longest-first so a more
# specific phrase wins if two could anchor. Non-destructive, so trailing
# matching is safe.
_TRAILING_PHRASES = [
    (r"make\s+that\s+a\s+bullet\s+list", "bullet"),
    (r"all\s+caps\s+that", "upper"),
    (r"new\s+paragraph", "newpara"),
    (r"cap\s+that", "cap"),
    (r"new\s+line", "newline"),
]
_TRAILING_RE = re.compile(
    r"(?:^|\s)(%s)\s*[.!?,।॥]*$" % "|".join(src for src, _ in _TRAILING_PHRASES),
    re.IGNORECASE,
)
# A trailing phrase preceded by one of these words is ordinary content, not a
# command: a determiner/possessive/quantifier makes it a noun phrase ("the
# metro opened a new line", "a whole new line"), and "to" makes it an
# infinitive ("don't forget to cap that"). Punctuation after the preceding
# word ("I like this. New line") ends that clause, so the command still fires.
# "that" is left out: it is far more often a pronoun or conjunction ("send
# that new line") than a determiner. Determiners guard only the phrases that
# can be ordinary words; "to" guards every phrase ("remember to make that a
# bullet list").
_CONTENT_PRECEDERS = frozenset(
    "a an the this these those my our your his her their its whose "
    "another every each any some no whole brand entire".split()
)
_GUARDED_TRANSFORMS = frozenset(("newline", "newpara", "cap"))

_PHRASE_TO_TRANSFORM = {
    src.replace("\\s+", " ").replace("\\s", " "): transform
    for src, transform in _TRAILING_PHRASES
}

# Scratch DELETES user text, so it gets the strictest matching: the utterance
# must BE the phrase ("scratch that." / "DELETE THAT") — nothing before it.
# A trailing match ("I asked him to please scratch that") would destroy real
# dictated content on a false positive; the primary flow (dictate, release,
# say "scratch that") is unaffected.
_SCRATCH_RE = re.compile(
    r"^(?:scratch|delete)\s+that\s*[.!?,।॥]*$", re.IGNORECASE
)


def detect_command(raw: str, leading_fillers=None):
    """Detect a voice command in the utterance.

    Returns (signal, remaining_text, transform):
      signal    — SCRATCH_SIGNAL for structural actions, else None.
      remaining — the dictated content with the phrase stripped ("" for a
                  standalone command).
      transform — one of 'none'|'upper'|'cap'|'bullet'|'newpara'|'newline'.

    Scratch matches only a STANDALONE phrase; transform phrases may close a
    longer utterance. Anything else returns (None, raw, TRANSFORM_NONE)
    unchanged.

    `leading_fillers` (the configured filler list) are stripped from the front
    before standalone-scratch matching: Whisper habitually prefixes "um", and
    a filler must not turn a spoken command into typed text. Fillers are NOT
    stripped for transform phrases or kept in the remainder — post-processing
    owns filler removal there."""
    text = (raw or "").strip()
    if not text:
        return None, raw or "", TRANSFORM_NONE

    if leading_fillers:
        fillers = {f.lower() for f in leading_fillers if f}
        tokens = text.split()
        while tokens and tokens[0].lower().strip(".,!?;:") in fillers:
            tokens.pop(0)
        scratch_candidate = " ".join(tokens)
    else:
        scratch_candidate = text

    if _SCRATCH_RE.match(scratch_candidate):
        return SCRATCH_SIGNAL, "", TRANSFORM_NONE

    match = _TRAILING_RE.search(text)
    if not match:
        return None, raw, TRANSFORM_NONE
    phrase = re.sub(r"\s+", " ", match.group(1).lower())
    transform = _PHRASE_TO_TRANSFORM.get(phrase, TRANSFORM_NONE)
    before = text[: match.start()].split()
    prev = before[-1].lower().strip("\"'“”‘’") if before else ""
    if prev == "to" or (transform in _GUARDED_TRANSFORMS and prev in _CONTENT_PRECEDERS):
        return None, raw, TRANSFORM_NONE
    return None, text[: match.start()].strip(), transform


def apply_transform(text: str, transform: str) -> str:
    """Apply a text transform AFTER post-processing (clean casing/spacing)."""
    # A standalone "new line" / "new paragraph" leaves no remainder but must
    # still type the break itself.
    if transform == "newpara":
        return text + "\n\n"
    if transform == "newline":
        return text + "\n"
    if not text:
        return text
    if transform == "upper":
        return text.upper()
    if transform == "cap":
        # Capitalize the first letter without touching acronyms later in the
        # sentence ("iPhone stays weird" keeps its lowercase i after "The").
        for index, char in enumerate(text):
            if char.isalpha():
                return text[:index] + char.upper() + text[index + 1:]
        return text
    if transform == "bullet":
        # Blank lines stay separators; prefixing them would paint "- " ghosts.
        # (text is non-empty here, so splitlines() is never empty.)
        return "\n".join(
            ("- " + line if line.strip() else line) for line in text.splitlines()
        )
    return text
