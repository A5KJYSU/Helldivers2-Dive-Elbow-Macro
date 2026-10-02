# -*- coding: utf-8 -*-
"""肘击飞扑通牒宏：应用入口。

按住触发键（默认鼠标侧键 MOUSE5）且游戏窗口在前台时，屏幕中央显示高度
选择窗口；上下移动鼠标实时计算等待时间，水平拖出窗口范围则取消；松开触
发键后异步执行宏。

状态信息输出到控制台；Tk 根窗口保持隐藏，仅作为 ShootingOverlay 的宿主。
所有 Tk 操作统一经 ``root.after(0, ...)`` 回主线程执行，pynput 监听线程
只更新普通 Python 属性，不直接触碰 Tk。
"""
import ctypes
import threading
import time
import tkinter as tk

import win32api
import win32con
import win32gui
import win32print
from pynput import keyboard, mouse

import config
import macro


def get_screen_scal():
    """屏幕缩放比例 = 物理分辨率宽度 / 逻辑分辨率宽度。"""
    return round(
        win32print.GetDeviceCaps(win32gui.GetDC(0), win32con.DESKTOPHORZRES)
        / win32api.GetSystemMetrics(0),
        2,
    )


def get_screen_size():
    """逻辑分辨率 (宽, 高)。"""
    return win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1)


def foreground_window_is_game():
    """前台窗口标题是否包含游戏标识 HELLDIVERS。"""
    return "HELLDIVERS" in win32gui.GetWindowText(win32gui.GetForegroundWindow())


def async_run(fn):
    """在守护线程中执行 fn。"""
    thread = threading.Thread(target=fn, daemon=True)
    thread.start()
    return thread


def set_click_through_by_tk(root):
    """设置鼠标点击穿透（复刻参考 tk_tool.set_click_through_by_tk）。"""
    root.update()
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    if hwnd <= 0:
        print("[窗口] 获取窗口句柄失败，跳过鼠标穿透设置: %s" % (hwnd,))
        return
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    win32gui.SetWindowLong(
        hwnd,
        win32con.GWL_EXSTYLE,
        style
        | win32con.WS_EX_LAYERED
        | win32con.WS_EX_TRANSPARENT
        | win32con.WS_EX_NOACTIVATE
        | win32con.WS_EX_TOOLWINDOW,
    )


