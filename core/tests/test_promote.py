"""Tests for core/promote.py — promoting captured corrections into the rules."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from core import promote

NO_ENGLISH = set()  # explicit, so classify() doesn't depend on the host word list


class GroupingTests(unittest.TestCase):
    def test_groups_and_counts_by_frequency(self):
        items = [
            {"from": "Kalpith", "to": "Kalpit"},
            {"from": "kalpith", "to": "Kalpit"},  # same correction, different case
            {"from": "blr", "to": "Bengaluru"},
        ]
        groups = promote.group_corrections(items)
        self.assertEqual(groups[0]["to"], "Kalpit")
        self.assertEqual(groups[0]["count"], 2)
        self.assertEqual(groups[1]["count"], 1)


class ClassifyTests(unittest.TestCase):
    def test_near_miss_name_goes_to_lexicon(self):
        self.assertEqual(promote.classify("Kalpith", "Kalpit", NO_ENGLISH), "lexicon")

    def test_abbreviation_goes_to_dictionary(self):
        self.assertEqual(promote.classify("blr", "Bengaluru", NO_ENGLISH), "dictionary")

    def test_phrase_goes_to_dictionary(self):
        self.assertEqual(promote.classify("100k", "1 lakh", NO_ENGLISH), "dictionary")

    def test_real_english_target_goes_to_dictionary(self):
        # If the target is a common word, don't treat it as a name.
        self.assertEqual(promote.classify("Moor", "More", {"more"}), "dictionary")


class PromoteTargetTests(unittest.TestCase):
    def test_promote_to_lexicon_adds_and_dedupes(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "contacts.json"
            promote.promote_to_lexicon("Zubin", path)
            promote.promote_to_lexicon("Zubin", path)  # dedupe
            promote.promote_to_lexicon("Aarav", path)
            names = json.loads(path.read_text())["names"]
            self.assertEqual(names, ["Aarav", "Zubin"])  # sorted, deduped

    def test_promote_to_dictionary_adds_rule(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cfg.json"
            path.write_text(json.dumps({"fillers": ["um"], "replacements": {}}))
            promote.promote_to_dictionary("BLR", "Bengaluru", path)
            data = json.loads(path.read_text())
            self.assertEqual(data["replacements"]["blr"], "Bengaluru")
            self.assertEqual(data["fillers"], ["um"])  # untouched

    def test_rewrite_log_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "corrections.jsonl"
            entries = [{"from": "a", "to": "b"}, {"from": "c", "to": "d"}]
            promote.rewrite_log(path, entries)
            lines = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
            self.assertEqual(lines, entries)
            promote.rewrite_log(path, [])  # empty log must not leave stale rows
            self.assertEqual(path.read_text(), "")


class CorruptionSafetyTests(unittest.TestCase):
    """A corrupt promotion target must never be silently overwritten (data loss)."""

    def test_promote_to_dictionary_refuses_corrupt_target(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cfg.json"
            corrupt = '{"replacements": {"blr": "Bengaluru"  OOPS not json'
            path.write_text(corrupt, encoding="utf-8")
            with self.assertRaises(ValueError):
                promote.promote_to_dictionary("foo", "bar", path)
            # the user's (unparseable) file is preserved, not clobbered
            self.assertEqual(path.read_text(encoding="utf-8"), corrupt)

    def test_promote_to_lexicon_refuses_corrupt_target(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "contacts.json"
            corrupt = '{"names": ["Zubin"  OOPS'
            path.write_text(corrupt, encoding="utf-8")
            with self.assertRaises(ValueError):
                promote.promote_to_lexicon("Aarav", path)
            self.assertEqual(path.read_text(encoding="utf-8"), corrupt)

    def test_apply_one_preserves_log_when_target_corrupt(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "corrections.jsonl"
            lex = Path(d) / "contacts.json"
            cfg = Path(d) / "cfg.json"
            cfg.write_text("{ not valid json", encoding="utf-8")
            log.write_text(json.dumps({"from": "blr", "to": "Bengaluru"}) + "\n")
            result = promote.apply_one("promote", "blr", "Bengaluru",
                                       log, lex, cfg, english=NO_ENGLISH)
            self.assertFalse(result["ok"])
            # the correction must NOT be dropped from the log on a failed write
            self.assertEqual(len(log.read_text().strip().splitlines()), 1)


class AssessRiskTests(unittest.TestCase):
    """The quality gate that keeps garbage rules (the 'minu' -> 'menu' class)
    out of bulk promotion."""

    ENGLISH = {"give", "make", "menu", "today", "contact", "integrity", "minu"}

    def test_common_english_from_is_risky(self):
        risky, reason = promote.assess_risk("give", "make", 5, self.ENGLISH)
        self.assertTrue(risky)
        self.assertIn("real English word", reason)

    def test_single_sighting_is_risky(self):
        risky, reason = promote.assess_risk("wani", "Vaani", 1, self.ENGLISH)
        self.assertTrue(risky)
        self.assertIn("once", reason)

    def test_repeated_uncommon_word_is_safe(self):
        risky, reason = promote.assess_risk("wani", "Vaani", 2, self.ENGLISH)
        self.assertFalse(risky)
        self.assertEqual(reason, "")

    def test_multiword_from_not_flagged_as_common_word(self):
        # The common-word check applies to single words only; a phrase source
        # is much more specific, so two sightings make it safe.
        risky, _ = promote.assess_risk("view type", "VivoType", 2, self.ENGLISH)
        self.assertFalse(risky)

    def test_both_reasons_reported_together(self):
        risky, reason = promote.assess_risk("menu", "minu", 1, self.ENGLISH)
        self.assertTrue(risky)
        self.assertIn("real English word", reason)
        self.assertIn("once", reason)

    def test_missing_word_list_degrades_to_count_gate_only(self):
        # /usr/share/dict/words absent → empty english set: the common-word
        # gate is silently gone (promote.main warns to stderr), but the
        # sighting-count gate must keep working.
        risky, _ = promote.assess_risk("give", "make", 5, set())
        self.assertFalse(risky)  # documented degradation, not a crash
        risky_once, reason = promote.assess_risk("give", "make", 1, set())
        self.assertTrue(risky_once)
        self.assertIn("once", reason)


class MainTests(unittest.TestCase):
    def _setup(self, d):
        log = Path(d) / "corrections.jsonl"
        lex = Path(d) / "contacts.json"
        cfg = Path(d) / "cfg.json"
        cfg.write_text(json.dumps({"replacements": {}}))
        log.write_text(
            json.dumps({"from": "Kalpith", "to": "Kalpit"}) + "\n"
            + json.dumps({"from": "Kalpith", "to": "Kalpit"}) + "\n"
            + json.dumps({"from": "blr", "to": "Bengaluru"}) + "\n"
        )
        return log, lex, cfg

    def _run(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = promote.main(argv)
        return rc, out.getvalue()

    def test_yes_promotes_safe_rules_and_keeps_risky_ones(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._setup(d)
            rc, out = self._run(["--log", str(log), "--lexicon", str(lex), "--config", str(cfg), "--yes"])
            self.assertEqual(rc, 0)
            # Seen 2× and not a common word → promoted.
            self.assertIn("Kalpit", json.loads(lex.read_text())["names"])
            # Seen only once → risky; bulk promotion must NOT take it, and the
            # entry stays in the log for a deliberate per-rule decision later.
            self.assertNotIn("blr", json.loads(cfg.read_text())["replacements"])
            self.assertIn("skipped (risky)", out)
            remaining = [json.loads(l) for l in log.read_text().strip().splitlines()]
            self.assertEqual([e["from"] for e in remaining], ["blr"])

    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._setup(d)
            before = log.read_text()
            self._run(["--log", str(log), "--lexicon", str(lex), "--config", str(cfg), "--dry-run"])
            self.assertFalse(lex.exists())
            self.assertEqual(json.loads(cfg.read_text())["replacements"], {})
            self.assertEqual(log.read_text(), before)  # log intact

    def test_skip_keeps_entries(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._setup(d)
            import builtins
            orig_input = builtins.input
            builtins.input = lambda *a, **k: "n"  # decline everything
            try:
                self._run(["--log", str(log), "--lexicon", str(lex), "--config", str(cfg)])
            finally:
                builtins.input = orig_input
            self.assertFalse(lex.exists())
            # both groups kept (3 original lines remain)
            self.assertEqual(len(log.read_text().strip().splitlines()), 3)

    def test_discard_removes_without_promoting(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._setup(d)
            import builtins
            orig_input = builtins.input
            builtins.input = lambda *a, **k: "d"  # discard everything
            try:
                self._run(["--log", str(log), "--lexicon", str(lex), "--config", str(cfg)])
            finally:
                builtins.input = orig_input
            self.assertFalse(lex.exists())  # nothing promoted
            self.assertEqual(json.loads(cfg.read_text())["replacements"], {})
            self.assertEqual(log.read_text().strip(), "")  # log fully cleared


class GuiInterfaceTests(unittest.TestCase):
    """The --list-json / --apply interface the GUI panel calls."""

    def _setup(self, d):
        log = Path(d) / "corrections.jsonl"
        lex = Path(d) / "contacts.json"
        cfg = Path(d) / "cfg.json"
        cfg.write_text(json.dumps({"replacements": {}}))
        log.write_text(
            json.dumps({"from": "Kalpith", "to": "Kalpit"}) + "\n"
            + json.dumps({"from": "Kalpith", "to": "Kalpit"}) + "\n"
            + json.dumps({"from": "blr", "to": "Bengaluru"}) + "\n"
        )
        return log, lex, cfg

    def _run(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = promote.main(argv)
        return rc, out.getvalue()

    def test_list_json_reports_groups_and_targets(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._setup(d)
            _, out = self._run(["--log", str(log), "--lexicon", str(lex),
                                "--config", str(cfg), "--list-json"])
            data = json.loads(out)
            self.assertEqual(data[0]["from"], "Kalpith")
            self.assertEqual(data[0]["count"], 2)
            self.assertEqual(data[0]["target"], "lexicon")
            blr = next(x for x in data if x["from"] == "blr")
            self.assertEqual(blr["target"], "dictionary")

    def test_list_json_includes_risk_fields(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._setup(d)
            _, out = self._run(["--log", str(log), "--lexicon", str(lex),
                                "--config", str(cfg), "--list-json"])
            data = json.loads(out)
            kalpith = next(x for x in data if x["from"] == "Kalpith")
            self.assertFalse(kalpith["risky"])       # seen 2×, not a common word
            blr = next(x for x in data if x["from"] == "blr")
            self.assertTrue(blr["risky"])            # seen only once
            self.assertIn("once", blr["risk_reason"])

    def test_apply_promote_routes_and_removes(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._setup(d)
            self._run(["--log", str(log), "--lexicon", str(lex), "--config", str(cfg),
                       "--apply", "--from", "blr", "--to", "Bengaluru", "--action", "promote"])
            self.assertEqual(json.loads(cfg.read_text())["replacements"]["blr"], "Bengaluru")
            # only the blr line removed; the two Kalpith lines remain
            self.assertEqual(len(log.read_text().strip().splitlines()), 2)

    def test_apply_discard_removes_only(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._setup(d)
            self._run(["--log", str(log), "--lexicon", str(lex), "--config", str(cfg),
                       "--apply", "--from", "Kalpith", "--to", "Kalpit", "--action", "discard"])
            self.assertFalse(lex.exists())
            self.assertEqual(json.loads(cfg.read_text())["replacements"], {})
            self.assertEqual(len(log.read_text().strip().splitlines()), 1)  # blr remains


if __name__ == "__main__":
    unittest.main()
