# -*- coding: utf-8 -*-
"""配置模块：config.json 的路径、读写校验与热键名称解析。"""
import json
import os
import sys
from typing import Any, Dict

from pynput.keyboard import Key
from pynput.mouse import Button

DEFAULT_CONFIG = {
    "trigger_hotkey": "MOUSE5",
    "melee_hotkey": "F",
    "dive_hotkey": "ALT",
    "strafe_hotkey": "A",
    "wait_time": 110,
    "top_wait_time": 100,
    "bottom_wait_time": 140,
}

_HOTKEY_FIELDS = ("trigger_hotkey", "melee_hotkey", "dive_hotkey", "strafe_hotkey")
_NUMBER_FIELDS = ("wait_time", "top_wait_time", "bottom_wait_time")

_NAMES = {
    "MOUSE1": Button.left,
    "MOUSE2": Button.right,
    "MOUSE3": Button.middle,
    "MOUSE4": Button.x1,
    "MOUSE5": Button.x2,
    "ALT": Key.alt,
    "CTRL": Key.ctrl,
    "SHIFT": Key.shift,
    "SPACE": Key.space,
    "ESC": Key.esc,
    "ENTER": Key.enter,
    "TAB": Key.tab,
}
for _index in range(1, 13):
    _NAMES["F%d" % _index] = getattr(Key, "f%d" % _index)
_TO_NAME = {value: name for name, value in _NAMES.items()}


def get_config_path() -> str:
    """打包后返回 exe 同目录的 config.json，否则返回本文件同目录。"""
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "config.json")


def resolve_hotkey(name):
    """人类可读名称 -> pynput Key/Button/小写字符；无法识别返回 None。"""
    if not isinstance(name, str):
        return None
    text = name.strip()
    if not text:
        return None
    upper = text.upper()
    if upper in _NAMES:
        return _NAMES[upper]
    if len(text) == 1 and text.isprintable():
        return text.lower()
    return None


def key_to_name(spec) -> str:
    """resolve_hotkey 的逆操作，用于界面显示；无法识别返回空串。"""
    if isinstance(spec, (Button, Key)):
        return _TO_NAME.get(spec, "")
    if isinstance(spec, str) and len(spec) == 1:
        return spec.upper()
    return ""


def matches_key(pressed, spec) -> bool:
    """事件值（Key/KeyCode/Button）是否匹配 spec；字符比较忽略大小写。"""
    if spec is None or pressed is None:
        return False
    if isinstance(spec, str):
        char = pressed if isinstance(pressed, str) else getattr(pressed, "char", None)
        return isinstance(char, str) and char.lower() == spec.lower()
    return pressed == spec


def load_config() -> Dict[str, Any]:
    """读取 config.json；缺失时创建默认文件，非法字段逐项回退默认值。"""
    path = get_config_path()
    result: Dict[str, Any] = dict(DEFAULT_CONFIG)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as file:
            json.dump(DEFAULT_CONFIG, file, ensure_ascii=False, indent=2)
        return result
    try:
        with open(path, "r", encoding="utf-8") as file:
            raw = json.load(file)
    except (OSError, ValueError) as exc:
        print("[配置] config.json 解析失败，使用默认值: %s" % (exc,))
        return result
    if not isinstance(raw, dict):
        print("[配置] config.json 顶层不是对象，使用默认值")
        return result
    for key in _HOTKEY_FIELDS:
        value = raw.get(key, DEFAULT_CONFIG[key])
        if resolve_hotkey(value) is None:
            print("[配置] 无效热键 %s=%r，已回退默认值 %s" % (key, value, DEFAULT_CONFIG[key]))
        else:
            result[key] = value
    for key in _NUMBER_FIELDS:
        value = raw.get(key, DEFAULT_CONFIG[key])
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            print("[配置] %s=%r 无效（需为正数毫秒），已回退默认值 %s" % (key, value, DEFAULT_CONFIG[key]))
        else:
            result[key] = int(round(value))
    return result
