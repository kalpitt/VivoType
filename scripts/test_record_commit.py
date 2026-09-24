#!/usr/bin/env python3
"""test_record_commit.py — tests for record_commit.py's secret scan and the
append-only rule for context/handoffs/. Plain python3, stdlib only, no
network: each test builds a throwaway repo whose "origin" is a local bare
repo, and runs the real script against it as a subprocess.

Run: python3 scripts/test_record_commit.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "record_commit.py")

AGENTS_MD = """# AGENTS.md (test fixture)

```record-lane
context/STATE.md
context/ITERATION_LOG.md
context/handoffs/
```
"""

HANDOFF = "context/handoffs/2026-01-01-session.md"
HANDOFF_V1 = "# Handoff\n\n- did a thing\n- did another thing\n"


def git(cwd, *args):
    return subprocess.run(["git"] + list(args), cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


class RecordLaneRepo(unittest.TestCase):
    """A clone of a local bare origin whose main already carries AGENTS.md,
    context/STATE.md and one handoff."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="record-commit-test-")
        self.origin = os.path.join(self.tmp, "origin.git")
        self.clone = os.path.join(self.tmp, "clone")
        git(self.tmp, "init", "-q", "--bare", "-b", "main", self.origin)
        git(self.tmp, "clone", "-q", self.origin, self.clone)
        for k, v in (("user.email", "t@example.com"), ("user.name", "Test"),
                     ("commit.gpgsign", "false")):
            git(self.clone, "config", k, v)
        git(self.clone, "checkout", "-q", "-b", "main")
        self.write("AGENTS.md", AGENTS_MD)
        self.write("context/STATE.md", "# State\n\nall good\n")
        self.write(HANDOFF, HANDOFF_V1)
        git(self.clone, "add", "-A")
        git(self.clone, "commit", "-qm", "init")
        git(self.clone, "push", "-q", "origin", "main")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, text):
        path = os.path.join(self.clone, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def record(self, *files):
        return subprocess.run(
            [sys.executable, SCRIPT, "state: test"] + list(files),
            cwd=self.clone, capture_output=True, text=True, timeout=60)

    def origin_text(self, rel):
        return git(self.origin, "show", f"main:{rel}")

    def assertBlocked(self, proc, needle):
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("BLOCKED", proc.stderr)
        self.assertIn(needle, proc.stderr)

    def assertRecorded(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("recorded to main", proc.stdout)


class GitHubTokenPatterns(RecordLaneRepo):
    # Synthetic, well-formed but never-issued tokens (prefix + 36 chars).
    BODY = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"

    def test_every_github_token_prefix_is_blocked(self):
        for prefix in ("ghp_", "gho_", "ghu_", "ghs_", "ghr_"):
            with self.subTest(prefix=prefix):
                self.write("context/STATE.md",
                           f"# State\n\ntoken {prefix}{self.BODY}\n")
                self.assertBlocked(self.record("context/STATE.md"),
                                   "GitHub token")
        self.assertEqual(self.origin_text("context/STATE.md"),
                         "# State\n\nall good\n")

    def test_token_glued_to_a_word_is_blocked(self):
        # "\b" fails after "_" (both word characters), so an env-style
        # "TOKEN_ghp_..." or a trailing "_suffix" used to slip through.
        for text in (f"TOKEN_ghp_{self.BODY}", f"ghp_{self.BODY}_old",
                     "AWS_AKIAABCDEFGHIJKLMNOP", "KEY_sk-" + "a" * 40):
            with self.subTest(text=text):
                self.write("context/STATE.md", f"# State\n\n{text}\n")
                self.assertEqual(self.record("context/STATE.md").returncode, 1)

    def test_prefix_without_a_token_body_is_fine(self):
        self.write("context/STATE.md", "# State\n\nthe gho_ prefix is OAuth\n")
        self.assertRecorded(self.record("context/STATE.md"))


class HandoffsAreAppendOnly(RecordLaneRepo):

    def test_append_is_recorded(self):
        new = HANDOFF_V1 + "- and a third thing\n"
        self.write(HANDOFF, new)
        self.assertRecorded(self.record(HANDOFF))
        self.assertEqual(self.origin_text(HANDOFF), new)

    def test_truncation_is_refused(self):
        self.write(HANDOFF, "# Handoff\n")
        self.assertBlocked(self.record(HANDOFF), "append-only")
        self.assertEqual(self.origin_text(HANDOFF), HANDOFF_V1)

    def test_rewrite_in_the_middle_is_refused(self):
        self.write(HANDOFF, HANDOFF_V1.replace("did a thing", "did nothing")
                   + "- appended too\n")
        self.assertBlocked(self.record(HANDOFF), "append-only")
        self.assertEqual(self.origin_text(HANDOFF), HANDOFF_V1)

    def test_line_ending_change_is_refused(self):
        # Byte-for-byte: a CRLF rewrite of the old text is not an append.
        self.write(HANDOFF, HANDOFF_V1.replace("\n", "\r\n") + "- more\r\n")
        self.assertBlocked(self.record(HANDOFF), "append-only")

    def test_new_handoff_file_is_recorded(self):
        rel = "context/handoffs/2026-01-02-next.md"
        self.write(rel, "# Next\n")
        self.assertRecorded(self.record(rel))
        self.assertEqual(self.origin_text(rel), "# Next\n")

    def test_non_handoff_record_may_still_be_rewritten(self):
        # STATE.md is a living snapshot, not a log: unchanged behaviour.
        self.write("context/STATE.md", "# State\n\nrewritten\n")
        self.assertRecorded(self.record("context/STATE.md"))
        self.assertEqual(self.origin_text("context/STATE.md"),
                         "# State\n\nrewritten\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
