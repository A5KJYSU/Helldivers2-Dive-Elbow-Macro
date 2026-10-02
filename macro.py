"""肘击飞扑通牒 mouse macro.

Replicates the ``shooting_model == 1`` branch of the reference
``ShootingHeightWindow.shooting()`` as a standalone function.  Key specs are
already-resolved pynput values, so this module never imports ``config``.
"""

import ctypes
import time

import pynput.keyboard
import pynput.mouse
import win32api
import win32con
import win32gui
import win32print
from pynput.mouse import Button


def compute_wait_time(height, wait_ms, top_ms, bottom_ms):
    """复刻参考 ShootingHeightWindow.compute_wait_time。

    height > 0 时向 top_ms 插值，height < 0 时向 bottom_ms 插值，
    返回整数毫秒。
    """
    wait_time = wait_ms
    if height > 0:
        wait_time = wait_time + (top_ms - wait_time) * height
    elif height < 0:
        wait_time = wait_time - (bottom_ms - wait_time) * height
    return int(wait_time)


def run_macro(wait_time, melee_key, dive_key, strafe_key,
              mouse_ctrl=None, keyboard_ctrl=None):
    if mouse_ctrl is None:
        mouse_ctrl = pynput.mouse.Controller()
    if keyboard_ctrl is None:
        keyboard_ctrl = pynput.keyboard.Controller()

    # Raise system timer precision so time.sleep is accurate.
    ctypes.windll.winmm.timeBeginPeriod(1)

    try:
        h = 300
        screen_h = win32api.GetSystemMetrics(1)
        scale = round(
            win32print.GetDeviceCaps(win32gui.GetDC(0), win32con.DESKTOPHORZRES)
            / win32api.GetSystemMetrics(0),
            2,
        )
        vmove = int(1300 / 1600 * screen_h * scale)

        # Move view left then lift it, so the dive lands on the target.
        win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, -h, -vmove, 0, 1)
        time.sleep(0.3)

        keyboard_ctrl.press(melee_key)
        keyboard_ctrl.press(strafe_key)
        time.sleep(wait_time / 2)

        mouse_ctrl.press(Button.left)
        time.sleep(wait_time / 2)

        keyboard_ctrl.press(dive_key)
        time.sleep(0.05)

        mouse_ctrl.release(Button.left)
        keyboard_ctrl.release(dive_key)
        keyboard_ctrl.release(strafe_key)
        keyboard_ctrl.release(melee_key)

        # Restore the view.
        win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, 0, vmove, 0, 1)
    finally:
        # No stuck keys on any path: release each independently, then restore
        # the system timer precision.
        for release in (
            lambda: mouse_ctrl.release(Button.left),
            lambda: keyboard_ctrl.release(dive_key),
            lambda: keyboard_ctrl.release(strafe_key),
            lambda: keyboard_ctrl.release(melee_key),
        ):
            try:
                release()
            except Exception:
                pass
        ctypes.windll.winmm.timeEndPeriod(1)
