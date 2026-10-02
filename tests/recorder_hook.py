"""Low-level global input event recorder (stdlib ``ctypes`` only).

Chosen approach: **pure ctypes low-level hooks** (``SetWindowsHookExW`` with
``WH_KEYBOARD_LL`` / ``WH_MOUSE_LL``), NOT ``pynput``.  Rationale: the low-level
hook structs (``KBDLLHOOKSTRUCT`` / ``MSLLHOOKSTRUCT``) expose the
``LLKHF_INJECTED`` / ``LLMHF_INJECTED`` flags directly, which lets us prove a
captured event was synthesised by our own macro (``SendInput`` / ``mouse_event``)
rather than typed by a human.  ``pynput``'s listeners drop that flag.

Records are newline-delimited JSON objects written to the file passed to
``start()``::

    {"ts": <float perf_counter>, "kind": "key"|"mouse", "name": <str>,
     "down": true|false, "dx": int, "dy": int, "injected": true|false}

The hooks run their own ``GetMessageW`` loop on a dedicated thread, so the
recorder can be driven either in-process or as a SEPARATE process via the CLI::

    python tests/recorder_hook.py <path> <seconds>
"""

import argparse
import ctypes
import json
import threading
import time
from ctypes import wintypes

# --- hook / message constants ------------------------------------------------
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0

WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_MOUSEHWHEEL = 0x020E

LLKHF_INJECTED = 0x00000010
LLMHF_INJECTED = 0x00000001

XBUTTON1 = 1
XBUTTON2 = 2

_LRESULT = ctypes.c_ssize_t
_WPARAM = ctypes.c_size_t
_LPARAM = ctypes.c_ssize_t
_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", _WPARAM),
        ("lParam", _LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


_HOOKPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, _WPARAM, _LPARAM)

_VK_NAMED = {
    0x08: "BACKSPACE", 0x09: "TAB", 0x0D: "ENTER", 0x10: "SHIFT", 0x11: "CTRL",
    0x12: "ALT", 0x13: "PAUSE", 0x14: "CAPSLOCK", 0x1B: "ESC", 0x20: "SPACE",
    0x21: "PAGEUP", 0x22: "PAGEDOWN", 0x23: "END", 0x24: "HOME", 0x25: "LEFT",
    0x26: "UP", 0x27: "RIGHT", 0x28: "DOWN", 0x2C: "SNAPSHOT", 0x2D: "INSERT",
    0x2E: "DELETE", 0x5B: "LWIN", 0x5C: "RWIN", 0x5D: "APPS",
    0xA0: "SHIFT", 0xA1: "SHIFT", 0xA2: "CTRL", 0xA3: "CTRL",
    0xA4: "ALT", 0xA5: "ALT",
}


def _vk_name(vk):
    if vk in _VK_NAMED:
        return _VK_NAMED[vk]
    if 0x41 <= vk <= 0x5A:
        return chr(vk)                       # A-Z
    if 0x30 <= vk <= 0x39:
        return chr(vk)                       # 0-9
    if 0x60 <= vk <= 0x69:
        return "NUM%d" % (vk - 0x60)
    if 0x70 <= vk <= 0x87:
        return "F%d" % (vk - 0x6F)
    return "VK_0x%02X" % vk


