# -*- coding: utf-8 -*-
"""main.py 集成测试（stdlib unittest，无 pytest）。

覆盖参考实现（shooting_height_window.py / tk_tool.py）的触发交互：
- 结构：根窗口隐藏、overlay 不可见、监听器接线
- 控制台：启动信息与 [状态] 输出（调整中/运行中/待命/已取消）
- S1：按住触发键显示高度窗口，移动改变等待时间，松开异步执行宏
- S1b：水平拖出窗口范围取消执行
- S3：非触发键忽略、冷却/重入保护
- S4：键盘触发按键流程（去自动重复）
- S2：config.json 损坏时回退默认值且不崩溃

运行：python -m unittest tests.test_integration -v
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from pynput.keyboard import KeyCode
from pynput.mouse import Button

import config

import main  # RED 证据：main.py 不存在时此处 ImportError


class _FakeListener(object):
    """替代 pynput Listener：只记录回调，不安装真实全局钩子。"""

    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        _FakeListener.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class IntegrationTestBase(unittest.TestCase):
    """构建不进入 mainloop 的 MacroApp，并隔离所有外部副作用。"""

    def setUp(self):
        _FakeListener.instances = []
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_path = os.path.join(self._tmp.name, "config.json")
        self._write_config(config.DEFAULT_CONFIG)

        patchers = [
            mock.patch.object(config, "get_config_path", return_value=self.config_path),
            mock.patch.object(main, "foreground_window_is_game", return_value=True),
            mock.patch.object(main.macro, "run_macro"),
            mock.patch.object(main, "async_run", side_effect=lambda fn: fn()),
            mock.patch.object(main.win32api, "GetCursorPos", return_value=(50, 50)),
            mock.patch.object(main.mouse, "Listener", _FakeListener),
            mock.patch.object(main.keyboard, "Listener", _FakeListener),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.mock_run_macro = main.macro.run_macro
        self.app = self._make_app()

    # -- helpers -------------------------------------------------------------
    def _make_app(self):
        app = main.MacroApp()
        self.addCleanup(app.destroy)
        app.overlay.scal = 1.0
        return app

    def _write_config(self, data):
        with open(self.config_path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

    def _write_raw(self, text):
        with open(self.config_path, "w", encoding="utf-8") as file:
            file.write(text)

    def pump(self):
        """执行 after(0) 回调（两次，兼容潜在的嵌套排队）。"""
        self.app.root.update()
        self.app.root.update()


class TestStructuralShell(IntegrationTestBase):
    """用例 1：应用外壳结构（无可见状态窗口）。"""

    def test_root_is_withdrawn_and_overlay_not_viewable(self):
        self.assertEqual(self.app.root.state(), "withdrawn")
        self.pump()
        self.assertFalse(self.app.overlay.root.winfo_viewable())
        self.assertEqual(self.app.overlay.root.state(), "withdrawn")

    def test_listeners_created_wired_and_started(self):
        mouse_listener = next(l for l in _FakeListener.instances if "on_click" in l.kwargs)
        keyboard_listener = next(l for l in _FakeListener.instances if "on_press" in l.kwargs)
        self.assertTrue(mouse_listener.started)
        self.assertTrue(keyboard_listener.started)
        self.assertEqual(mouse_listener.kwargs["on_click"], self.app._on_click)
        self.assertEqual(mouse_listener.kwargs["on_move"], self.app._on_move)
        self.assertEqual(keyboard_listener.kwargs["on_press"], self.app._on_press)
        self.assertEqual(keyboard_listener.kwargs["on_release"], self.app._on_release)


class TestStartupConsole(IntegrationTestBase):
    """用例 2：启动时向控制台输出固定信息。"""

    def test_startup_prints_trigger_path_and_standby(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self._make_app()

        output = buffer.getvalue()
        self.assertIn(" 肘击飞扑通牒宏 已启动", output)
        self.assertIn("配置文件: %s" % self.config_path, output)
        self.assertIn("触发热键: MOUSE5", output)
        self.assertIn("按键: 肘击=F | 飞扑=ALT | 横移=A", output)
        self.assertIn("等待时间: 110 ms (top=100, bottom=140)", output)
        self.assertIn("提示: 请保持 HELLDIVERS 游戏窗口处于前台", output)
        self.assertIn("提示: 按 Ctrl+C 或关闭此控制台窗口退出", output)
        self.assertIn("提示: 修改 config.json 后需重启本程序生效", output)
        self.assertIn("[状态] 待命", output)


class TestSetConfig(IntegrationTestBase):
    """set_config 应用新配置（替代旧的状态窗口重载按钮路径）。"""

    def test_set_config_applies_new_trigger(self):
        self._write_config(dict(config.DEFAULT_CONFIG, trigger_hotkey="MOUSE4"))
        self.app.set_config(config.load_config())
        self.assertEqual(self.app.trigger_spec, config.resolve_hotkey("MOUSE4"))
        self.assertEqual(self.app.overlay.cfg["trigger_hotkey"], "MOUSE4")


class TestConsoleStatus(IntegrationTestBase):
    """用例 3：触发交互通过控制台输出状态。"""

    def test_trigger_prints_adjusting_running_and_standby(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.app._on_click(100, 100, Button.x2, True)
            self.pump()
            self.app._on_click(100, 100, Button.x2, False)
            self.pump()

        output = buffer.getvalue()
        self.assertIn("[状态] 调整中", output)
        self.assertIn("[状态] 运行中", output)
        self.assertIn("[状态] 待命", output)
        self.assertLess(output.index("[状态] 调整中"), output.index("[状态] 运行中"))
        self.assertLess(output.index("[状态] 运行中"), output.index("[状态] 待命"))

    def test_cancel_prints_cancelled_and_skips_macro(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.app._on_click(0, 0, Button.x2, True)
            self.pump()
            self.app._on_move(100, 0)
            self.pump()
            self.app._on_move(200, 0)
            self.pump()
            self.app._on_click(200, 0, Button.x2, False)
            self.pump()

        output = buffer.getvalue()
        self.assertIn("[状态] 调整中", output)
        self.assertIn("[状态] 已取消", output)
        self.assertNotIn("[状态] 运行中", output)
        self.assertEqual(self.mock_run_macro.call_count, 0)


class TestHappyPathFire(IntegrationTestBase):
    """用例 4：按住-松开默认触发，异步执行宏。"""

    def test_press_shows_overlay_and_release_fires_default_wait(self):
        overlay = self.app.overlay
        self.assertFalse(overlay.is_show)

        press_buffer = io.StringIO()
        with redirect_stdout(press_buffer):
            self.app._on_click(100, 100, Button.x2, True)
            self.pump()

        self.assertIn("[状态] 调整中", press_buffer.getvalue())
        self.assertIs(overlay.is_show, True)
        self.assertTrue(overlay.root.winfo_viewable())
        self.assertEqual(overlay.root.state(), "normal")
        self.assertEqual(overlay.label.cget("text"), "110")
        self.assertEqual((overlay.mouse_start_x, overlay.mouse_start_y), (100, 100))
        self.assertEqual(self.mock_run_macro.call_count, 0)

        release_buffer = io.StringIO()
        with redirect_stdout(release_buffer):
            self.app._on_click(100, 100, Button.x2, False)
            self.pump()

        self.mock_run_macro.assert_called_once()
        args = self.mock_run_macro.call_args[0]
        self.assertEqual(args[0], 0.11)
        self.assertEqual(args[1], config.resolve_hotkey("F"))
        self.assertEqual(args[2], config.resolve_hotkey("ALT"))
        self.assertEqual(args[3], config.resolve_hotkey("A"))
        self.assertFalse(overlay.is_show)
        self.assertFalse(overlay.root.winfo_viewable())
        self.assertIn("[状态] 运行中", release_buffer.getvalue())
        self.assertIn("[状态] 待命", release_buffer.getvalue())

    def test_status_shows_running_while_macro_executes(self):
        buffer = io.StringIO()
        observed = []

        def fake_run_macro(*args, **kwargs):
            observed.append(buffer.getvalue())

        self.mock_run_macro.side_effect = fake_run_macro

        with redirect_stdout(buffer):
            self.app._on_click(100, 100, Button.x2, True)
            self.pump()
            self.app._on_click(100, 100, Button.x2, False)
            self.pump()

        self.assertEqual(len(observed), 1)
        self.assertIn("[状态] 运行中", observed[0])
        self.assertNotIn("[状态] 待命", observed[0])
        self.assertIn("[状态] 待命", buffer.getvalue())


class TestDragChangesWaitTime(IntegrationTestBase):
    """用例 5：拖动改变等待时间，松开按所选时间执行。"""

    def test_drag_up_reduces_wait_time_and_fire_uses_it(self):
        overlay = self.app.overlay
        self.app._on_click(100, 100, Button.x2, True)
        self.pump()

        self.app._on_move(100, 40)
        self.pump()

        self.assertGreater(overlay.result_height, 0)
        wait_text = overlay.label.cget("text")
        self.assertEqual(wait_text, str(int(wait_text)))
        wait_ms = int(wait_text)
        self.assertLess(wait_ms, 110)
        self.assertGreaterEqual(wait_ms, 100)

        self.app._on_click(100, 40, Button.x2, False)
        self.pump()

        self.assertEqual(self.mock_run_macro.call_count, 1)
        seconds = self.mock_run_macro.call_args[0][0]
        self.assertLessEqual(seconds, 0.11)
        self.assertGreaterEqual(seconds, 0.10)

    def test_drag_down_increases_wait_time(self):
        overlay = self.app.overlay
        self.app._on_click(100, 100, Button.x2, True)
        self.pump()

        self.app._on_move(100, 160)
        self.pump()

        self.assertLess(overlay.result_height, 0)
        wait_ms = int(overlay.label.cget("text"))
        self.assertGreater(wait_ms, 110)
        self.assertLessEqual(wait_ms, 140)

        self.app._on_click(100, 160, Button.x2, False)
        self.pump()

        self.assertEqual(self.mock_run_macro.call_count, 1)
        seconds = self.mock_run_macro.call_args[0][0]
        self.assertGreater(seconds, 0.11)
        self.assertLessEqual(seconds, 0.14)


class TestCancel(IntegrationTestBase):
    """用例 6：水平拖出窗口范围 -> 红线 -> 取消执行。"""

    def test_drag_out_turns_red_and_cancels(self):
        overlay = self.app.overlay
        self.app._on_click(0, 0, Button.x2, True)
        self.pump()
        self.app._on_move(100, 0)
        self.pump()
        self.app._on_move(200, 0)
        self.pump()

        self.assertIs(overlay.shooting_state, False)
        self.assertEqual(overlay.canvas.itemcget(overlay.canvas_line, "fill"), "#f00")

        self.app._on_click(200, 0, Button.x2, False)
        self.pump()

        self.assertEqual(self.mock_run_macro.call_count, 0)
        self.assertFalse(overlay.is_show)


class TestNonTriggerIgnored(IntegrationTestBase):
    """用例 7：触发器为鼠标时键盘事件被忽略。"""

    def test_keyboard_key_ignored_when_trigger_is_mouse(self):
        overlay = self.app.overlay
        self.app._on_press(KeyCode.from_char("q"))
        self.pump()
        self.app._on_release(KeyCode.from_char("q"))
        self.pump()

        self.assertFalse(overlay.is_show)
        self.assertFalse(overlay.root.winfo_viewable())
        self.assertEqual(self.mock_run_macro.call_count, 0)

    def test_other_mouse_button_ignored(self):
        self.app._on_click(100, 100, Button.left, True)
        self.pump()
        self.app._on_click(100, 100, Button.left, False)
        self.pump()

        self.assertFalse(self.app.overlay.is_show)
        self.assertEqual(self.mock_run_macro.call_count, 0)

    def test_mouse_move_ignored_when_hidden(self):
        self.app._on_move(800, 600)
        self.pump()

        self.assertFalse(self.app.overlay.is_show)
        self.assertEqual(self.app.overlay.result_height, 0)
        self.assertEqual(self.app.overlay.canvas_line, None)


class TestCooldownReentrancy(IntegrationTestBase):
    """用例 8：成功执行后 1 秒内再次触发被冷却拦截。"""

    def test_second_fire_within_cooldown_is_ignored(self):
        clock = [1000.0]
        with mock.patch.object(main.time, "time", side_effect=lambda: clock[0]):
            self.app._on_click(100, 100, Button.x2, True)
            self.pump()
            self.app._on_click(100, 100, Button.x2, False)
            self.pump()
            self.assertEqual(self.mock_run_macro.call_count, 1)

            clock[0] += 0.5
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                self.app._on_click(100, 100, Button.x2, True)
                self.pump()
                self.assertFalse(self.app.overlay.is_show)
                self.app._on_click(100, 100, Button.x2, False)
                self.pump()
            self.assertEqual(self.mock_run_macro.call_count, 1)
        self.assertNotIn("[状态] 调整中", buffer.getvalue())


class TestKeyboardTrigger(IntegrationTestBase):
    """用例 9：键盘触发按键（含自动重复抑制）。"""

    def test_keyboard_trigger_flow(self):
        overlay = self.app.overlay
        self.app.set_config(dict(config.DEFAULT_CONFIG, trigger_hotkey="T"))
        self.pump()
        self.assertEqual(config.key_to_name(self.app.trigger_spec), "T")
        self.assertIsInstance(self.app.trigger_spec, str)

        # 未按下时 release 不应触发。
        self.app._on_release(KeyCode.from_char("T"))
        self.pump()
        self.assertEqual(self.mock_run_macro.call_count, 0)

        with mock.patch.object(overlay, "init_canvas", wraps=overlay.init_canvas) as spy:
            self.app._on_press(KeyCode.from_char("T"))
            self.pump()
            self.assertIs(overlay.is_show, True)
            self.assertEqual((overlay.mouse_start_x, overlay.mouse_start_y), (50, 50))

            # 按住期间的自动重复事件不应重新初始化。
            self.app._on_press(KeyCode.from_char("T"))
            self.app._on_press(KeyCode.from_char("T"))
            self.pump()
            self.assertEqual(spy.call_count, 1)

        self.app._on_release(KeyCode.from_char("T"))
        self.pump()
        self.assertEqual(self.mock_run_macro.call_count, 1)
        self.assertFalse(overlay.is_show)

        # 此时鼠标侧键不再是触发键。
        self.app._on_click(0, 0, Button.x2, True)
        self.app._on_click(0, 0, Button.x2, False)
        self.pump()
        self.assertEqual(self.mock_run_macro.call_count, 1)


class TestMalformedConfig(IntegrationTestBase):
    """用例 10：config.json 非法时不崩溃并使用默认值。"""

    def test_malformed_config_construction_uses_defaults(self):
        self._write_raw("{ this is not valid json")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            app = self._make_app()

        output = buffer.getvalue()
        self.assertEqual(config.key_to_name(app.trigger_spec), "MOUSE5")
        self.assertIn("触发热键: MOUSE5", output)
        self.assertIn("[状态] 待命", output)
        self.assertEqual(app.trigger_spec, config.resolve_hotkey("MOUSE5"))
        self.assertIn("默认", output)

        app._on_click(100, 100, Button.x2, True)
        app.root.update()
        app.root.update()
        app._on_click(100, 100, Button.x2, False)
        app.root.update()
        app.root.update()
        self.mock_run_macro.assert_called_once()
        self.assertEqual(self.mock_run_macro.call_args[0][0], 0.11)


if __name__ == "__main__":
    unittest.main()
