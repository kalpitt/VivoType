#!/usr/bin/env python3
"""test_record_commit.py — self-contained tests for the ROADMAP.md guard in
record_commit.py (2026-07-31). Plain python3, stdlib only, no pytest
dependency (matches this repo's toolchain). Exits non-zero on any failure.

Run: python3 scripts/test_record_commit.py
"""
import os
import subprocess
import sys
import tempfile
import unittest.mock as mock

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
import record_commit as rc  # noqa: E402

BASE = """# ROADMAP — Test

<!-- HUMAN-OWNED. Agents: propose edits via handoff notes; never rewrite this
file yourself. -->

> **DRAFT — FOR KALPIT'S REVIEW** (drafted 2026-07-12 during the governance
> migration; reorder/cut/rewrite freely, then delete this banner and set the
> review date.)

Last reviewed by Kalpit: <pending first review>

## Now
- Item A — done, provably (see STATE.md)
- Item B — still active

## Next
- Item C

## Later (parked)
- Item D

## Not doing (decided against — don't re-propose)
- Item E — never, hard rule 1

## Done
- Item G — shipped, PR #9
"""

RESULTS = []


def check(name, new_text, message, expect_allowed):
    """Run validate_roadmap(old=BASE, new=new_text, message) in an isolated
    temp dir, with read_ref_text patched to hand back BASE as 'origin/main'.
    Records PASS/FAIL into RESULTS."""
    with tempfile.TemporaryDirectory() as root:
        path = os.path.join(root, "ROADMAP.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_text)
        with mock.patch.object(rc, "read_ref_text", return_value=BASE):
            allowed = True
            err = None
            try:
                rc.validate_roadmap(root, "ROADMAP.md", message)
            except SystemExit:
                allowed = False
            except Exception as e:  # noqa: BLE001
                allowed = False
                err = f"unexpected exception: {e!r}"
    ok = (allowed == expect_allowed) and err is None
    RESULTS.append((name, ok, allowed, expect_allowed, err))


def record(name, ok):
    """Record a direct (non-guard) assertion into the same results table."""
    RESULTS.append((name, bool(ok), None, None, None if ok else "assertion failed"))


def delete_line(text, needle):
    return "\n".join(l for l in text.splitlines() if needle not in l) + "\n"


def replace_line(text, needle, replacement):
    return "\n".join(replacement if needle in l else l
                      for l in text.splitlines()) + "\n"


def swap_now_lines(text):
    lines = text.splitlines()
    ia = next(i for i, l in enumerate(lines) if "Item A" in l)
    ib = next(i for i, l in enumerate(lines) if "Item B" in l)
    lines[ia], lines[ib] = lines[ib], lines[ia]
    return "\n".join(lines) + "\n"


def real_sha():
    """A commit SHA that genuinely exists in whichever repo this runs in.

    The evidence check resolves SHA-shaped tokens against real history, so this
    test can't hardcode one — the file is byte-identical across every repo.
    """
    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=REPO_ROOT, text=True, capture_output=True,
    ).stdout.strip()


def main():
    # 1. delete one item from ## Now with a SHA in the message -> ALLOWED
    check(
        "delete Now item + PR-reference evidence -> ALLOWED",
        delete_line(BASE, "Item A"),
        "state: reconcile ROADMAP — shipped in #42",
        expect_allowed=True,
    )

    # SHA evidence is resolved against REAL history, so it must be exercised
    # against this actual repo — the guard tests above run in a temp dir with
    # no git, where every SHA would fail for the wrong reason.
    record("evidence: a real SHA from this repo -> credible",
           rc.evidence_is_credible(f"state: done in {real_sha()}", REPO_ROOT) is True)
    record("evidence: a fabricated SHA -> NOT credible",
           rc.evidence_is_credible("state: done in deadbeef1", REPO_ROOT) is False)
    record("evidence: no evidence at all -> NOT credible",
           rc.evidence_is_credible("state: just tidying", REPO_ROOT) is False)

    # 2. delete an item with NO evidence in the message -> REJECTED
    check(
        "delete Now item + no evidence -> REJECTED",
        delete_line(BASE, "Item A"),
        "state: tidy up the roadmap a bit",
        expect_allowed=False,
    )

    # 3. adding a line to ## Next -> REJECTED
    check(
        "add line to Next -> REJECTED",
        BASE.replace("## Next\n- Item C\n",
                     "## Next\n- Item C\n- Item F — new idea\n"),
        "state: add item — EVIDENCE: because I said so",
        expect_allowed=False,
    )

    # 4. reordering two existing lines in ## Now -> REJECTED (subsequence)
    check(
        "reorder two Now lines -> REJECTED",
        swap_now_lines(BASE),
        "state: reorder — EVIDENCE: reprioritized",
        expect_allowed=False,
    )

    # 5. editing a line inside ## Not doing -> REJECTED
    check(
        "edit line inside Not doing -> REJECTED",
        replace_line(BASE, "Item E", "- Item E — actually reconsidering"),
        "state: revisit — EVIDENCE: changed my mind",
        expect_allowed=False,
    )

    # 6. deleting the DRAFT banner + setting the review date -> ALLOWED
    banner_gone = "\n".join(
        l for l in BASE.splitlines()
        if not l.strip().startswith(">")
    ) + "\n"
    banner_gone = replace_line(
        banner_gone, "Last reviewed by Kalpit:",
        "Last reviewed by Kalpit: 2026-08-01"
    )
    check(
        "delete DRAFT banner + set review date -> ALLOWED",
        banner_gone,
        "state: first review",
        expect_allowed=True,
    )

    # 7. rewriting an existing line's text in place (same position) -> REJECTED
    check(
        "rewrite a Now line in place -> REJECTED",
        replace_line(BASE, "Item B", "- Item B — completely different text"),
        "state: reword — EVIDENCE: clarity",
        expect_allowed=False,
    )

    # 8. deleting a line inside ## Done -> REJECTED (shipped history is
    # byte-identical protected, same as ## Not doing)
    check(
        "delete line inside Done -> REJECTED",
        delete_line(BASE, "Item G"),
        "state: tidy up shipped history — EVIDENCE: cleanup",
        expect_allowed=False,
    )

    # Rule 6b. Deleting a section heading is a pure, order-preserving deletion,
    # so rules 2/3 allow it — but the EFFECT is to re-parent every item beneath
    # it into the section above: a Class B promotion wearing Class A clothing.
    # Must be rejected even WITH valid evidence, because no citation can make
    # restructuring a fact.
    check(
        "delete a '## Next' heading (silently promotes its items to Now) -> REJECTED",
        delete_line(BASE, "## Next"),
        "state: tidy up the sections",
        expect_allowed=False,
    )
    check(
        "delete a '## Next' heading even WITH evidence -> still REJECTED",
        delete_line(BASE, "## Next"),
        "state: tidy sections — EVIDENCE: a1b2c3d",
        expect_allowed=False,
    )

    failed = [r for r in RESULTS if not r[1]]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed\n")
    for name, ok, allowed, expect, err in RESULTS:
        status = "PASS" if ok else "FAIL"
        detail = f" (got allowed={allowed}, expected={expect}"
        detail += f", error={err})" if err else ")"
        print(f"[{status}] {name}" + ("" if ok else detail))

    if failed:
        sys.exit(1)
    print("\nAll ROADMAP guard tests passed.")


if __name__ == "__main__":
    main()
