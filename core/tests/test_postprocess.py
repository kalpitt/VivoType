"""Tests for core/postprocess.py against the docs/indic-nlp.md spec."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from core.postprocess import (
    apply_replacements,
    config_mtime,
    convert_currency,
    load_config,
    postprocess,
    remove_fillers,
)


class CurrencyTests(unittest.TestCase):
    def test_integer_k(self):
        self.assertEqual(convert_currency("$10k"), "₹10 lakh")

    def test_decimal_k(self):
        self.assertEqual(convert_currency("$1.5k"), "₹1.5 lakh")

    def test_million(self):
        self.assertEqual(convert_currency("$1M"), "₹1 crore")

    def test_decimal_million_normalizes_to_int(self):
        self.assertEqual(convert_currency("$2.0M"), "₹2 crore")

    def test_in_sentence(self):
        self.assertEqual(
            convert_currency("My CTC is $10k now."), "My CTC is ₹10 lakh now."
        )

    def test_not_inside_url(self):
        text = "see https://x.com/$10k here"
        self.assertEqual(convert_currency(text), text)

    def test_not_inside_code_span(self):
        text = "run `price=$5k` ok"
        self.assertEqual(convert_currency(text), text)

    def test_not_inside_double_quotes(self):
        text = 'he said "$2k" loudly'
        self.assertEqual(convert_currency(text), text)

    def test_contraction_is_not_treated_as_quote(self):
        # Apostrophes must not form a bogus quoted span that hides the amount.
        self.assertEqual(convert_currency("it's $5k now"), "it's ₹5 lakh now")

    def test_kg_suffix_not_matched(self):
        self.assertEqual(convert_currency("$10kg"), "$10kg")


class FillerAndDictionaryTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()  # bundled defaults

    def test_fillers_removed_and_blr_replaced(self):
        out = postprocess("Um, I am, uh, moving to blr next month.", self.cfg)
        self.assertEqual(out, "I am, moving to Bengaluru next month.")

    def test_dictionary_100k(self):
        self.assertEqual(postprocess("100k users joined.", self.cfg), "1 lakh users joined.")

    def test_replacement_is_case_insensitive(self):
        self.assertEqual(apply_replacements("BLR rocks", {"blr": "Bengaluru"}), "Bengaluru rocks")

    def test_replacement_respects_word_boundaries(self):
        self.assertEqual(apply_replacements("blrx", {"blr": "Bengaluru"}), "blrx")

    def test_remove_fillers_noop_when_empty(self):
        self.assertEqual(remove_fillers("hello world", []), "hello world")

    def test_replacement_value_with_backslash_is_literal(self):
        # A dictionary value containing a regex backreference token (e.g. "\1")
        # must be inserted literally, not interpreted as a group reference. Before
        # the fix this raised re.error and broke the whole post-processing run.
        self.assertEqual(
            apply_replacements("say foo", {"foo": r"\1bar"}), r"say \1bar"
        )

    def test_replacement_value_with_backslash_g_is_literal(self):
        self.assertEqual(
            apply_replacements("path cs", {"cs": r"C:\Go"}), r"path C:\Go"
        )

    def test_replacements_do_not_chain(self):
        # One rule's output must NOT be re-matched by another rule (single pass),
        # so results don't depend on dict order.
        self.assertEqual(
            apply_replacements("ml", {"ml": "machine learning", "machine": "device"}),
            "machine learning",
        )

    def test_longest_source_wins_on_overlap(self):
        # A more specific (longer) source must take precedence over a shorter one.
        self.assertEqual(
            apply_replacements("new york", {"york": "Y", "new york": "NYC"}), "NYC"
        )


class LoadConfigTests(unittest.TestCase):
    def test_defaults_present(self):
        cfg = load_config()
        self.assertIn("um", cfg["fillers"])
        self.assertEqual(cfg["replacements"].get("blr"), "Bengaluru")

    def test_missing_path_falls_back_to_defaults(self):
        cfg = load_config(Path("/no/such/config.json"))
        self.assertIn("fillers", cfg)
        self.assertIn("replacements", cfg)

    def test_corrupt_base_config_falls_back_to_defaults(self):
        # A corrupt base/--config file must NOT crash: load_config feeds the CLI
        # and the daemon at boot, so a raised JSONDecodeError would break all
        # dictation. It should fall back to defaults like a missing file does.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cfg.json"
            p.write_text("{ not valid json", encoding="utf-8")
            cfg = load_config(p, user_path=Path(d) / "none.json")
            self.assertIn("um", cfg["fillers"])
            self.assertEqual(cfg["replacements"].get("blr"), "Bengaluru")

    def test_corrupt_base_config_warns_on_stderr(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cfg.json"
            p.write_text("{ not valid json", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                load_config(p, user_path=Path(d) / "none.json")
            self.assertIn("config", err.getvalue().lower())
            self.assertIn(str(p), err.getvalue())

    def test_corrupt_overlay_warns_on_stderr(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "base.json"
            base.write_text(json.dumps({"fillers": ["um"], "replacements": {}}))
            overlay = Path(d) / "user.json"
            overlay.write_text("{ broken overlay", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                cfg = load_config(base, user_path=overlay)
            self.assertIn("um", cfg["fillers"])  # base still loads
            self.assertIn("overlay", err.getvalue().lower())

    def test_custom_config_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cfg.json"
            p.write_text(json.dumps({"fillers": ["foo"], "replacements": {"bar": "BAZ"}}), encoding="utf-8")
            cfg = load_config(p)
            self.assertEqual(cfg["fillers"], ["foo"])
            self.assertEqual(postprocess("foo bar foo", cfg), "BAZ")

    def test_user_overlay_merges_over_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "base.json"
            base.write_text(json.dumps({"fillers": ["um"], "replacements": {"blr": "Bengaluru"}}))
            overlay = Path(d) / "user.json"
            overlay.write_text(json.dumps({"fillers": ["erm"], "replacements": {"kalpith": "Kalpit"}}))
            cfg = load_config(base, user_path=overlay)
            self.assertIn("um", cfg["fillers"])      # from base
            self.assertIn("erm", cfg["fillers"])     # from overlay
            self.assertEqual(cfg["replacements"]["blr"], "Bengaluru")   # base
            self.assertEqual(cfg["replacements"]["kalpith"], "Kalpit")  # overlay


class ConfigMtimeTests(unittest.TestCase):
    def test_zero_when_no_files(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(
                config_mtime(Path(d) / "none.json", user_path=Path(d) / "none2.json"), 0
            )

    def test_reflects_latest_file_and_changes_on_overlay_write(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "base.json"
            overlay = Path(d) / "user.json"
            base.write_text("{}", encoding="utf-8")
            m1 = config_mtime(base, user_path=overlay)
            self.assertGreater(m1, 0)  # base exists
            # Writing the overlay with a newer mtime must move the reported value.
            import os
            overlay.write_text("{}", encoding="utf-8")
            os.utime(overlay, (m1 + 10, m1 + 10))
            m2 = config_mtime(base, user_path=overlay)
            self.assertEqual(m2, m1 + 10)


if __name__ == "__main__":
    unittest.main()
