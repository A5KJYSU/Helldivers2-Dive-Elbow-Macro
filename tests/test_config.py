# -*- coding: utf-8 -*-
"""config 模块单元测试（stdlib unittest，无 pytest）。

运行：python -m unittest tests.test_config -v
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from pynput.keyboard import Key, KeyCode
from pynput.mouse import Button

import config


class TestGetConfigPath(unittest.TestCase):
    """get_config_path 的冻结/非冻结路径规则。"""

    def test_frozen_uses_executable_dir_not_meipass(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_exe = os.path.join(tmp, "macro.exe")
            meipass_dir = os.path.join(tmp, "_internal")
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "executable", fake_exe), \
                    mock.patch.object(sys, "_MEIPASS", meipass_dir, create=True):
                path = config.get_config_path()
            self.assertEqual(path, os.path.join(tmp, "config.json"))
            self.assertNotEqual(os.path.dirname(path), meipass_dir)

    def test_script_mode_uses_module_dir(self):
        expected = os.path.join(os.path.dirname(os.path.abspath(config.__file__)), "config.json")
        self.assertEqual(config.get_config_path(), expected)


class ConfigFileTestCase(unittest.TestCase):
    """把 config.json 重定向到临时目录，避免污染真实项目目录。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_path = os.path.join(self._tmp.name, "config.json")
        patcher = mock.patch.object(config, "get_config_path", return_value=self.config_path)
        self.addCleanup(patcher.stop)
        patcher.start()

    def write_raw(self, text):
        with open(self.config_path, "w", encoding="utf-8") as file:
            file.write(text)

    def write_config(self, data):
        with open(self.config_path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

    def read_config_file(self):
        with open(self.config_path, "r", encoding="utf-8") as file:
            return json.load(file)

    def load_config_capturing_stdout(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result = config.load_config()
        return result, buffer.getvalue()


class TestLoadConfig(ConfigFileTestCase):

    def test_default_config_matches_frozen_schema(self):
        self.assertEqual(config.DEFAULT_CONFIG, {
            "trigger_hotkey": "MOUSE5",
            "melee_hotkey": "F",
            "dive_hotkey": "ALT",
            "strafe_hotkey": "A",
            "wait_time": 110,
            "top_wait_time": 100,
            "bottom_wait_time": 140,
        })
        for key in ("wait_time", "top_wait_time", "bottom_wait_time"):
            self.assertIsInstance(config.DEFAULT_CONFIG[key], int)
            self.assertNotIsInstance(config.DEFAULT_CONFIG[key], bool)

    def test_missing_file_creates_default_config(self):
        result, _ = self.load_config_capturing_stdout()
        self.assertEqual(result, config.DEFAULT_CONFIG)
        self.assertEqual(set(result), set(config.DEFAULT_CONFIG))
        self.assertEqual(len(result), 7)
        self.assertIsNot(result, config.DEFAULT_CONFIG)
        self.assertTrue(os.path.exists(self.config_path))
        self.assertEqual(self.read_config_file(), config.DEFAULT_CONFIG)
        self.assertEqual(
            (result["wait_time"], result["top_wait_time"], result["bottom_wait_time"]),
            (110, 100, 140),
        )
        for key in ("wait_time", "top_wait_time", "bottom_wait_time"):
            self.assertIsInstance(result[key], int)

    def test_load_config_returns_fresh_copy(self):
        first = config.load_config()
        first["wait_time"] = 999
        second = config.load_config()
        self.assertEqual(second["wait_time"], 110)
        self.assertEqual(config.DEFAULT_CONFIG["wait_time"], 110)

    def test_malformed_json_falls_back_to_defaults(self):
        self.write_raw("{ this is not valid json")
        result, output = self.load_config_capturing_stdout()
        self.assertEqual(result, config.DEFAULT_CONFIG)
        self.assertEqual(set(result), set(config.DEFAULT_CONFIG))
        self.assertIn("默认", output)

    def test_unknown_hotkey_falls_back_per_field(self):
        self.write_config(dict(
            config.DEFAULT_CONFIG,
            trigger_hotkey="FOOBAR",
            melee_hotkey="T",
            wait_time=120,
        ))
        result, output = self.load_config_capturing_stdout()
        self.assertEqual(result["trigger_hotkey"], "MOUSE5")
        self.assertEqual(result["melee_hotkey"], "T")
        self.assertEqual(result["dive_hotkey"], "ALT")
        self.assertEqual(result["strafe_hotkey"], "A")
        self.assertEqual(result["wait_time"], 120)
        self.assertEqual(result["top_wait_time"], 100)
        self.assertEqual(result["bottom_wait_time"], 140)
        self.assertIn("FOOBAR", output)

    def test_invalid_wait_time_falls_back_to_default(self):
        for invalid in ("abc", -1, 0, True):
            with self.subTest(wait_time=invalid):
                self.write_config(dict(config.DEFAULT_CONFIG, wait_time=invalid))
                result, output = self.load_config_capturing_stdout()
                self.assertEqual(result["wait_time"], 110)
                self.assertIsInstance(result["wait_time"], int)
                self.assertIn("默认", output)

    def test_valid_wait_time_preserved_as_int(self):
        self.write_config(dict(config.DEFAULT_CONFIG, wait_time=110))
        result, _ = self.load_config_capturing_stdout()
        self.assertEqual(result["wait_time"], 110)
        self.assertIsInstance(result["wait_time"], int)
        self.assertNotIsInstance(result["wait_time"], bool)

    def test_fractional_ms_values_round_to_int(self):
        self.write_config(dict(
            config.DEFAULT_CONFIG,
            wait_time=110.6,
            top_wait_time=99.6,
            bottom_wait_time=140.4,
        ))
        result, _ = self.load_config_capturing_stdout()
        self.assertEqual(
            (result["wait_time"], result["top_wait_time"], result["bottom_wait_time"]),
            (111, 100, 140),
        )

    def test_invalid_top_and_bottom_wait_time_fall_back(self):
        for invalid in ("abc", -5, True):
            with self.subTest(invalid=invalid):
                self.write_config(dict(
                    config.DEFAULT_CONFIG,
                    top_wait_time=invalid,
                    bottom_wait_time=invalid,
                ))
                result, output = self.load_config_capturing_stdout()
                self.assertEqual(result["top_wait_time"], 100)
                self.assertEqual(result["bottom_wait_time"], 140)
                self.assertIsInstance(result["top_wait_time"], int)
                self.assertIsInstance(result["bottom_wait_time"], int)
                self.assertIn("默认", output)

    def test_valid_top_and_bottom_wait_time_preserved_as_int(self):
        self.write_config(dict(config.DEFAULT_CONFIG, top_wait_time=90, bottom_wait_time=150))
        result, _ = self.load_config_capturing_stdout()
        self.assertEqual(result["top_wait_time"], 90)
        self.assertEqual(result["bottom_wait_time"], 150)
        self.assertIsInstance(result["top_wait_time"], int)
        self.assertIsInstance(result["bottom_wait_time"], int)

    def test_partial_config_fills_missing_keys(self):
        self.write_config({"melee_hotkey": "G"})
        result = config.load_config()
        self.assertEqual(set(result), set(config.DEFAULT_CONFIG))
        self.assertEqual(len(result), 7)
        self.assertEqual(result["melee_hotkey"], "G")
        self.assertEqual(result["trigger_hotkey"], "MOUSE5")
        self.assertEqual(result["wait_time"], 110)
        self.assertEqual(result["top_wait_time"], 100)
        self.assertEqual(result["bottom_wait_time"], 140)


class TestResolveHotkey(unittest.TestCase):

    def test_mouse_buttons(self):
        cases = [
            ("MOUSE1", Button.left),
            ("MOUSE2", Button.right),
            ("MOUSE3", Button.middle),
            ("MOUSE4", Button.x1),
            ("MOUSE5", Button.x2),
        ]
        for name, spec in cases:
            with self.subTest(name=name):
                self.assertEqual(config.resolve_hotkey(name), spec)

    def test_mouse_names_case_insensitive(self):
        self.assertEqual(config.resolve_hotkey("mouse5"), Button.x2)
        self.assertEqual(config.resolve_hotkey("Mouse1"), Button.left)

    def test_special_keys(self):
        cases = [
            ("ALT", Key.alt),
            ("CTRL", Key.ctrl),
            ("SHIFT", Key.shift),
            ("SPACE", Key.space),
            ("ESC", Key.esc),
            ("ENTER", Key.enter),
            ("TAB", Key.tab),
        ] + [("F%d" % index, getattr(Key, "f%d" % index)) for index in range(1, 13)]
        for name, spec in cases:
            with self.subTest(name=name):
                self.assertEqual(config.resolve_hotkey(name), spec)

    def test_special_keys_case_insensitive(self):
        self.assertEqual(config.resolve_hotkey("alt"), Key.alt)
        self.assertEqual(config.resolve_hotkey("f12"), Key.f12)

    def test_single_printable_char_normalized_lowercase(self):
        self.assertEqual(config.resolve_hotkey("T"), "t")
        self.assertEqual(config.resolve_hotkey("a"), "a")
        self.assertEqual(config.resolve_hotkey("7"), "7")

    def test_unknown_empty_or_non_string_returns_none(self):
        for name in ("FOOBAR", "", "   ", "AB", "MOUSE10", None, 123):
            with self.subTest(name=name):
                self.assertIsNone(config.resolve_hotkey(name))


class TestKeyToNameAndMatchesKey(unittest.TestCase):

    def test_key_to_name_mouse_buttons(self):
        self.assertEqual(config.key_to_name(Button.left), "MOUSE1")
        self.assertEqual(config.key_to_name(Button.right), "MOUSE2")
        self.assertEqual(config.key_to_name(Button.middle), "MOUSE3")
        self.assertEqual(config.key_to_name(Button.x1), "MOUSE4")
        self.assertEqual(config.key_to_name(Button.x2), "MOUSE5")

    def test_key_to_name_special_keys(self):
        self.assertEqual(config.key_to_name(Key.alt), "ALT")
        self.assertEqual(config.key_to_name(Key.ctrl), "CTRL")
        self.assertEqual(config.key_to_name(Key.shift), "SHIFT")
        self.assertEqual(config.key_to_name(Key.space), "SPACE")
        self.assertEqual(config.key_to_name(Key.esc), "ESC")
        self.assertEqual(config.key_to_name(Key.enter), "ENTER")
        self.assertEqual(config.key_to_name(Key.tab), "TAB")
        self.assertEqual(config.key_to_name(Key.f1), "F1")
        self.assertEqual(config.key_to_name(Key.f12), "F12")

    def test_key_to_name_char_and_unknown(self):
        self.assertEqual(config.key_to_name("t"), "T")
        self.assertEqual(config.key_to_name(None), "")

    def test_matches_mouse_button(self):
        self.assertTrue(config.matches_key(Button.x2, config.resolve_hotkey("MOUSE5")))
        self.assertFalse(config.matches_key(Button.left, config.resolve_hotkey("MOUSE5")))

    def test_matches_special_key(self):
        self.assertTrue(config.matches_key(Key.alt, config.resolve_hotkey("ALT")))
        self.assertFalse(config.matches_key(Key.ctrl, config.resolve_hotkey("ALT")))

    def test_matches_char_case_insensitive(self):
        spec = config.resolve_hotkey("T")
        self.assertTrue(config.matches_key(KeyCode.from_char("T"), spec))
        self.assertTrue(config.matches_key(KeyCode.from_char("t"), spec))
        self.assertFalse(config.matches_key(KeyCode.from_char("g"), spec))

    def test_matches_keycode_without_char_is_safe(self):
        pressed = KeyCode(vk=18)
        self.assertIsNone(pressed.char)
        self.assertFalse(config.matches_key(pressed, config.resolve_hotkey("T")))
        self.assertFalse(config.matches_key(pressed, config.resolve_hotkey("ALT")))

    def test_matches_none_spec_or_pressed(self):
        self.assertFalse(config.matches_key(Button.left, None))
        self.assertFalse(config.matches_key(None, config.resolve_hotkey("ALT")))

    def test_round_trip_name_spec_match(self):
        names = [
            "MOUSE1", "MOUSE2", "MOUSE3", "MOUSE4", "MOUSE5",
            "ALT", "CTRL", "SHIFT", "SPACE", "ESC", "ENTER", "TAB",
            "F1", "F6", "F12", "T",
        ]
        for name in names:
            with self.subTest(name=name):
                spec = config.resolve_hotkey(name)
                self.assertIsNotNone(spec)
                self.assertEqual(config.key_to_name(spec), name)
                self.assertTrue(config.matches_key(spec, spec))


if __name__ == "__main__":
    unittest.main()