class _Recorder(object):
    def __init__(self):
        self._fh = None
        self._thread = None
        self._tid = 0
        self._ready = threading.Event()
        self._kb_proc = None
        self._ms_proc = None
        self._kb_hook = None
        self._ms_hook = None
        self._last_pt = (0, 0)
        self._user32 = ctypes.windll.user32
        self._user32.CallNextHookEx.restype = _LRESULT
        self._user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, _WPARAM, _LPARAM]
        self._user32.SetWindowsHookExW.restype = ctypes.c_void_p
        self._user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, _HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
        self._user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        self._user32.GetMessageW.restype = wintypes.BOOL
        self._user32.GetMessageW.argtypes = [
            ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        self._user32.PostThreadMessageW.argtypes = [
            wintypes.DWORD, wintypes.UINT, _WPARAM, _LPARAM]

    # -- public API -----------------------------------------------------------
    def start(self, path):
        self._fh = open(path, "a", encoding="utf-8")
        self._thread = threading.Thread(target=self._run, name="recorder-hook")
        self._thread.daemon = True
        self._thread.start()
        if not self._ready.wait(5.0):
            raise RuntimeError("recorder hook thread failed to become ready")

    def stop(self):
        if self._thread is None:
            return
        self._user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        self._thread.join(5.0)
        self._thread = None
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    # -- thread body ----------------------------------------------------------
    def _run(self):
        kernel32 = ctypes.windll.kernel32
        self._tid = kernel32.GetCurrentThreadId()
        start_pt = wintypes.POINT()
        self._user32.GetCursorPos(ctypes.byref(start_pt))
        self._last_pt = (start_pt.x, start_pt.y)
        self._kb_proc = _HOOKPROC(self._on_key)
        self._ms_proc = _HOOKPROC(self._on_mouse)
        self._kb_hook = self._user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_proc, None, 0)
        self._ms_hook = self._user32.SetWindowsHookExW(WH_MOUSE_LL, self._ms_proc, None, 0)
        self._ready.set()
        try:
            msg = MSG()
            while self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                self._user32.TranslateMessage(ctypes.byref(msg))
                self._user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self._kb_hook:
                self._user32.UnhookWindowsHookEx(self._kb_hook)
            if self._ms_hook:
                self._user32.UnhookWindowsHookEx(self._ms_hook)

    # -- hook callbacks -------------------------------------------------------
    def _on_key(self, n_code, w_param, l_param):
        try:
            if n_code == HC_ACTION:
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                self._emit("key", _vk_name(kb.vkCode), down, 0, 0,
                           bool(kb.flags & LLKHF_INJECTED))
        except Exception:
            pass
        return self._user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _on_mouse(self, n_code, w_param, l_param):
        try:
            if n_code == HC_ACTION:
                ms = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                pt_x, pt_y = ms.pt.x, ms.pt.y
                dx, dy = pt_x - self._last_pt[0], pt_y - self._last_pt[1]
                self._last_pt = (pt_x, pt_y)
                injected = bool(ms.flags & LLMHF_INJECTED)
                msg = int(w_param)
                if msg == WM_MOUSEMOVE:
                    self._emit("mouse", "MOVE", False, dx, dy, injected)
                elif msg in (WM_LBUTTONDOWN, WM_LBUTTONUP):
                    self._emit("mouse", "LEFT", msg == WM_LBUTTONDOWN, 0, 0, injected)
                elif msg in (WM_RBUTTONDOWN, WM_RBUTTONUP):
                    self._emit("mouse", "RIGHT", msg == WM_RBUTTONDOWN, 0, 0, injected)
                elif msg in (WM_MBUTTONDOWN, WM_MBUTTONUP):
                    self._emit("mouse", "MIDDLE", msg == WM_MBUTTONDOWN, 0, 0, injected)
                elif msg in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                    which = (ms.mouseData >> 16) & 0xFFFF
                    name = "XBUTTON1" if which == XBUTTON1 else "XBUTTON2"
                    self._emit("mouse", name, msg == WM_XBUTTONDOWN, 0, 0, injected)
                elif msg in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
                    self._emit("mouse", "WHEEL", False, 0, 0, injected)
        except Exception:
            pass
        return self._user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _emit(self, kind, name, down, dx, dy, injected):
        rec = {
            "ts": time.perf_counter(),
            "kind": kind,
            "name": name,
            "down": bool(down),
            "dx": int(dx),
            "dy": int(dy),
            "injected": bool(injected),
        }
        self._fh.write(json.dumps(rec) + "\n")
        self._fh.flush()


_recorder = _Recorder()


def start(path):
    """Start recording global input events, appending JSON lines to ``path``."""
    _recorder.start(path)


def stop():
    """Stop recording and flush/close the output file."""
    _recorder.stop()


def main(path, seconds):
    """Record for ``seconds`` then stop (used as a separate CLI process)."""
    start(path)
    try:
        time.sleep(seconds)
    finally:
        stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Low-level input event recorder")
    parser.add_argument("path", help="output JSON-lines file")
    parser.add_argument("seconds", type=float, help="record duration in seconds")
    args = parser.parse_args()
    main(args.path, args.seconds)
