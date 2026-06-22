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

    def test_save_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            config.save_settings({"model": "tiny.en", "toast_enabled": False}, path)
            reloaded = config.load_settings(path)
            self.assertEqual(reloaded["model"], "tiny.en")
            self.assertFalse(reloaded["toast_enabled"])
            self.assertEqual(reloaded["hotkey_label"], "Right Option")


if __name__ == "__main__":
    unittest.main()