class ShootingOverlay(object):
    """屏幕中央的射击延迟时间选择窗口（复刻参考 ShootingHeightWindow）。"""

    def __init__(self, master, height=150, alpha=0.5):
        self.bg = "#111"
        self.label_font_color = "#f00"
        self.last_call_time = 0
        self.is_show = False
        self.shooting_state = False
        self.result_height = 0
        self.canvas_line = None
        self.mouse_position_line = None
        self.mouse_start_x = 0
        self.mouse_start_y = 0
        self.last_mouse_x = 0
        self.last_mouse_y = 0
        self.status_callback = None
        self.cfg = dict(config.DEFAULT_CONFIG)

        self.root = tk.Toplevel(master=master)
        root = self.root
        root.withdraw()
        root.title("HELLDIVERS 选择射击延迟时间窗口")
        root.attributes("-topmost", True)
        root.configure(bg=self.bg)
        self.window_width = int(height / 3)
        self.window_height = height
        self.screen_width = root.winfo_screenwidth()
        self.screen_height = root.winfo_screenheight()
        self.center_x = int(self.screen_width / 2)
        self.center_y = int(self.screen_height / 2)
        self.window_offset_x = int(self.screen_width // 2 - self.window_width // 2)
        self.window_offset_y = int(self.screen_height // 2 - self.window_height // 2)
        root.geometry(
            "%dx%d+%d+%d"
            % (
                self.window_width,
                self.window_height,
                self.window_offset_x,
                self.window_offset_y,
            )
        )
        root.overrideredirect(True)
        root.resizable(False, False)
        root.attributes("-alpha", alpha)
        self.label_height = 20
        self.canvas_height = self.window_height - self.label_height

        self.canvas = tk.Canvas(
            root,
            width=self.window_width,
            height=self.canvas_height,
            bg=self.bg,
            highlightthickness=0,
            border=0,
        )
        self.canvas.pack()
        self.label = tk.Label(
            root,
            text="",
            font=("SimHei", 12),
            bg=self.bg,
            fg=self.label_font_color,
            anchor="center",
        )
        self.label.pack()

        self.scal = get_screen_scal()
        set_click_through_by_tk(root)

    # -- 配置与状态 ----------------------------------------------------------
    def set_config(self, cfg):
        self.cfg = cfg

    def set_status_callback(self, callback):
        self.status_callback = callback

    def _notify_status(self, text):
        if self.status_callback is None:
            return
        try:
            self.status_callback(text)
        except (tk.TclError, RuntimeError):
            # 状态文字是纯展示信息：主线程未运行 mainloop（如测试轮询）或窗口
            # 已销毁时，不得因状态刷新失败而阻断宏执行。
            pass

    def compute_wait_time(self):
        return macro.compute_wait_time(
            self.result_height,
            self.cfg["wait_time"],
            self.cfg["top_wait_time"],
            self.cfg["bottom_wait_time"],
        )

    # -- Tk 线程内的实际绘制 -------------------------------------------------
    def _apply_label(self, text):
        self.label.config(text=str(text))

    def _apply_canvas_line(self, y2, line_color):
        if self.canvas_line is not None:
            self.canvas.delete(self.canvas_line)
        self.canvas_line = self.canvas.create_line(
            0, y2, self.window_width, y2, width=4, fill=line_color
        )

    def _apply_mouse_line(self, x1, y1, x2, y2):
        if self.mouse_position_line is not None:
            self.canvas.delete(self.mouse_position_line)
        self.mouse_position_line = self.canvas.create_line(
            x1, y1, x2, y2, fill="#f80", width=3
        )

    def _clear_canvas_lines(self):
        for name in ("mouse_position_line", "canvas_line"):
            item = getattr(self, name)
            if item is not None:
                self.canvas.delete(item)
                setattr(self, name, None)

    # -- 交互逻辑（属性同步更新，Tk 操作排队） --------------------------------
    def init_canvas(self, x, y):
        self.mouse_start_x = self.last_mouse_x = x
        self.mouse_start_y = self.last_mouse_y = y
        self.result_height = 0
        self.shooting_state = True
        h = self.window_height // 2
        self.draw_line(0, h, 0, h)
        self.root.after(0, self._apply_label, str(self.cfg["wait_time"]))

    def draw_line(self, x1, y1, x2, y2):
        y2 = max(0, min(self.canvas_height, y2))
        line_color = "#0f0"
        shooting_state = True
        if x2 < 0 or x2 > self.window_width:
            line_color = "#f00"
            shooting_state = False
        if y2 < y1:
            self.result_height = (y1 - y2) / self.window_height * 2
        else:
            self.result_height = (y1 - y2) / self.canvas_height * 2
        self.shooting_state = shooting_state
        self.root.after(0, self._apply_canvas_line, y2, line_color)

    def on_mouse_move(self, x, y):
        if not self.is_show:
            return
        if abs(self.last_mouse_x - x) > 300 or abs(self.last_mouse_y - y) > 300:
            # 鼠标瞬移：平移起点，不改变当前高度。
            self.mouse_start_x += x - self.last_mouse_x
            self.mouse_start_y += y - self.last_mouse_y
            self.last_mouse_x = x
            self.last_mouse_y = y
            return

        center_x = self.window_width // 2
        center_y = self.window_height // 2
        x2 = (x - self.mouse_start_x) / self.scal + center_x
        y2 = (y - self.mouse_start_y) / self.scal + center_y
        x2 = center_x + (x2 - center_x) // 4
        self.root.after(0, self._apply_mouse_line, center_x, center_y, x2, y2)
        self.draw_line(center_x, center_y, x2, y2)
        self.last_mouse_x = x
        self.last_mouse_y = y
        self.root.after(0, self._apply_label, str(self.compute_wait_time()))

    def show_window(self):
        def show_window_fun():
            if self.last_call_time + 1 > time.time():
                return
            self.root.deiconify()
            if not self.is_show:
                self.is_show = True
            self._notify_status("调整中")

        self.root.after(0, show_window_fun)

    def hide_window(self):
        def hide_window_fun():
            self.root.withdraw()
            if self.is_show:
                self.is_show = False

        self.root.after(0, hide_window_fun)

    def on_trigger_press(self, x, y):
        if not foreground_window_is_game():
            return
        if self.is_show is None or self.is_show:
            return
        self.init_canvas(x, y)
        self.show_window()

    def on_trigger_release(self):
        if not self.is_show:
            self.hide_window()
            return
        self.root.after(0, self._clear_canvas_lines)
        self.hide_window()
        self.is_show = None
        async_run(self._shoot)

    # -- 异步执行 ------------------------------------------------------------
    def _shoot(self):
        self.is_show = False
        if not self.shooting_state:
            self._notify_status("已取消")
            return
        # 冷却只在真正发射时生效（与原版一致：取消不进入冷却）
        self.last_call_time = time.time()
        wait_ms = self.compute_wait_time()
        self._notify_status("运行中")
        try:
            macro.run_macro(
                wait_ms / 1000,
                config.resolve_hotkey(self.cfg["melee_hotkey"]),
                config.resolve_hotkey(self.cfg["dive_hotkey"]),
                config.resolve_hotkey(self.cfg["strafe_hotkey"]),
            )
        finally:
            self._notify_status("待命")


class MacroApp(object):
    """应用外壳：隐藏 Tk 根窗口 + 控制台状态 + 全局监听。"""

    def __init__(self):
        self._destroyed = False
        self._key_down = False
        self.mouse_listener = None
        self.keyboard_listener = None

        self.root = tk.Tk()
        self.root.title("肘击飞扑通牒宏")
        self.root.withdraw()

        self.overlay = ShootingOverlay(self.root)

        self.set_config(config.load_config())
        self.overlay.set_status_callback(self._notify_status)
        self._print_startup()
        self._start_listeners()

    # -- 配置与状态 ----------------------------------------------------------
    def set_config(self, cfg):
        self.cfg = cfg
        self.trigger_spec = config.resolve_hotkey(cfg["trigger_hotkey"])
        self.overlay.set_config(cfg)

    def _print_startup(self):
        print("========================================", flush=True)
        print(" 肘击飞扑通牒宏 已启动", flush=True)
        print("========================================", flush=True)
        print("配置文件: %s" % config.get_config_path(), flush=True)
        print("触发热键: %s" % config.key_to_name(self.trigger_spec), flush=True)
        print(
            "按键: 肘击=%s | 飞扑=%s | 横移=%s"
            % (
                self.cfg["melee_hotkey"],
                self.cfg["dive_hotkey"],
                self.cfg["strafe_hotkey"],
            ),
            flush=True,
        )
        print(
            "等待时间: %s ms (top=%s, bottom=%s)"
            % (
                self.cfg["wait_time"],
                self.cfg["top_wait_time"],
                self.cfg["bottom_wait_time"],
            ),
            flush=True,
        )
        print("提示: 请保持 HELLDIVERS 游戏窗口处于前台", flush=True)
        print("提示: 按 Ctrl+C 或关闭此控制台窗口退出", flush=True)
        print("提示: 修改 config.json 后需重启本程序生效", flush=True)
        self._notify_status("待命")

    def _notify_status(self, text):
        print("[状态] %s" % text, flush=True)

    # -- 全局监听（处理器绝不返回 False，不吞掉任何输入） ---------------------
    def _start_listeners(self):
        self.mouse_listener = mouse.Listener(
            on_click=self._on_click, on_move=self._on_move
        )
        self.keyboard_listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )
        self.mouse_listener.start()
        self.keyboard_listener.start()

    def _on_click(self, x, y, button, pressed):
        if not isinstance(self.trigger_spec, mouse.Button):
            return
        if not config.matches_key(button, self.trigger_spec):
            return
        if pressed:
            self.overlay.on_trigger_press(x, y)
        else:
            self.overlay.on_trigger_release()

    def _on_move(self, x, y):
        self.overlay.on_mouse_move(x, y)

    def _on_press(self, key):
        if not self._is_keyboard_trigger():
            return
        if not config.matches_key(key, self.trigger_spec):
            return
        if self._key_down:
            return
        self._key_down = True
        x, y = win32api.GetCursorPos()
        self.overlay.on_trigger_press(x, y)

    def _on_release(self, key):
        if not self._is_keyboard_trigger():
            return
        if not config.matches_key(key, self.trigger_spec):
            return
        if not self._key_down:
            return
        self._key_down = False
        self.overlay.on_trigger_release()

    def _is_keyboard_trigger(self):
        return isinstance(self.trigger_spec, (keyboard.Key, str))

    # -- 生命周期 ------------------------------------------------------------
    def destroy(self):
        if self._destroyed:
            return
        self._destroyed = True
        for listener in (self.mouse_listener, self.keyboard_listener):
            if listener is not None:
                listener.stop()
        self.root.destroy()


def main():
    app = MacroApp()
    try:
        app.root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        app.destroy()
        print("[状态] 已退出", flush=True)


if __name__ == "__main__":
    main()
