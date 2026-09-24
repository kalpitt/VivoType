"""Tests for core/promote.py — promoting captured corrections into the rules."""

import io
import json
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout
from pathlib import Path

from core import promote
from core.postprocess import postprocess

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

    def test_lexicon_only_when_the_matcher_would_apply_it(self):
        # Each of these was filed as a name, reported promoted, and never fired:
        # a lowercase source (the matcher only snaps Capitalised words) or a
        # distance the matcher's own threshold rejects. A dictionary rule fires.
        for frm, to in [("kalpith", "Kalpit"), ("Shrivas", "Shrinivas")]:
            self.assertEqual(promote.classify(frm, to, NO_ENGLISH), "dictionary", frm)
        self.assertEqual(promote.classify("Kalpith", "Kalpit", NO_ENGLISH), "lexicon")


class PromoteTargetTests(unittest.TestCase):
    def test_promote_to_lexicon_adds_and_dedupes(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "contacts.json"
            promote.promote_to_lexicon("Zubin", path)
            promote.promote_to_lexicon("Zubin", path)  # dedupe
            promote.promote_to_lexicon("Aarav", path)
            data = json.loads(path.read_text())
            self.assertEqual(data["names"], ["Aarav", "Zubin"])  # sorted, deduped
            self.assertEqual(data["learned"], ["Aarav", "Zubin"])

    def test_promote_to_lexicon_reports_what_it_added(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "contacts.json"
            path.write_text(json.dumps({"names": ["Kalpit"]}))
            self.assertEqual(promote.promote_to_lexicon("Kalpit", path), (False, True))
            self.assertEqual(promote.promote_to_lexicon("Zubin", path), (True, True))
            self.assertEqual(promote.promote_to_lexicon("Zubin", path), (False, False))

    def test_promote_to_dictionary_returns_previous_value(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cfg.json"
            path.write_text(json.dumps({"replacements": {"blr": "Bangalore"}}))
            self.assertEqual(promote.promote_to_dictionary("BLR", "Bengaluru", path), "Bangalore")
            self.assertIsNone(promote.promote_to_dictionary("ty", "thank you", path))

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

    def test_apply_allow_unlogged_promotes_without_log_row(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "corrections.jsonl"
            lex = Path(d) / "contacts.json"
            cfg = Path(d) / "cfg.json"
            cfg.write_text(json.dumps({"replacements": {}}))
            _, out = self._run([
                "--log", str(log), "--lexicon", str(lex), "--config", str(cfg),
                "--apply", "--from", "Kalpith", "--to", "Kalpit",
                "--action", "promote", "--allow-unlogged",
            ])
            result = json.loads(out)
            self.assertTrue(result["ok"])
            self.assertEqual(result["target"], "lexicon")
            data = json.loads(lex.read_text())
            self.assertIn("Kalpit", data["names"])
            self.assertIn("Kalpit", data["learned"])
            self.assertFalse(log.exists())

    def test_allow_unlogged_without_flag_stays_not_found(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "corrections.jsonl"
            lex = Path(d) / "contacts.json"
            cfg = Path(d) / "cfg.json"
            cfg.write_text(json.dumps({"replacements": {}}))
            result = promote.apply_one(
                "promote", "Kalpith", "Kalpit", log, lex, cfg,
                english=NO_ENGLISH, allow_unlogged=False,
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "not_found")

    def test_allow_unlogged_dictionary_rule_applies_in_postprocess(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "corrections.jsonl"
            lex = Path(d) / "contacts.json"
            cfg = Path(d) / "cfg.json"
            cfg.write_text(json.dumps({"replacements": {}}))
            result = promote.apply_one(
                "promote", "BLR", "Bengaluru", log, lex, cfg,
                english=NO_ENGLISH, allow_unlogged=True,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["target"], "dictionary")
            overlay = json.loads(cfg.read_text())
            self.assertEqual(
                postprocess("fly to BLR tomorrow", overlay),
                "fly to Bengaluru tomorrow",
            )

    def _tick_then_undo(self, d, frm, to):
        """Promote via the CLI (as the tick will), then Undo with its token."""
        log = Path(d) / "corrections.jsonl"
        lex = Path(d) / "contacts.json"
        cfg = Path(d) / "cfg.json"
        base = ["--log", str(log), "--lexicon", str(lex), "--config", str(cfg),
                "--apply", "--from", frm, "--to", to]
        _, out = self._run(base + ["--action", "promote", "--allow-unlogged"])
        tick = json.loads(out)
        self.assertTrue(tick["ok"])
        _, out = self._run(base + ["--action", "remove", "--undo", json.dumps(tick["undo"])])
        return tick, json.loads(out)

    def test_undo_new_dictionary_rule_drops_key(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "cfg.json"
            cfg.write_text(json.dumps({"replacements": {"foo": "bar"}}))
            _, undo = self._tick_then_undo(d, "blr", "Bengaluru")
            self.assertEqual(undo["status"], "reverted")
            self.assertEqual(json.loads(cfg.read_text())["replacements"], {"foo": "bar"})

    def test_undo_restores_overwritten_dictionary_rule(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "cfg.json"
            cfg.write_text(json.dumps({"replacements": {"blr": "Bangalore"}}))
            tick, undo = self._tick_then_undo(d, "BLR", "Bengaluru")
            self.assertEqual(tick["undo"]["previous"], "Bangalore")
            self.assertTrue(undo["ok"])
            self.assertEqual(json.loads(cfg.read_text())["replacements"], {"blr": "Bangalore"})

    def test_undo_leaves_dictionary_rule_changed_since(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "corrections.jsonl"
            lex = Path(d) / "contacts.json"
            cfg = Path(d) / "cfg.json"
            cfg.write_text(json.dumps({"replacements": {}}))
            tick = promote.apply_one("promote", "blr", "Bengaluru", log, lex, cfg,
                                     english=NO_ENGLISH, allow_unlogged=True)
            cfg.write_text(json.dumps({"replacements": {"blr": "Bangalore"}}))
            undo = promote.apply_one("remove", "blr", "Bengaluru", log, lex, cfg,
                                     english=NO_ENGLISH, undo=tick["undo"])
            self.assertEqual(undo["status"], "changed_since")
            self.assertEqual(json.loads(cfg.read_text())["replacements"], {"blr": "Bangalore"})

    def test_undo_new_lexicon_name_removes_it(self):
        with tempfile.TemporaryDirectory() as d:
            lex = Path(d) / "contacts.json"
            lex.write_text(json.dumps({"names": ["Zubin"]}))
            (Path(d) / "cfg.json").write_text(json.dumps({"replacements": {}}))
            _, undo = self._tick_then_undo(d, "Kalpith", "Kalpit")
            self.assertEqual(undo["status"], "reverted")
            data = json.loads(lex.read_text())
            self.assertEqual(data["names"], ["Zubin"])
            self.assertEqual(data["learned"], [])

    def test_undo_keeps_name_that_was_already_a_contact(self):
        with tempfile.TemporaryDirectory() as d:
            lex = Path(d) / "contacts.json"
            lex.write_text(json.dumps({"names": ["Kalpit", "Zubin"]}))
            (Path(d) / "cfg.json").write_text(json.dumps({"replacements": {}}))
            tick, undo = self._tick_then_undo(d, "Kalpith", "Kalpit")
            self.assertFalse(tick["undo"]["added_name"])
            self.assertTrue(undo["ok"])
            data = json.loads(lex.read_text())
            self.assertEqual(data["names"], ["Kalpit", "Zubin"])
            self.assertEqual(data["learned"], [])

    def test_remove_without_undo_token_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "corrections.jsonl"
            lex = Path(d) / "contacts.json"
            cfg = Path(d) / "cfg.json"
            lex.write_text(json.dumps({"names": ["Kalpit"]}))
            cfg.write_text(json.dumps({"replacements": {"blr": "Bengaluru"}}))
            _, out = self._run([
                "--log", str(log), "--lexicon", str(lex), "--config", str(cfg),
                "--apply", "--from", "blr", "--to", "Bengaluru", "--action", "remove",
            ])
            self.assertEqual(json.loads(out)["status"], "undo_required")
            self.assertEqual(json.loads(cfg.read_text())["replacements"], {"blr": "Bengaluru"})
            self.assertEqual(json.loads(lex.read_text())["names"], ["Kalpit"])

    def test_remove_with_token_for_another_pair_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "corrections.jsonl"
            lex = Path(d) / "contacts.json"
            cfg = Path(d) / "cfg.json"
            cfg.write_text(json.dumps({"replacements": {"blr": "Bengaluru"}}))
            token = {"target": "dictionary", "key": "ty", "to": "thank you", "previous": None}
            result = promote.apply_one("remove", "blr", "Bengaluru", log, lex, cfg,
                                       english=NO_ENGLISH, undo=token)
            self.assertEqual(result["status"], "undo_mismatch")
            self.assertEqual(json.loads(cfg.read_text())["replacements"], {"blr": "Bengaluru"})

    def _paths(self, d):
        return (Path(d) / "corrections.jsonl", Path(d) / "contacts.json", Path(d) / "cfg.json")

    def test_undo_lexicon_token_for_another_name_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            lex.write_text(json.dumps({"names": ["Kalpit", "Zubin"], "learned": ["Zubin"]}))
            token = {"target": "lexicon", "name": "Zubin",
                     "added_name": True, "added_learned": True}
            result = promote.apply_one("remove", "Kalpith", "Kalpit", log, lex, cfg,
                                       english=NO_ENGLISH, undo=token)
            self.assertEqual(result["status"], "undo_mismatch")
            self.assertEqual(json.loads(lex.read_text())["names"], ["Kalpit", "Zubin"])

    def test_undo_of_no_op_tick_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            promote.promote_to_lexicon("Kalpit", lex)
            tick = promote.apply_one("promote", "Kalpith", "Kalpit", log, lex, cfg,
                                     english=NO_ENGLISH, allow_unlogged=True)
            self.assertEqual((tick["undo"]["added_name"], tick["undo"]["added_learned"]),
                             (False, False))
            before = lex.read_bytes()
            lex.touch()
            mtime = lex.stat().st_mtime_ns
            undo = promote.apply_one("remove", "Kalpith", "Kalpit", log, lex, cfg,
                                     english=NO_ENGLISH, undo=tick["undo"])
            self.assertEqual(undo["status"], "nothing_to_undo")
            self.assertEqual(lex.read_bytes(), before)
            self.assertEqual(lex.stat().st_mtime_ns, mtime)

    def test_undo_is_case_insensitive_and_keeps_existing_case_variant(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            lex.write_text(json.dumps({"names": ["KALPIT"]}))
            tick = promote.apply_one("promote", "Kalpith", "Kalpit", log, lex, cfg,
                                     english=NO_ENGLISH, allow_unlogged=True)
            self.assertFalse(tick["undo"]["added_name"])
            undo = promote.apply_one("remove", "kalpith", "kalpit", log, lex, cfg,
                                     english=NO_ENGLISH, undo=tick["undo"])
            self.assertTrue(undo["ok"])
            data = json.loads(lex.read_text())
            self.assertEqual(data["names"], ["KALPIT"])
            self.assertEqual(data["learned"], [])

    def test_replayed_lexicon_token_keeps_name_added_back_since(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            tick = promote.apply_one("promote", "Marc", "Mark", log, lex, cfg,
                                     english=NO_ENGLISH, allow_unlogged=True)
            promote.apply_one("remove", "Marc", "Mark", log, lex, cfg,
                              english=NO_ENGLISH, undo=tick["undo"])
            data = json.loads(lex.read_text())
            data["names"].append("Mark")  # e.g. a Contacts restore
            lex.write_text(json.dumps(data))
            replay = promote.apply_one("remove", "Marc", "Mark", log, lex, cfg,
                                       english=NO_ENGLISH, undo=tick["undo"])
            self.assertEqual(replay["status"], "nothing_to_undo")
            self.assertEqual(json.loads(lex.read_text())["names"], ["Mark"])

    def test_undo_restores_consumed_log_rows_once(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            rows = [{"from": "Kalpith", "to": "Kalpit", "at": f"2026-09-2{i}T10:00:00"}
                    for i in range(3)]
            other = {"from": "blr", "to": "Bengaluru", "at": "2026-09-20T09:00:00"}
            log.write_text("".join(json.dumps(r) + "\n" for r in rows + [other]))
            tick = promote.apply_one("promote", "Kalpith", "Kalpit", log, lex, cfg,
                                     english=NO_ENGLISH, allow_unlogged=True)
            self.assertEqual(tick["removed"], 3)
            self.assertEqual(promote.load_corrections(log), [other])
            undo = promote.apply_one("remove", "Kalpith", "Kalpit", log, lex, cfg,
                                     english=NO_ENGLISH, undo=tick["undo"])
            self.assertEqual(undo["restored"], 3)
            self.assertEqual(sorted(promote.load_corrections(log), key=str),
                             sorted(rows + [other], key=str))
            again = promote.apply_one("remove", "Kalpith", "Kalpit", log, lex, cfg,
                                      english=NO_ENGLISH, undo=tick["undo"])
            self.assertEqual(again["restored"], 0)
            self.assertEqual(len(promote.load_corrections(log)), 4)

    def test_undo_changed_since_is_not_ok_and_restores_no_rows(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            cfg.write_text(json.dumps({"replacements": {}}))
            log.write_text(json.dumps({"from": "blr", "to": "Bengaluru"}) + "\n")
            tick = promote.apply_one("promote", "blr", "Bengaluru", log, lex, cfg,
                                     english=NO_ENGLISH)
            cfg.write_text(json.dumps({"replacements": {"blr": "Bangalore"}}))
            undo = promote.apply_one("remove", "blr", "Bengaluru", log, lex, cfg,
                                     english=NO_ENGLISH, undo=tick["undo"])
            self.assertFalse(undo["ok"])
            self.assertEqual(undo["status"], "changed_since")
            self.assertEqual(promote.load_corrections(log), [])

    def test_malformed_undo_tokens_change_nothing(self):
        lexicon_ok = {"target": "lexicon", "name": "Mark",
                      "added_name": True, "added_learned": True}
        dict_ok = {"target": "dictionary", "key": "teh", "to": "the", "previous": None}
        bad = [
            ("Marc", "Mark", {**lexicon_ok, "added_name": "false"}),
            ("Marc", "Mark", {**lexicon_ok, "name": 7}),
            ("Marc", "Mark", {k: v for k, v in lexicon_ok.items() if k != "added_learned"}),
            ("Marc", "Mark", {**lexicon_ok, "rows": "x"}),
            ("teh", "the", {**dict_ok, "previous": {"evil": 1}}),
            ("teh", "the", {k: v for k, v in dict_ok.items() if k != "previous"}),
            ("teh", "the", {**dict_ok, "target": "other"}),
        ]
        for frm, to, token in bad:
            with tempfile.TemporaryDirectory() as d:
                log, lex, cfg = self._paths(d)
                lex.write_text(json.dumps({"names": ["Mark"], "learned": ["Mark"]}))
                cfg.write_text(json.dumps({"replacements": {"teh": "the"}}))
                result = promote.apply_one("remove", frm, to, log, lex, cfg,
                                           english=NO_ENGLISH, undo=token)
                self.assertEqual(result["status"], "undo_invalid", token)
                self.assertEqual(json.loads(lex.read_text())["names"], ["Mark"])
                self.assertEqual(json.loads(cfg.read_text())["replacements"], {"teh": "the"})

    def test_undo_refuses_wrong_shaped_files(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            lex.write_text(json.dumps({"names": "Kalpit, Zubin", "learned": ["Kalpit"]}))
            cfg.write_text(json.dumps({"replacements": []}))
            lex_token = {"target": "lexicon", "name": "Kalpit",
                         "added_name": True, "added_learned": True}
            dict_token = {"target": "dictionary", "key": "blr", "to": "Bengaluru",
                          "previous": None}
            for frm, to, token, path in [("Kalpith", "Kalpit", lex_token, lex),
                                         ("blr", "Bengaluru", dict_token, cfg)]:
                before = path.read_bytes()
                result = promote.apply_one("remove", frm, to, log, lex, cfg,
                                           english=NO_ENGLISH, undo=token)
                self.assertEqual(result["status"], "write_failed")
                self.assertEqual(path.read_bytes(), before)
            cfg.write_text("{oops")
            result = promote.apply_one("remove", "blr", "Bengaluru", log, lex, cfg,
                                       english=NO_ENGLISH, undo=dict_token)
            self.assertEqual(result["status"], "write_failed")

    def test_promote_refuses_wrong_shaped_files(self):
        for lex_body, cfg_body, frm, to in [
            ({"names": "Kalpit"}, {"replacements": {}}, "Kalpith", "Kalpit"),
            ({"names": [1]}, {"replacements": {}}, "Kalpith", "Kalpit"),
            ({"names": []}, {"replacements": []}, "blr", "Bengaluru"),
        ]:
            with tempfile.TemporaryDirectory() as d:
                log, lex, cfg = self._paths(d)
                lex.write_text(json.dumps(lex_body))
                cfg.write_text(json.dumps(cfg_body))
                before = (lex.read_bytes(), cfg.read_bytes())
                result = promote.apply_one("promote", frm, to, log, lex, cfg,
                                           english=NO_ENGLISH, allow_unlogged=True)
                self.assertEqual(result["status"], "write_failed", lex_body)
                self.assertEqual((lex.read_bytes(), cfg.read_bytes()), before)

    def test_non_object_log_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            cfg.write_text(json.dumps({"replacements": {}}))
            row = {"from": "blr", "to": "Bengaluru"}
            log.write_text("[1]\n\"text\"\n" + json.dumps(row) + "\n")
            self.assertEqual(promote.load_corrections(log), [row])
            result = promote.apply_one("promote", "blr", "Bengaluru", log, lex, cfg,
                                       english=NO_ENGLISH)
            self.assertTrue(result["ok"])

    def test_undo_with_missing_files_creates_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            token = {"target": "lexicon", "name": "Kalpit",
                     "added_name": True, "added_learned": True}
            result = promote.apply_one("remove", "Kalpith", "Kalpit", log, lex, cfg,
                                       english=NO_ENGLISH, undo=token)
            self.assertEqual(result["status"], "nothing_to_undo")
            self.assertFalse(lex.exists())
            self.assertFalse(log.exists())

    def test_cli_rejects_bad_undo_json(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            cfg.write_text(json.dumps({"replacements": {"blr": "Bengaluru"}}))
            base = ["--log", str(log), "--lexicon", str(lex), "--config", str(cfg),
                    "--apply", "--from", "blr", "--to", "Bengaluru", "--action", "remove"]
            for raw in ["{not json", "[" * 200000]:
                err = io.StringIO()
                with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", err):
                    rc = promote.main(base + ["--undo", raw])
                self.assertEqual(rc, 2)
                self.assertIn("--undo must be JSON", err.getvalue())
            self.assertEqual(json.loads(cfg.read_text())["replacements"], {"blr": "Bengaluru"})

    def test_bulk_yes_skips_symbol_target(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            cfg.write_text(json.dumps({"replacements": {}}))
            row = json.dumps({"from": "and", "to": "&"}) + "\n"
            log.write_text(row * 3)
            self._run(["--log", str(log), "--lexicon", str(lex), "--config", str(cfg), "--yes"])
            self.assertEqual(json.loads(cfg.read_text())["replacements"], {})
            self.assertEqual(len(promote.load_corrections(log)), 3)

    def test_list_json_flags_refused_rows(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            log.write_text(json.dumps({"from": "and", "to": "&"}) + "\n"
                           + json.dumps({"from": "kama", "to": "comma"}) + "\n"
                           + json.dumps({"from": "blr", "to": "Bengaluru"}) + "\n")
            _, out = self._run(["--log", str(log), "--lexicon", str(lex),
                                "--config", str(cfg), "--list-json"])
            flags = {row["from"]: row["refused"] for row in json.loads(out)}
            self.assertEqual(flags, {"and": True, "kama": False, "blr": False})

    def test_promote_accepts_punctuation_words(self):
        # Spoken words are words: "period", "comma", lowercase "mark" promote
        # like any other correction.
        for frm, to in [("perid", "period"), ("period", "perid"),
                        ("comma Raul", "Rahul"), ("mark", "Marc")]:
            with tempfile.TemporaryDirectory() as d:
                log, lex, cfg = self._paths(d)
                cfg.write_text(json.dumps({"replacements": {}}))
                result = promote.apply_one("promote", frm, to, log, lex, cfg,
                                           english=NO_ENGLISH, allow_unlogged=True)
                self.assertTrue(result["ok"], (frm, result))

    def test_promote_refuses_symbol_source(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            cfg.write_text(json.dumps({"replacements": {}}))
            result = promote.apply_one("promote", "&", "and", log, lex, cfg,
                                       english=NO_ENGLISH, allow_unlogged=True)
            self.assertEqual(result["status"], "refused_punctuation")
            self.assertEqual(json.loads(cfg.read_text())["replacements"], {})

    def test_apply_promote_allows_name_that_doubles_as_punctuation(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "corrections.jsonl"
            lex = Path(d) / "contacts.json"
            cfg = Path(d) / "cfg.json"
            cfg.write_text(json.dumps({"replacements": {}}))
            result = promote.apply_one("promote", "Marc", "Mark", log, lex, cfg,
                                       english=NO_ENGLISH, allow_unlogged=True)
            self.assertTrue(result["ok"])
            self.assertIn("Mark", json.loads(lex.read_text())["names"])

    def test_apply_promote_refuses_punctuation_target(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "corrections.jsonl"
            lex = Path(d) / "contacts.json"
            cfg = Path(d) / "cfg.json"
            cfg.write_text(json.dumps({"replacements": {}}))
            log.write_text(json.dumps({"from": "comma", "to": ","}) + "\n")
            _, out = self._run([
                "--log", str(log), "--lexicon", str(lex), "--config", str(cfg),
                "--apply", "--from", "comma", "--to", ",", "--action", "promote",
            ])
            result = json.loads(out)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "refused_punctuation")



class RuleDeletionTests(unittest.TestCase):
    """Review window: list and delete active rules without an undo token."""

    def _paths(self, d):
        return (Path(d) / "corrections.jsonl", Path(d) / "contacts.json", Path(d) / "cfg.json")

    def test_list_rules_shows_replacements_and_learned_names(self):
        with tempfile.TemporaryDirectory() as d:
            _, lex, cfg = self._paths(d)
            cfg.write_text(json.dumps({"replacements": {"tool": "app", "blr": "Bengaluru"}}))
            lex.write_text(json.dumps({"names": ["Asha", "Kalpit"], "learned": ["Kalpit"]}))
            rules = promote.list_rules(lex, cfg)
            self.assertEqual(rules["rules"], [{"from": "blr", "to": "Bengaluru"},
                                              {"from": "tool", "to": "app"}])
            self.assertEqual(rules["names"], ["Kalpit"])

    def test_delete_rule_removes_only_that_rule(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            cfg.write_text(json.dumps({"replacements": {"tool": "app", "blr": "Bengaluru"}}))
            result = promote.apply_one("delete-rule", "tool", "app", log, lex, cfg,
                                       english=NO_ENGLISH)
            self.assertTrue(result["ok"])
            self.assertEqual(json.loads(cfg.read_text())["replacements"], {"blr": "Bengaluru"})

    def test_delete_rule_refuses_a_rule_that_changed(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            cfg.write_text(json.dumps({"replacements": {"tool": "tools"}}))
            result = promote.apply_one("delete-rule", "tool", "app", log, lex, cfg,
                                       english=NO_ENGLISH)
            self.assertEqual(result["status"], "changed_since")
            self.assertEqual(json.loads(cfg.read_text())["replacements"], {"tool": "tools"})
            result = promote.apply_one("delete-rule", "gone", "x", log, lex, cfg,
                                       english=NO_ENGLISH)
            self.assertEqual(result["status"], "not_found")

    def test_delete_learned_name_added_by_learning(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            lex.write_text(json.dumps({"names": ["Asha"]}))
            promote.promote_to_lexicon("Zubin", lex)
            result = promote.apply_one("delete-name", "Zubin", "Zubin", log, lex, cfg,
                                       english=NO_ENGLISH)
            self.assertTrue(result["ok"])
            self.assertTrue(result["removed_from_names"])
            data = json.loads(lex.read_text())
            self.assertEqual(data["names"], ["Asha"])
            self.assertEqual(data["learned"], [])

    def test_delete_learned_name_keeps_an_existing_contact(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            lex.write_text(json.dumps({"names": ["Asha", "Kalpit"]}))
            promote.promote_to_lexicon("Kalpit", lex)  # already a contact
            result = promote.apply_one("delete-name", "Kalpit", "Kalpit", log, lex, cfg,
                                       english=NO_ENGLISH)
            self.assertTrue(result["ok"])
            self.assertFalse(result["removed_from_names"])
            data = json.loads(lex.read_text())
            self.assertEqual(data["names"], ["Asha", "Kalpit"])
            self.assertEqual(data["learned"], [])

    def test_delete_legacy_learned_name_keeps_it_in_names(self):
        # Learned before learned_added existed: can't tell it from a contact.
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            lex.write_text(json.dumps({"names": ["Asha", "Kalpit"], "learned": ["Kalpit"]}))
            result = promote.apply_one("delete-name", "Kalpit", "Kalpit", log, lex, cfg,
                                       english=NO_ENGLISH)
            self.assertTrue(result["ok"])
            self.assertFalse(result["removed_from_names"])
            self.assertEqual(json.loads(lex.read_text())["names"], ["Asha", "Kalpit"])

    def test_delete_hand_edited_rule_with_capital_key(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            cfg.write_text(json.dumps({"replacements": {"Foo": "bar", "tool": "app"}}))
            result = promote.apply_one("delete-rule", "Foo", "bar", log, lex, cfg,
                                       english=NO_ENGLISH)
            self.assertTrue(result["ok"])
            self.assertEqual(json.loads(cfg.read_text())["replacements"], {"tool": "app"})

    def test_undo_clears_learned_added(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            lex.write_text(json.dumps({"names": []}))
            result = promote.apply_one("promote", "Kalpith", "Kalpit", log, lex, cfg,
                                       english=NO_ENGLISH, allow_unlogged=True)
            self.assertEqual(result["target"], "lexicon")
            promote.apply_one("remove", "Kalpith", "Kalpit", log, lex, cfg,
                              english=NO_ENGLISH, undo=result["undo"])
            data = json.loads(lex.read_text())
            self.assertEqual((data["names"], data["learned"], data.get("learned_added")),
                             ([], [], []))

    def test_cli_list_rules_json(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            cfg.write_text(json.dumps({"replacements": {"tool": "app"}}))
            out = io.StringIO()
            with redirect_stdout(out):
                rc = promote.main(["--log", str(log), "--lexicon", str(lex),
                                   "--config", str(cfg), "--list-rules-json"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out.getvalue()),
                             {"rules": [{"from": "tool", "to": "app"}], "names": []})



class StdinJsonTests(unittest.TestCase):
    """--apply --stdin-json: the words and undo token never touch argv."""

    def _paths(self, d):
        return (Path(d) / "corrections.jsonl", Path(d) / "contacts.json", Path(d) / "cfg.json")

    def _run(self, argv, stdin):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch("sys.stdin", io.StringIO(stdin)):
            rc = promote.main(argv)
        return rc, out.getvalue()

    def test_promote_then_undo_over_stdin(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            cfg.write_text(json.dumps({"replacements": {}}))
            base = ["--log", str(log), "--lexicon", str(lex), "--config", str(cfg),
                    "--apply", "--stdin-json"]
            rc, out = self._run(base + ["--action", "promote", "--allow-unlogged"],
                                json.dumps({"from": "blr", "to": "Bengaluru"}))
            self.assertEqual(rc, 0)
            result = json.loads(out)
            self.assertTrue(result["ok"])
            self.assertEqual(json.loads(cfg.read_text())["replacements"], {"blr": "Bengaluru"})
            rc, out = self._run(base + ["--action", "remove"],
                                json.dumps({"from": "blr", "to": "Bengaluru",
                                            "undo": result["undo"]}))
            self.assertTrue(json.loads(out)["ok"])
            self.assertEqual(json.loads(cfg.read_text())["replacements"], {})

    def test_word_starting_with_dash_is_a_value(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            cfg.write_text(json.dumps({"replacements": {"-foo": "bar"}}))
            rc, out = self._run(["--log", str(log), "--lexicon", str(lex), "--config", str(cfg),
                                 "--apply", "--stdin-json", "--action", "delete-rule"],
                                json.dumps({"from": "-foo", "to": "bar"}))
            self.assertTrue(json.loads(out)["ok"])

    def test_bad_stdin_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            log, lex, cfg = self._paths(d)
            for bad in ["not json", "[]", json.dumps({"from": "a"}), json.dumps({"from": 1, "to": "b"})]:
                err = io.StringIO()
                with mock.patch("sys.stderr", err):
                    rc, _ = self._run(["--log", str(log), "--lexicon", str(lex), "--config", str(cfg),
                                       "--apply", "--stdin-json", "--action", "promote"], bad)
                self.assertEqual(rc, 2, bad)
                self.assertIn("--stdin-json", err.getvalue())


if __name__ == "__main__":
    unittest.main()
