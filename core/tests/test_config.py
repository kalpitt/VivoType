"""Tests for core/config.py — shared settings."""

import json
import tempfile
import unittest
from pathlib import Path

from core import config


class ConfigTests(unittest.TestCase):
    def test_defaults_when_missing(self):
        settings = config.load_settings(Path("/no/such/config.json"))
        self.assertEqual(settings["model"], "small.en")
        self.assertEqual(settings["hotkey_keycode"], 61)
        self.assertTrue(settings["sound_enabled"])
        self.assertTrue(settings["hud_enabled"])

    def test_file_overrides_defaults_but_keeps_missing_keys(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps({"model": "tiny.en", "sound_enabled": False}))
            settings = config.load_settings(path)
            self.assertEqual(settings["model"], "tiny.en")
            self.assertFalse(settings["sound_enabled"])
            self.assertEqual(settings["hotkey_keycode"], 61)  # default retained

    def test_corrupt_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text("{not valid json")
            self.assertEqual(config.load_settings(path)["model"], "small.en")

    def test_non_utf8_file_falls_back_to_defaults_with_warning(self):
        # A-F9: UnicodeDecodeError escaped, so the daemon would not start.
        import contextlib, io
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_bytes(b'{"model": "tiny.en\xff\xfe"}')
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                settings = config.load_settings(path)
        self.assertEqual(settings["model"], "small.en")
        self.assertIn("config", err.getvalue())

    def test_deeply_nested_file_falls_back_to_defaults(self):
        # A-F9: json raises RecursionError on pathological nesting.
        import contextlib, io
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text("[" * 200000 + "]" * 200000)
            with contextlib.redirect_stderr(io.StringIO()):
                settings = config.load_settings(path)
        self.assertEqual(settings["model"], "small.en")

    def test_non_string_model_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps({"model": 123, "sound_enabled": "yes"}))
            settings = config.load_settings(path)
            self.assertEqual(settings["model"], "small.en")
            self.assertTrue(settings["sound_enabled"])  # bad bool → default

    def test_save_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            config.save_settings({"model": "tiny.en", "toast_enabled": False}, path)
            reloaded = config.load_settings(path)
            self.assertEqual(reloaded["model"], "tiny.en")
            self.assertFalse(reloaded["toast_enabled"])
            self.assertEqual(reloaded["hotkey_label"], "Right Option")

    def test_hud_enabled_defaults_true_when_missing_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps({"model": "tiny.en"}))
            self.assertTrue(config.load_settings(path)["hud_enabled"])

    def test_suggest_corrections_defaults_off_and_rejects_non_bool(self):
        # The correction offer reads the focused field: it must stay off
        # unless the user turns it on, and a hand-edited "yes" is not on.
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps({"model": "tiny.en"}))
            self.assertIs(config.load_settings(path)["suggest_corrections"], False)
            path.write_text(json.dumps({"suggest_corrections": "yes"}))
            self.assertIs(config.load_settings(path)["suggest_corrections"], False)
            config.save_settings({"suggest_corrections": True}, path)
            self.assertIs(config.load_settings(path)["suggest_corrections"], True)

    def test_hud_enabled_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            config.save_settings({"hud_enabled": False}, path)
            self.assertFalse(config.load_settings(path)["hud_enabled"])
            config.save_settings({"hud_enabled": True}, path)
            self.assertTrue(config.load_settings(path)["hud_enabled"])

    def test_hud_enabled_survives_a_save_that_omits_it(self):
        # save_settings() re-merges over DEFAULTS, so a Swift-written key with no
        # Python-side default would silently vanish here. Guards that regression.
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            config.save_settings({"model": "tiny.en"}, path)
            self.assertIn("hud_enabled", json.loads(path.read_text()))

    def test_voice_commands_default_off_and_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            self.assertFalse(config.load_settings(path)["voice_commands"])
            path.write_text(json.dumps({"voice_commands": "yes"}))
            self.assertFalse(config.load_settings(path)["voice_commands"])
            config.save_settings({"voice_commands": True}, path)
            self.assertTrue(config.load_settings(path)["voice_commands"])

    def test_non_bool_hud_enabled_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps({"hud_enabled": "nope"}))
            self.assertTrue(config.load_settings(path)["hud_enabled"])

    # --- app_profiles (per-app contexts; bundle ID -> profile name) ---

    def test_app_profiles_defaults_to_empty_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps({"model": "tiny.en"}))
            settings = config.load_settings(path)
        self.assertEqual(settings["app_profiles"], {})

    def test_app_profiles_absent_from_defaults_survives_save(self):
        # The Settings-window save path rewrites config.json; a Python-side
        # DEFAULTS entry is what keeps the Swift-written key from vanishing.
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            config.save_settings({"model": "tiny.en"}, path)
            data = json.loads(path.read_text())
        self.assertIn("app_profiles", data)
        self.assertEqual(data["app_profiles"], {})

    def test_app_profiles_round_trip(self):
        mapping = {"com.apple.Notes": "code", "com.google.Chrome": "default"}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            config.save_settings({"app_profiles": mapping}, path)
            self.assertEqual(config.load_settings(path)["app_profiles"], mapping)

    def test_non_dict_app_profiles_coerced_to_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps({"app_profiles": ["com.apple.Notes"]}))
            self.assertEqual(config.load_settings(path)["app_profiles"], {})

    def test_non_string_values_coerce_whole_mapping_to_empty(self):
        # One bad entry invalidates the mapping rather than leaking junk into
        # the daemon protocol; the next Settings save rewrites it cleanly.
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps(
                {"app_profiles": {"com.apple.Notes": 5, "x": "code"}}))
            self.assertEqual(config.load_settings(path)["app_profiles"], {})


if __name__ == "__main__":
    unittest.main()
