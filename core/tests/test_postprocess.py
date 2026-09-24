"""Tests for core/postprocess.py against the docs/indic-nlp.md spec."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.postprocess import (
    apply_replacements,
    collapse_repetitions,
    config_mtime,
    convert_currency,
    load_config,
    postprocess,
    remove_fillers,
    resolve_profile,
)
from core import namematch
from core import postprocess as pp

# Never read the developer's real personal data (core/data/user_dictionary.json
# overlay, core/data/lexicon/contacts.json): point both at synthetic temp
# files for the whole module, so results can't depend on whose checkout runs.
_TMP = None
_PATCHES = []


def setUpModule():
    global _TMP
    _TMP = tempfile.TemporaryDirectory()
    lexicon = Path(_TMP.name) / "contacts.json"
    lexicon.write_text(json.dumps({"names": ["Kalpit", "Shrivastava"]}), encoding="utf-8")
    _PATCHES[:] = [
        mock.patch.object(pp, "USER_DICT_PATH", Path(_TMP.name) / "no-overlay.json"),
        mock.patch.object(namematch, "LEXICON_PATH", lexicon),
        # Force a reload now, and restore the cache afterwards so later test
        # modules never see the synthetic names.
        mock.patch.object(namematch, "_last_lexicon_mtime", -1),
        mock.patch.object(namematch, "_names_by_len", None),
    ]
    for patcher in _PATCHES:
        patcher.start()


def tearDownModule():
    for patcher in reversed(_PATCHES):
        patcher.stop()
    _TMP.cleanup()


class CollapseRepetitionTests(unittest.TestCase):
    """Whisper repetition loops ("guilty guilty guilty …") must collapse to a
    single occurrence; deliberate doubles must survive."""

    def test_word_loop_collapses_to_one(self):
        self.assertEqual(
            collapse_repetitions("guilty guilty guilty guilty guilty"), "guilty")

    def test_triple_collapses(self):
        self.assertEqual(collapse_repetitions("guilty guilty guilty"), "guilty")

    def test_deliberate_double_survives(self):
        self.assertEqual(collapse_repetitions("very very good"), "very very good")

    def test_loop_inside_sentence(self):
        self.assertEqual(
            collapse_repetitions("I feel guilty guilty guilty guilty about it"),
            "I feel guilty about it")

    def test_case_insensitive_first_occurrence_kept(self):
        self.assertEqual(collapse_repetitions("Guilty guilty guilty"), "Guilty")

    def test_phrase_loop_with_punctuation(self):
        self.assertEqual(
            collapse_repetitions("thank you. thank you. thank you."),
            "thank you.")

    def test_comma_separated_loop(self):
        self.assertEqual(collapse_repetitions("yes, yes, yes, yes"), "yes")

    def test_devanagari_loop(self):
        self.assertEqual(collapse_repetitions("ठीक ठीक ठीक ठीक है"), "ठीक है")

    def test_normal_dictation_untouched(self):
        text = "Move the meeting to Monday and tell Priya about the budget."
        self.assertEqual(collapse_repetitions(text), text)

    def test_dictated_pin_digits_survive(self):
        # "1 1 1 1" is someone reading out a PIN/OTP, not a decoder loop.
        self.assertEqual(collapse_repetitions("my pin is 1 1 1 1"), "my pin is 1 1 1 1")

    def test_repeated_multidigit_numbers_survive(self):
        self.assertEqual(collapse_repetitions("codes 10 10 10 done"), "codes 10 10 10 done")

    def test_spelled_letters_survive(self):
        self.assertEqual(collapse_repetitions("the code is A A A B"), "the code is A A A B")

    def test_deliberate_word_triple_is_collapsed_by_design(self):
        # Documented trade-off: a real "no no no" loses its emphasis so that
        # hallucinated loops die. Pinned so a future change is deliberate.
        self.assertEqual(collapse_repetitions("no no no"), "no")

    def test_full_pipeline_collapses_loops(self):
        cfg = {"fillers": [], "replacements": {}}
        self.assertEqual(
            postprocess("The verdict was guilty guilty guilty guilty guilty.", cfg),
            "The verdict was guilty.")


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

    def test_100k_rolls_over_to_crore(self):
        # 100 lakh IS 1 crore; Indian usage never says "₹100 lakh" for salaries.
        self.assertEqual(convert_currency("$100k"), "₹1 crore")

    def test_250k_rolls_over_to_fractional_crore(self):
        self.assertEqual(convert_currency("$250k CTC"), "₹2.5 crore CTC")

    def test_99k_stays_in_lakh(self):
        self.assertEqual(convert_currency("$99k"), "₹99 lakh")

    def test_sub_crore_million_downshifts_to_lakh(self):
        # Nobody says "₹0.5 crore" — fractional-crore sums read in lakh.
        self.assertEqual(convert_currency("$0.5M"), "₹50 lakh")

    def test_quarter_million_downshifts_to_lakh(self):
        self.assertEqual(convert_currency("$0.25M deal"), "₹25 lakh deal")

    def test_whole_million_stays_in_crore(self):
        self.assertEqual(convert_currency("$1M"), "₹1 crore")

    def test_trailing_zero_decimal_normalized(self):
        # "$1.50k" must read "₹1.5 lakh", not "₹1.50 lakh".
        self.assertEqual(convert_currency("$1.50k"), "₹1.5 lakh")

    def test_trailing_zero_million_normalized(self):
        self.assertEqual(convert_currency("$2.10M"), "₹2.1 crore")

    def test_rs_prefix_becomes_symbol(self):
        self.assertEqual(convert_currency("Rs 500"), "₹500")

    def test_rs_dot_prefix_with_grouped_number(self):
        self.assertEqual(convert_currency("a Rs. 2,000 fine"), "a ₹2,000 fine")

    def test_inr_prefix_becomes_symbol(self):
        self.assertEqual(convert_currency("INR 1200"), "₹1200")

    def test_rupees_suffix_becomes_symbol(self):
        self.assertEqual(convert_currency("worth 500 rupees"), "worth ₹500")

    def test_rs_without_number_untouched(self):
        self.assertEqual(convert_currency("the rs value"), "the rs value")

    def test_rs_inside_quotes_untouched(self):
        text = 'she said "Rs 500" twice'
        self.assertEqual(convert_currency(text), text)


class FillerAndDictionaryTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()  # bundled defaults (overlay patched to a temp path)

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

    def test_filler_between_sentences_leaves_no_doubled_period(self):
        # "I went. Um. Then" must not normalize to "I went.. Then".
        out = postprocess("I went. Um. Then we left.", self.cfg)
        self.assertEqual(out, "I went. Then we left.")

    def test_filler_after_question_mark_leaves_single_mark(self):
        out = postprocess("Ready? Uh. Let us start.", self.cfg)
        self.assertEqual(out, "Ready? Let us start.")

    def test_genuine_ellipsis_preserved(self):
        # A real "..." has no spaces between the dots and must survive.
        self.assertEqual(postprocess("Wait... okay", self.cfg), "Wait... okay")

    def test_leading_filler_strip_restores_capital(self):
        # Stripping "Um, " must not leave the sentence starting lowercase.
        out = postprocess("Um, actually we should go.", self.cfg)
        self.assertEqual(out, "Actually we should go.")

    def test_leading_um_period_does_not_leave_orphan_dot(self):
        out = postprocess("Um. Then we left.", self.cfg)
        self.assertEqual(out, "Then we left.")

    def test_leading_uh_period_does_not_leave_orphan_dot(self):
        out = postprocess("Uh. Let us start.", self.cfg)
        self.assertEqual(out, "Let us start.")

    def test_multiple_leading_um_period_fillers(self):
        out = postprocess("Um. Um. Hello there.", self.cfg)
        self.assertEqual(out, "Hello there.")

    def test_leading_ellipsis_preserved(self):
        # Must not strip a genuine "..." — only a single orphan mark + space.
        self.assertEqual(postprocess("... okay then", self.cfg), "... okay then")

    def test_multiple_leading_fillers_restore_capital(self):
        out = postprocess("Uh, er, let us start.", self.cfg)
        self.assertEqual(out, "Let us start.")

    def test_mixed_case_first_word_not_capitalized(self):
        # "iPhone" is intentionally mixed-case; forcing "IPhone" would be worse.
        out = postprocess("Um, iPhone rocks.", self.cfg)
        self.assertEqual(out, "iPhone rocks.")

    def test_all_lowercase_dictation_left_alone(self):
        # No filler stripped, original started lowercase — don't invent a capital.
        self.assertEqual(postprocess("just a note", self.cfg), "just a note")

    def test_percent_after_number_tightened(self):
        out = postprocess("growth was 10 % this year", self.cfg)
        self.assertEqual(out, "growth was 10% this year")

    def test_percent_not_after_number_untouched(self):
        out = postprocess("use the % operator", self.cfg)
        self.assertEqual(out, "use the % operator")

    def test_rupee_symbol_before_number_tightened(self):
        self.assertEqual(postprocess("paid ₹ 500 cash", self.cfg), "paid ₹500 cash")

    def test_dollar_symbol_before_number_tightened(self):
        self.assertEqual(postprocess("about $ 500 total", self.cfg), "about $500 total")

    def test_no_space_before_danda(self):
        # danda (।) is the Devanagari full stop and follows the same spacing rules.
        self.assertEqual(postprocess("नमस्ते ।", self.cfg), "नमस्ते।")

    def test_filler_between_danda_sentences_leaves_single_danda(self):
        out = postprocess("अच्छा। um। चलो", self.cfg)
        self.assertEqual(out, "अच्छा। चलो")

    def test_spaced_danda_glued_to_next_word(self):
        out = postprocess("नमस्ते ।फिर मिलेंगे", self.cfg)
        self.assertEqual(out, "नमस्ते। फिर मिलेंगे")

    def test_space_inserted_after_danda(self):
        out = postprocess("नमस्ते।फिर मिलेंगे", self.cfg)
        self.assertEqual(out, "नमस्ते। फिर मिलेंगे")

    def test_double_danda_verse_marker_preserved(self):
        # ॥ (U+0965, verse end) is its own character and must survive untouched.
        self.assertEqual(postprocess("शुभम् ॥", self.cfg), "शुभम्॥")

    def test_devanagari_latin_join_gets_space(self):
        # Code-switched runs sometimes arrive glued together.
        out = postprocess("मेरीmeeting कल है", self.cfg)
        self.assertEqual(out, "मेरी meeting कल है")

    def test_latin_devanagari_join_gets_space(self):
        self.assertEqual(postprocess("officeजाना है", self.cfg), "office जाना है")

    def test_devanagari_digit_join_gets_space(self):
        self.assertEqual(postprocess("कल10 बजे", self.cfg), "कल 10 बजे")

    def test_already_spaced_code_switch_untouched(self):
        text = "मैं office जा रहा हूँ"
        self.assertEqual(postprocess(text, self.cfg), text)

    def test_glued_rupee_amount_converts_after_split(self):
        # The script-boundary split must run BEFORE currency conversion, so a
        # glued amount is both spaced and ₹-converted in one pass.
        out = postprocess("मैंने50 rupees दिए", self.cfg)
        self.assertEqual(out, "मैंने ₹50 दिए")

    def test_glued_dollar_amount_converts_after_split(self):
        out = postprocess("मुझे$10k मिले", self.cfg)
        self.assertEqual(out, "मुझे ₹10 lakh मिले")

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


class CodeAndPathPunctuationTests(unittest.TestCase):
    """Spacing cleanup must only tidy prose punctuation, never code or paths."""

    CFG = {"fillers": ["um"], "replacements": {},
           "profiles": {"code": {"convert_currency": False, "remove_fillers": False}}}

    CASES = {
        "use std::vector here": "use std::vector here",
        "run ./deploy.sh now": "run ./deploy.sh now",
        "copy the .env file": "copy the .env file",
        "then cd .. and build": "then cd .. and build",
        ":wq to quit": ":wq to quit",
        "for (;;) loop": "for (;;) loop",
        "edit the .gitignore file": "edit the .gitignore file",
    }

    def test_code_survives_in_every_profile(self):
        for profile in ("default", "code"):
            for text, expected in self.CASES.items():
                with self.subTest(profile=profile, text=text):
                    self.assertEqual(postprocess(text, self.CFG, profile=profile), expected)

    def test_prose_spacing_still_tidied(self):
        self.assertEqual(postprocess("Hello , world .", self.CFG), "Hello, world.")
        self.assertEqual(postprocess("I, um, went", self.CFG), "I, went")
        self.assertEqual(postprocess("Um, then we left", self.CFG), "Then we left")


class FillerEdgeTests(unittest.TestCase):
    CFG = {"fillers": ["um", "uh", "er", "erm"], "replacements": {}}

    def test_allcaps_token_is_not_a_filler(self):
        self.assertEqual(postprocess("Take him to the ER now.", self.CFG),
                         "Take him to the ER now.")

    def test_hyphenated_words_never_split(self):
        self.assertEqual(postprocess("Uh-oh, the build failed.", self.CFG),
                         "Uh-oh, the build failed.")
        self.assertEqual(postprocess("She said um-hmm and left.", self.CFG),
                         "She said um-hmm and left.")

    def test_ordinary_fillers_still_removed(self):
        self.assertEqual(postprocess("Um, I er think, uh, yes", self.CFG),
                         "I think, yes")


class RepetitionBoundaryTests(unittest.TestCase):
    def test_suffix_of_previous_word_is_not_a_copy(self):
        # "also so so" is two copies of "so", not three.
        self.assertEqual(collapse_repetitions("It was also so so."), "It was also so so.")
        self.assertEqual(collapse_repetitions("Goodbye bye bye."), "Goodbye bye bye.")

    def test_loop_after_open_paren_still_collapses(self):
        self.assertEqual(collapse_repetitions("(guilty guilty guilty, ok"), "(guilty, ok")

    def test_devanagari_suffix_is_not_a_copy(self):
        # "ने" after the combining mark in "मैंने" is inside that word.
        self.assertEqual(collapse_repetitions("मैंने ने ने"), "मैंने ने ने")


class ReplacementBoundaryTests(unittest.TestCase):
    def test_non_word_edged_keys_fire(self):
        self.assertEqual(apply_replacements("I write c++ code", {"c++": "C++"}),
                         "I write C++ code")
        self.assertEqual(apply_replacements("we use .net here", {".net": ".NET"}),
                         "we use .NET here")
        self.assertEqual(apply_replacements("ask dr. rao", {"dr.": "Doctor"}),
                         "ask Doctor rao")

    def test_non_word_edged_keys_respect_neighbours(self):
        self.assertEqual(apply_replacements("asp.net rocks", {".net": ".NET"}),
                         "asp.net rocks")

    def test_devanagari_key_never_matches_mid_word(self):
        self.assertEqual(apply_replacements("मैंने कहा", {"मैं": "X"}), "मैंने कहा")
        self.assertEqual(apply_replacements("कोई नहीं", {"को": "Y"}), "कोई नहीं")
        self.assertEqual(apply_replacements("मैं गया", {"मैं": "X"}), "X गया")

    def test_urls_emails_and_filenames_untouched(self):
        rules = {"bangalore": "Bengaluru", "upi": "UPI", "blr": "Bengaluru"}
        for text in ("Open https://bangalore.craigslist.org/jobs now",
                     "see www.bangalore.example.com",
                     "Mail me at team@upi.example.com",
                     "Check `blr config` first",
                     "The file is upi.json"):
            with self.subTest(text=text):
                self.assertEqual(apply_replacements(text, rules), text)

    def test_rule_equal_to_whole_dotted_token_still_fires(self):
        self.assertEqual(apply_replacements("use node.js", {"node.js": "Node.js"}),
                         "use Node.js")

    def test_prose_next_to_url_still_replaced(self):
        self.assertEqual(
            apply_replacements("blr office https://x.example/blr", {"blr": "Bengaluru"}),
            "Bengaluru office https://x.example/blr")

    def test_lowercase_replacement_capitalized_at_sentence_start(self):
        rules = {"gonna": "going to", "iphone": "iPhone"}
        self.assertEqual(apply_replacements("I'm late. Gonna run.", rules),
                         "I'm late. Going to run.")
        self.assertEqual(apply_replacements("Gonna run.", rules), "Going to run.")
        # Mid-sentence, or a deliberately mixed-case value, stays as written.
        self.assertEqual(apply_replacements("I'm gonna run.", rules), "I'm going to run.")
        self.assertEqual(apply_replacements("Iphone died.", rules), "iPhone died.")


class CaseVariantRuleTests(unittest.TestCase):
    def setUp(self):
        pp._WARNED_PROFILES.clear()

    def test_profile_rule_differing_in_case_overrides_base(self):
        cfg = {"fillers": [], "replacements": {"blr": "Bengaluru"},
               "profiles": {"p": {"replacements": {"Blr": "BLR"}}}}
        self.assertEqual(postprocess("going to blr", cfg, profile="p"), "going to BLR")
        self.assertEqual(postprocess("going to blr", cfg), "going to Bengaluru")

    def test_overlay_rule_differing_in_case_overrides_base(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "base.json"
            base.write_text(json.dumps({"fillers": [], "replacements": {"blr": "Bengaluru"}}))
            overlay = Path(d) / "user.json"
            overlay.write_text(json.dumps({"replacements": {"BLR": "Bangalore"}}))
            cfg = load_config(base, user_path=overlay)
        self.assertEqual(postprocess("going to blr", cfg), "going to Bangalore")


class MalformedOverlayTests(unittest.TestCase):
    """A valid-JSON but wrong-shaped user dictionary must be coerced or skipped
    with a warning, never crash load_config or the cleanup that follows."""

    def _load(self, body):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "base.json"
            base.write_text(json.dumps({"fillers": ["um"], "replacements": {"blr": "Bengaluru"}}))
            overlay = Path(d) / "user.json"
            overlay.write_text(body, encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                cfg = load_config(base, user_path=overlay)
        return cfg, err.getvalue()

    def test_each_wrong_shape_is_survivable(self):
        shapes = {
            "top-level list": "[]",
            "replacements list": '{"replacements": ["a"]}',
            "replacement null": '{"replacements": {"blr": null}}',
            "replacement number": '{"replacements": {"blr": 5}}',
            "fillers not list": '{"fillers": "um"}',
            "filler number": '{"fillers": [5, null]}',
        }
        for label, body in shapes.items():
            with self.subTest(shape=label):
                cfg, err = self._load(body)
                self.assertEqual(postprocess("um going to blr", cfg), "going to Bengaluru")
                self.assertIn("overlay", err.lower())

    def test_good_entries_next_to_bad_ones_are_kept(self):
        cfg, _ = self._load('{"fillers": ["erm", 5], '
                            '"replacements": {"kalpith": "Kalpit", "x": null}}')
        self.assertIn("erm", cfg["fillers"])
        self.assertEqual(cfg["replacements"]["kalpith"], "Kalpit")
        self.assertNotIn("x", cfg["replacements"])

    def test_non_string_rules_passed_directly_are_skipped(self):
        self.assertEqual(apply_replacements("a blr", {"blr": None, "a": "A"}), "A blr")
        self.assertEqual(remove_fillers("um hi", ["um", 5, None]), " hi")


class LoadConfigTests(unittest.TestCase):
    def test_module_uses_synthetic_personal_data(self):
        # Guards the setUpModule isolation above.
        self.assertTrue(str(pp.USER_DICT_PATH).startswith(_TMP.name))
        self.assertTrue(str(namematch.LEXICON_PATH).startswith(_TMP.name))
        self.assertEqual(postprocess("ask Kalpith", {"fillers": [], "replacements": {}}),
                         "ask Kalpit")

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


class ProfileTests(unittest.TestCase):
    """Per-app contexts: a named profile toggles convert_currency /
    remove_fillers and merges its replacements over the defaults. "default"
    (or an unknown name) must be exactly the historical behavior."""

    CFG = {
        "fillers": ["um"],
        "replacements": {"blr": "Bengaluru"},
        "profiles": {
            "code": {
                "convert_currency": False,
                "remove_fillers": False,
                "replacements": {},
            },
        },
    }

    def setUp(self):
        # Module-level warn-once set persists across the whole test process;
        # clear it so warning assertions can't fail order-dependently.
        pp._WARNED_PROFILES.clear()

    def test_default_profile_matches_no_profile_arg(self):
        self.assertEqual(
            postprocess("um moving to blr", self.CFG),
            postprocess("um moving to blr", self.CFG, profile="default"),
        )

    def test_convert_currency_disabled_leaves_dollars(self):
        out = postprocess("that costs $10k today", self.CFG, profile="code")
        self.assertIn("$10k", out)
        self.assertNotIn("₹", out)

    def test_remove_fillers_disabled_keeps_um(self):
        self.assertEqual(postprocess("um hello there", self.CFG, profile="code"),
                         "um hello there")

    def test_profile_replacements_override_default(self):
        cfg = {
            "fillers": [],
            "replacements": {"todo": "backlog"},
            "profiles": {"work": {"replacements": {"todo": "TODOTicket"}}},
        }
        self.assertEqual(postprocess("todo", cfg, profile="work"), "TODOTicket")
        # Default rules keep the top-level mapping.
        self.assertEqual(postprocess("todo", cfg, profile="default"), "backlog")

    def test_profile_replacements_override_promoted_overlay(self):
        # Precedence is deliberate: a context term beats a personally promoted
        # one on collision (promote.py writes the overlay).
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "base.json"
            overlay = Path(d) / "overlay.json"
            base.write_text(json.dumps({
                "fillers": [], "replacements": {"acme": "Acme Corp"},
                "profiles": {"legal": {"replacements": {"acme": "ACME Ltd"}}},
            }), encoding="utf-8")
            overlay.write_text(json.dumps({"replacements": {"acme": "Acme Inc"}}),
                               encoding="utf-8")
            cfg = load_config(base, user_path=overlay)
        self.assertEqual(postprocess("acme", cfg, profile="legal"), "ACME Ltd")

    def test_unknown_profile_falls_back_to_default(self):
        pp._WARNED_PROFILES.clear()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = postprocess("$10k um blr", self.CFG, profile="no-such-profile")
        self.assertEqual(out, postprocess("$10k um blr", self.CFG))
        self.assertIn("unknown profile 'no-such-profile'", err.getvalue())

    def test_unknown_profile_warned_once_per_process(self):
        pp._WARNED_PROFILES.clear()
        with contextlib.redirect_stderr(io.StringIO()):
            postprocess("x", self.CFG, profile="warn-once")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            postprocess("x", self.CFG, profile="warn-once")
        self.assertNotIn("unknown profile", err.getvalue())

    def test_absent_toggles_default_true(self):
        cfg = {"fillers": ["um"], "replacements": {},
               "profiles": {"mail": {}}}
        self.assertEqual(postprocess("um hi $10k", cfg, profile="mail"),
                         "hi ₹10 lakh")

    def test_non_string_profile_name_is_just_unknown(self):
        pp._WARNED_PROFILES.clear()
        resolved = resolve_profile(self.CFG, 5)
        self.assertTrue(resolved["convert_currency"])
        self.assertEqual(resolved["replacements"], {"blr": "Bengaluru"})

    def test_unicode_profile_name_works_end_to_end(self):
        cfg = {"fillers": ["um"], "replacements": {},
               "profiles": {"मेराcontext": {"convert_currency": False}}}
        out = postprocess("um $10k", cfg, profile="मेराcontext")
        self.assertIn("$10k", out)


class ProfileConfigValidationTests(unittest.TestCase):
    """load_config must parse and validate the optional profiles object:
    malformed entries are skipped with a stderr warning, never fatal."""

    def setUp(self):
        pp._WARNED_PROFILES.clear()

    def _write_base(self, d, obj, name="base.json"):
        path = Path(d) / name
        path.write_text(json.dumps(obj), encoding="utf-8")
        return path

    def _load(self, obj):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = self._write_base(tmp.name, obj)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cfg = load_config(base, user_path=Path(tmp.name) / "no-overlay.json")
        return cfg, err.getvalue()

    def test_profiles_parsed_and_returned(self):
        cfg, _ = self._load({"fillers": [], "replacements": {},
                             "profiles": {"code": {"convert_currency": False}}})
        self.assertEqual(cfg["profiles"], {"code": {"convert_currency": False}})

    def test_missing_profiles_key_yields_empty(self):
        cfg, _ = self._load({"fillers": [], "replacements": {}})
        self.assertEqual(cfg["profiles"], {})

    def test_profiles_not_an_object_warns_and_drops_all(self):
        cfg, err = self._load({"fillers": [], "replacements": {},
                               "profiles": ["code"]})
        self.assertEqual(cfg["profiles"], {})
        self.assertIn("not a JSON object", err)

    def test_malformed_entry_skipped_with_warning(self):
        for bad in ("a string", 5, {"replacements": "nope"},
                    {"convert_currency": "false"}, {"remove_fillers": 1}):
            with self.subTest(bad=bad):
                cfg, err = self._load({"fillers": [], "replacements": {},
                                       "profiles": {"good": {}, "bad": bad}})
                self.assertEqual(list(cfg["profiles"]), ["good"])
                self.assertIn("malformed profile 'bad'", err)

    def test_replacements_with_non_string_values_skip_entry(self):
        cfg, err = self._load({
            "fillers": [], "replacements": {},
            "profiles": {"code": {"replacements": {"todo": 5}}},
        })
        self.assertEqual(cfg["profiles"], {})
        self.assertIn("malformed profile 'code'", err)

    def test_explicit_default_entry_ignored_with_warning(self):
        cfg, err = self._load({
            "fillers": [], "replacements": {},
            "profiles": {"default": {"convert_currency": False}},
        })
        self.assertEqual(cfg["profiles"], {})
        self.assertIn("ignoring profiles['default']", err)

    def test_empty_name_skipped(self):
        # Note: JSON object keys are always strings, so a non-string name can
        # only reach _validated_profiles from hand-built dicts; via files the
        # realistic bad case is an empty name.
        cfg, _ = self._load({
            "fillers": [], "replacements": {},
            "profiles": {"": {"convert_currency": False}},
        })
        self.assertEqual(cfg["profiles"], {})

    def test_wrong_typed_fillers_still_raises_for_daemon_guard(self):
        # Pins the contract the daemon's reload guard relies on: load_config
        # swallows JSON decode errors itself but a wrong-typed field RAISES,
        # and the daemon keeps serving its last-good config when that happens.
        with tempfile.TemporaryDirectory() as d:
            base = self._write_base(d, {"fillers": 5})
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(TypeError):
                    load_config(base, user_path=Path(d) / "none.json")


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


class ReviewRegressionTests(unittest.TestCase):
    """Regressions found in review of the code-safe cleanup (PR #52)."""

    CFG = {"fillers": ["um", "uh", "er"], "replacements": {"blr": "Bengaluru", "मै": "X"}}

    def test_allcaps_hesitations_are_still_fillers(self):
        self.assertEqual(postprocess("I AM, UM, WAITING", self.CFG), "I AM, WAITING")
        self.assertEqual(postprocess("UH, NO.", self.CFG), "NO.")
        self.assertEqual(postprocess("Take him to the ER now.", self.CFG),
                         "Take him to the ER now.")

    def test_prose_comma_before_a_word_is_tidied(self):
        self.assertEqual(postprocess("Hello ,world", self.CFG), "Hello, world")

    def test_leading_filler_glued_to_comma(self):
        self.assertEqual(postprocess("Um,okay go.", self.CFG), "Okay go.")

    def test_space_before_mark_inside_quotes_and_parens(self):
        self.assertEqual(postprocess('He said, "hello ."', self.CFG), 'He said, "hello."')
        self.assertEqual(postprocess("It works (mostly .)", self.CFG), "It works (mostly.)")

    def test_spaced_ellipsis_binds_left(self):
        self.assertEqual(postprocess("Wait ... what", self.CFG), "Wait... what")

    def test_rules_skip_email_www_and_devanagari_marks(self):
        self.assertEqual(postprocess("mail blr@x.com today", self.CFG), "mail blr@x.com today")
        self.assertEqual(postprocess("see www.x.com/blr now", self.CFG), "see www.x.com/blr now")
        self.assertEqual(postprocess("in blr now", self.CFG), "in Bengaluru now")
        self.assertEqual(apply_replacements("see x.com/blr now", {"blr": "Bengaluru"}),
                         "see x.com/blr now")
        self.assertEqual(postprocess("मैं ठीक हूँ", self.CFG), "मैं ठीक हूँ")

    def test_rule_value_capitalized_only_at_sentence_start(self):
        cfg = {"fillers": [], "replacements": {"gonna": "going to"}}
        self.assertEqual(postprocess("Gonna run. Then Gonna rest", cfg),
                         "Going to run. Then going to rest")

    def test_long_input_stays_linear(self):
        import time
        cfg = {"fillers": ["um"], "replacements": {"blr": "Bengaluru"}}
        for text in ("A" * 50000, "Gonna go. " * 8000, "a.b " * 20000):
            with self.subTest(size=len(text)):
                start = time.monotonic()
                postprocess(text, cfg)
                self.assertLess(time.monotonic() - start, 2.0)
