#!/usr/bin/env python3
"""test_publish_to_public.py — runs the real publish script in a sandbox: a
throwaway master repo (with a local bare origin) next to a throwaway mirror.
No network, no real data: every "private" file here is synthetic. Stdlib
only; needs bash, git, tar and rsync on PATH.

Run: python3 scripts/test_publish_to_public.py
"""
import os
import shutil
import subprocess
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.abspath(__file__))

COMMITTED = {
    "app.py": "print('hi')\n",
    ".gitignore": "*.bak\n*.tmp\n",
    # Built at runtime so this published file never carries a canary-shaped
    # string that a leak search would flag.
    "context/STATE.md": "VIVOTYPE-PRIVATE-" + "CANARY-test-0001\n",
    "core/data/README.md": "readme\n",
    "core/data/.gitkeep": "",
    "core/data/newdir/.gitkeep": "",
    "core/data/labels.csv": "synthetic\n",
    "core/data/lexicon/contacts.json": "{}\n",
    "core/data/prompts/training-paragraph.txt": "synthetic\n",
    # Tracked but under names the old denylist did not know:
    "core/data/backup/contacts.json": "{}\n",
    "core/data/notes.txt": "synthetic\n",
}
ALLOWED_DATA = {"core/data/README.md", "core/data/.gitkeep",
                "core/data/newdir/.gitkeep"}


# No personal git config (signing, hooks, identity) leaks into the sandbox.
ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
           GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="t@example.com",
           GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="t@example.com")


def run(cwd, *args, check=True):
    return subprocess.run(list(args), cwd=cwd, check=check, text=True,
                          capture_output=True, stdin=subprocess.DEVNULL,
                          env=ENV)


class PublishSandbox(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="publish-test-")
        self.master = os.path.join(self.tmp, "master")
        self.mirror = os.path.join(self.tmp, "VivoType")
        run(self.tmp, "git", "init", "-q", "--bare", "-b", "main", "origin.git")
        run(self.tmp, "git", "clone", "-q", "origin.git", "master")
        run(self.master, "git", "checkout", "-q", "-b", "main")
        for rel in ("publish_to_public.sh", "lib/publish_safety_gate.sh"):
            dest = os.path.join(self.master, "scripts", rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(os.path.join(SCRIPTS, rel), dest)
        for rel, text in COMMITTED.items():
            self.write(rel, text)
        run(self.master, "git", "add", "-A")
        run(self.master, "git", "commit", "-qm", "init")
        run(self.master, "git", "push", "-q", "origin", "main")
        # Ignored leftovers in the working tree: a backup and a temp file.
        self.write("core/data/corrections.jsonl.bak", "synthetic\n")
        self.write("core/data/user_dictionary.json.tmp", "synthetic\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, text):
        path = os.path.join(self.master, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def publish(self, *args):
        return run(self.master, "bash", "scripts/publish_to_public.sh", *args,
                   check=False)

    def mirror_files(self):
        return set(run(self.mirror, "git", "ls-files").stdout.split())

    def assertRefused(self, proc, needle):
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn(needle, proc.stderr)
        self.assertFalse(os.path.isdir(os.path.join(self.mirror, ".git")))

    def test_core_data_is_an_allowlist(self):
        proc = self.publish()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        files = self.mirror_files()
        self.assertIn("app.py", files)
        self.assertEqual({f for f in files if f.startswith("core/data/")},
                         ALLOWED_DATA)
        self.assertFalse(any(f.startswith("context/") for f in files))

    def test_refuses_off_main(self):
        run(self.master, "git", "checkout", "-q", "-b", "feature/x")
        self.assertRefused(self.publish(), "not main")

    def test_refuses_untracked_file(self):
        self.write("stray.txt", "x\n")
        self.assertRefused(self.publish(), "untracked")

    def test_refuses_head_not_origin_main(self):
        self.write("app.py", "print('ahead')\n")
        run(self.master, "git", "commit", "-qam", "ahead")
        self.assertRefused(self.publish(), "HEAD is not origin/main")

    def test_dry_run_reports_but_continues(self):
        self.write("stray.txt", "x\n")
        proc = self.publish("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("PREFLIGHT", proc.stderr)
        self.assertNotIn("stray.txt", proc.stdout)  # HEAD is previewed

    def test_push_needs_a_terminal(self):
        run(self.tmp, "git", "init", "-q", "--bare", "-b", "main", "pub.git")
        os.makedirs(self.mirror)
        run(self.mirror, "git", "init", "-q", "-b", "main")
        run(self.mirror, "git", "remote", "add", "origin",
            os.path.join(self.tmp, "pub.git"))
        proc = self.publish()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not a terminal", proc.stderr)
        refs = run(self.tmp, "git", "--git-dir", "pub.git", "for-each-ref")
        self.assertEqual(refs.stdout.strip(), "")  # nothing was pushed


if __name__ == "__main__":
    unittest.main(verbosity=2)
