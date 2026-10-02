# 绝地潜兵2飞扑肘击宏（Helldivers 2 Dive-Elbow Macro）

> **关于仓库名**：`juediqianbing2-feipu-zhouji-hong` 是原名称「**绝地潜兵2飞扑肘击宏**」的拼音转写——GitHub 仓库名只允许 ASCII 字母、数字与 `-` `_` `.`，无法直接使用中文。
> *Pinyin transliteration of the original Chinese name "绝地潜兵2飞扑肘击宏" (Helldivers 2 Dive-Elbow Macro), since GitHub repository names allow ASCII characters only.*

复刻《绝地潜兵2》「肘击飞扑通牒」鼠标宏的极简独立程序（Windows / Python 3.8），只保留该鼠标宏，其余功能全部丢弃。

## 功能

- **按住触发热键**（默认 `MOUSE5` 鼠标上侧键）→ 屏幕中央出现高度调整窗口；**上下移动鼠标**实时调整射击延迟（100–140 ms），**水平移出窗口**即取消 → **松开自动执行发射**
- `config.json`（首次运行自动生成在 exe 同目录）配置触发热键与游戏按键：
  - `trigger_hotkey` 触发热键：`T`、`MOUSE4`、`MOUSE5`、`MOUSE1`~`MOUSE3`、`ALT`/`CTRL`/`SHIFT`/`SPACE`/`ESC`/`ENTER`/`TAB`、`F1`~`F12`
  - `melee_hotkey` / `dive_hotkey` / `strafe_hotkey`：近战 / 飞扑 / 横移键（默认 `F` / `ALT` / `A`）
  - `wait_time` / `top_wait_time` / `bottom_wait_time`：延迟毫秒（默认 `110` / `100` / `140`）
- 控制台输出运行状态；仅当标题含 `HELLDIVERS` 的窗口处于前台时响应
- 退出：`Ctrl+C` 或关闭控制台窗口

## 下载使用

1. 前往 [Releases](../../releases) 下载 `肘击飞扑通牒宏.exe`
2. 放到任意目录运行（首次运行自动在同目录生成 `config.json`）
3. 编辑 `config.json` 后**重启程序**生效
4. 进入游戏（游戏窗口在前台），按住触发键 → 上下拖动调整 → 松开自动发射
5. 若游戏以管理员身份运行，请对本程序右键「以管理员身份运行」

## 目录结构

```
main.py            # 入口：隐藏 Tk 根窗口 + 浮窗交互 + 全局键鼠监听 + 控制台输出
config.py          # config.json 生成/校验 + 热键名 ↔ 键映射
macro.py           # 宏序列（逐字复刻）+ 延迟插值
tests/             # 单元/集成测试（60 用例）+ 低层事件记录器
requirements.txt   # 依赖（pynput、pywin32）
ass.ico            # 图标（多尺寸 16–256）
```

## 从源码运行 / 打包

```powershell
python -m pip install -r requirements.txt

python main.py                                # 直接运行
python -m unittest discover -s tests -v       # 运行测试（60 用例）

# 打包为单文件 exe
python -m PyInstaller --noconfirm --onefile --console --name 肘击飞扑通牒宏 `
  --icon ass.ico --collect-submodules pynput `
  --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 main.py
```

## 说明

- 触发交互与原版一致：按住触发键显示调整窗、上下拖动改延迟、左右拖出取消、松开发射；取消不进入 1 秒冷却
- 仅供学习与单机使用，请遵守游戏服务条款
