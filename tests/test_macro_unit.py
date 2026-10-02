"""Unit tests for ``macro.run_macro``.

Order/arguments are asserted deterministically through fake controllers and
patched Win32 calls (NO real input is emitted).  Timing is asserted with
tolerance, never exact wall-clock equality.
"""

import ctypes
import time
import unittest
from unittest import mock

import win32api
import win32con
import win32gui
import win32print
from pynput.mouse import Button

import macro

MELEE = "f"
DIVE = "space"
STRAFE = "a"

SCREEN_H = 1080
SCREEN_W = 1920
DESKTOP_H_RES = 2560  # scale = round(2560 / 1920, 2) = 1.33


class _FakeKeyboard(object):
    def __init__(self, log, raise_on=()):
        self._log = log
        self._raise_on = set(raise_on)

    def press(self, key):
        self._log.append(("keyboard", "press", key))
        if ("press", key) in self._raise_on:
            raise RuntimeError("press failed: %r" % (key,))

    def release(self, key):
        self._log.append(("keyboard", "release", key))


class _FakeMouse(object):
    def __init__(self, log, raise_on=()):
        self._log = log
        self._raise_on = set(raise_on)

    def press(self, button):
        self._log.append(("mouse", "press", button))
        if ("press", button) in self._raise_on:
            raise RuntimeError("press failed: %r" % (button,))

    def release(self, button):
        self._log.append(("mouse", "release", button))


class _FakeWinmm(object):
    def __init__(self, log):
        self._log = log

    def timeBeginPeriod(self, period):
        self._log.append(("timeBeginPeriod", period))

    def timeEndPeriod(self, period):
        self._log.append(("timeEndPeriod", period))


class _FakeWindll(object):
    def __init__(self, log):
        self.winmm = _FakeWinmm(log)


class MacroTestBase(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.mouse_events = []
        self.sleeps = []

        self._patches = [
            mock.patch.object(win32api, "mouse_event", side_effect=self._record_mouse_event),
            mock.patch.object(win32api, "GetSystemMetrics", side_effect=self._get_system_metrics),
            mock.patch.object(win32print, "GetDeviceCaps", side_effect=self._get_device_caps),
            mock.patch.object(win32gui, "GetDC", return_value=0x1234),
            mock.patch.object(ctypes, "windll", _FakeWindll(self.log)),
        ]
        for patch in self._patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self._patches):
            patch.stop()

    # -- patched Win32 behaviours --------------------------------------------
    def _record_mouse_event(self, *args):
        self.mouse_events.append(args)

    def _get_system_metrics(self, index):
        return SCREEN_W if index == 0 else SCREEN_H

    def _get_device_caps(self, _hdc, index):
        self.assertEqual(index, win32con.DESKTOPHORZRES)
        return DESKTOP_H_RES

    def expected_vmove(self):
        scale = round(DESKTOP_H_RES / SCREEN_W, 2)
        return int(1300 / 1600 * SCREEN_H * scale)

    def _run_with_fake_sleep(self, wait_time, mouse, keyboard):
        with mock.patch("time.sleep", side_effect=lambda d: self.sleeps.append(d)):
            macro.run_macro(wait_time, MELEE, DIVE, STRAFE,
                            mouse_ctrl=mouse, keyboard_ctrl=keyboard)


class TestRunMacroSequence(MacroTestBase):
    def test_exact_call_order_and_move_arguments(self):
        keyboard = _FakeKeyboard(self.log)
        mouse = _FakeMouse(self.log)
        self._run_with_fake_sleep(0.2, mouse, keyboard)

        vmove = self.expected_vmove()
        self.assertEqual(vmove, 1167)

        # relative move: left + lift view, then the mirrored restore.
        self.assertEqual(self.mouse_events[0],
                         (win32con.MOUSEEVENTF_MOVE, -300, -vmove, 0, 1))
        self.assertEqual(self.mouse_events[1],
                         (win32con.MOUSEEVENTF_MOVE, 0, vmove, 0, 1))

        expected = [
            ("timeBeginPeriod", 1),
            ("keyboard", "press", MELEE),
            ("keyboard", "press", STRAFE),
            ("mouse", "press", Button.left),
            ("keyboard", "press", DIVE),
            ("mouse", "release", Button.left),
            ("keyboard", "release", DIVE),
            ("keyboard", "release", STRAFE),
            ("keyboard", "release", MELEE),
            # finally() unconditionally releases everything, in order.
            ("mouse", "release", Button.left),
            ("keyboard", "release", DIVE),
            ("keyboard", "release", STRAFE),
            ("keyboard", "release", MELEE),
            ("timeEndPeriod", 1),
        ]
        self.assertEqual(self.log, expected)

    def test_sleep_durations_match_step_plan(self):
        keyboard = _FakeKeyboard(self.log)
        mouse = _FakeMouse(self.log)
        self._run_with_fake_sleep(0.2, mouse, keyboard)

        self.assertEqual(len(self.sleeps), 4)
        self.assertAlmostEqual(self.sleeps[0], 0.3, delta=1e-6)   # lift view settle
        self.assertAlmostEqual(self.sleeps[1], 0.1, delta=1e-6)   # wait_time / 2
        self.assertAlmostEqual(self.sleeps[2], 0.1, delta=1e-6)   # wait_time / 2
        self.assertAlmostEqual(self.sleeps[3], 0.05, delta=1e-6)  # dive settle

    def test_time_period_started_and_ended_once(self):
        keyboard = _FakeKeyboard(self.log)
        mouse = _FakeMouse(self.log)
        self._run_with_fake_sleep(0.2, mouse, keyboard)

        self.assertEqual(self.log.count(("timeBeginPeriod", 1)), 1)
        self.assertEqual(self.log.count(("timeEndPeriod", 1)), 1)
        self.assertEqual(self.log[0], ("timeBeginPeriod", 1))
        self.assertEqual(self.log[-1], ("timeEndPeriod", 1))


class TestRunMacroFailurePath(MacroTestBase):
    def test_exception_mid_sequence_releases_all_and_ends_period(self):
        # Raise when pressing the dive key: melee/strafe/left are already held.
        keyboard = _FakeKeyboard(self.log, raise_on=(("press", DIVE),))
        mouse = _FakeMouse(self.log)

        with self.assertRaises(RuntimeError):
            self._run_with_fake_sleep(0.2, mouse, keyboard)

        self.assertEqual(self.log[0], ("timeBeginPeriod", 1))
        self.assertEqual(self.log.count(("timeEndPeriod", 1)), 1)
        self.assertEqual(self.log[-1], ("timeEndPeriod", 1))
        self.assertEqual(self.log[-5:-1], [
            ("mouse", "release", Button.left),
            ("keyboard", "release", DIVE),
            ("keyboard", "release", STRAFE),
            ("keyboard", "release", MELEE),
        ])

    def test_exception_on_first_press_still_releases_everything(self):
        # Raise on the very first press: the finally() guard must still run
        # every release (each wrapped in its own try/except).
        keyboard = _FakeKeyboard(self.log, raise_on=(("press", MELEE),))
        mouse = _FakeMouse(self.log)

        with self.assertRaises(RuntimeError):
            self._run_with_fake_sleep(0.2, mouse, keyboard)

        self.assertEqual(self.log.count(("timeEndPeriod", 1)), 1)
        self.assertEqual(self.log[-5:-1], [
            ("mouse", "release", Button.left),
            ("keyboard", "release", DIVE),
            ("keyboard", "release", STRAFE),
            ("keyboard", "release", MELEE),
        ])


class TestRunMacroTiming(MacroTestBase):
    def test_lift_view_step_uses_real_sleep_within_tolerance(self):
        keyboard = _FakeKeyboard(self.log)
        mouse = _FakeMouse(self.log)
        real_sleep = time.sleep
        measured = []

        def timed(duration):
            started = time.perf_counter()
            real_sleep(duration)
            measured.append((duration, time.perf_counter() - started))

        with mock.patch("time.sleep", side_effect=timed):
            macro.run_macro(0.2, MELEE, DIVE, STRAFE,
                            mouse_ctrl=mouse, keyboard_ctrl=keyboard)

        self.assertEqual([round(d, 3) for d, _ in measured], [0.3, 0.1, 0.1, 0.05])
        self.assertGreaterEqual(measured[0][1], 0.25)
        self.assertLessEqual(measured[0][1], 0.40)
        self.assertGreaterEqual(sum(m[1] for m in measured), 0.45)


class TestComputeWaitTime(unittest.TestCase):
    WAIT_MS = 200
    TOP_MS = 500
    BOTTOM_MS = 100

    def compute(self, height):
        return macro.compute_wait_time(height, self.WAIT_MS, self.TOP_MS, self.BOTTOM_MS)

    def test_height_zero_returns_default_wait(self):
        self.assertEqual(self.compute(0), self.WAIT_MS)

    def test_height_one_returns_top_bound(self):
        self.assertEqual(self.compute(1), self.TOP_MS)

    def test_height_minus_one_returns_bottom_bound(self):
        self.assertEqual(self.compute(-1), self.BOTTOM_MS)

    def test_positive_half_interpolates_between_wait_and_top(self):
        # 200 + (500 - 200) * 0.5 = 350
        self.assertEqual(self.compute(0.5), 350)

    def test_negative_half_interpolates_between_wait_and_bottom(self):
        # 200 - (100 - 200) * (-0.5) = 150
        self.assertEqual(self.compute(-0.5), 150)

    def test_small_positive_height_within_top_and_wait(self):
        result = self.compute(0.01)
        self.assertGreaterEqual(result, self.WAIT_MS)
        self.assertLessEqual(result, self.TOP_MS)
        self.assertEqual(result, 203)

    def test_small_negative_height_within_wait_and_bottom(self):
        result = self.compute(-0.01)
        self.assertGreaterEqual(result, self.BOTTOM_MS)
        self.assertLessEqual(result, self.WAIT_MS)
        self.assertEqual(result, 199)

    def test_result_type_is_int(self):
        for height in (0, 1, -1, 0.5, -0.5, 0.01, -0.01):
            with self.subTest(height=height):
                self.assertIsInstance(self.compute(height), int)

    def test_intermediate_values_stay_monotonic_between_bounds(self):
        self.assertEqual(self.compute(0.25), 275)   # 200 + 300 * 0.25
        self.assertEqual(self.compute(-0.25), 175)  # 200 - (-100) * (-0.25)


if __name__ == "__main__":
    unittest.main()
