# Term Station

纯终端工作台：持久 Shell 会话、多个 Tab，以及可以拖拽和调整尺寸的组件 Dashboard。支持 macOS 和 Linux，Windows 可以在 WSL 中运行。

## Homebrew 安装与更新

macOS 14+、Apple Silicon 用户可以直接安装，无需另装 Python：

```sh
brew install KMMoonlightQ/tools/term-station
term-station
```

更新：

```sh
brew update
HOMEBREW_NO_INSTALL_CLEANUP=1 brew upgrade KMMoonlightQ/tools/term-station
```

更新时保留旧版本目录，供仍在运行的后台会话使用。新版鼠标转发需要新版后台；保存工作并结束旧后台后，再启动即可生效。

## 从源码运行

需要 Python 3.9 或更新版本，以及支持鼠标和真彩色的终端，例如 iTerm2、WezTerm、Ghostty 或 macOS Terminal。建议窗口至少 100 列 × 32 行。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/term-station
```

也可以通过 `pipx install .` 安装，然后直接运行 `term-station`。在本项目里，已安装依赖后可使用 `./run.sh`。

## 可执行程序

打包后的 `dist/term-station/` 包含独立的终端程序及其依赖，无需安装 Python 或项目依赖。在终端中运行：

```sh
./dist/term-station/term-station
```

分发时请复制整个 `dist/term-station/` 目录，包括 `_internal/`，不能只复制可执行文件。目录式打包让依赖保留在固定路径，避免每次重新打开都解包并重新加载临时动态库。默认继续使用 `~/.local/state/term-station/` 中的布局和后台会话，`sessions`、`stop --yes`、`--state-dir` 等参数保持一致。

安装为命令：

```sh
./install.sh
```

安装脚本会把完整程序安装到 `~/.local/share/term-station/releases/`，再通过 `~/.local/bin/term-station` 链接启动；自动配置 zsh / bash 的 PATH，无需 sudo。新开终端后直接输入 `term-station`；当前终端可运行 `export PATH="$HOME/.local/bin:$PATH"` 立即生效。已有 Shell 配置会先备份，重复安装不会重复添加 PATH。重新打包后再次运行安装脚本即可更新程序，布局和后台会话保留。每次安装使用独立的依赖目录，旧目录保留供运行中的后台使用；停止所有旧后台后可手动清理不再使用的版本。

重新打包当前源码：

```sh
.venv/bin/python -m pip install -e '.[bundle]'
./build.sh
```

产物对应构建机器的系统和 CPU 架构；在 Apple Silicon Mac 上构建的是 macOS arm64 程序。后台使用已安装的依赖目录，界面离开后仍能接回已有会话。实现遵循 [PyInstaller 独立子进程的要求](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#using-sys-executable-to-spawn-subprocesses-that-outlive-the-application-process-implementing-application-restart)。

## 使用

主界面保留组件线框和底部一行命令栏；Tab 数量达到两个时才展示 Tab 栏。默认只有终端和便笺，Shell 左上角显示当前前台命令，例如空闲时是 `zsh`，运行 `top` 时变为 `top`，退出后恢复。

- **Tab**：每个 Tab 有独立名称和组件布局。点击 Tab 或用快捷键切换，按 `Ctrl+B → C` 新建。
- **组件**：按 `Ctrl+B → A` 直接新增 Shell 窗口并自动聚焦。使用当前终端配置的 Shell 和启动目录；没有选中终端时使用工作台的默认值。
- **布局**：拖动两个组件的交界调整两侧占用比例，横向、纵向及 T 字交界都支持，外侧边界保持不变。交界两侧的边框和相邻一格都可以拖动；悬停会高亮边框，并在支持指针协议的终端中显示对应方向的缩放指针。拖动不与其他组件相接的上边框移动组件，其余独立边缘调整尺寸。采用 12 列网格，线框贴合窗口，相邻组件各自保留完整边框；移动组件时，冲突组件自动下移。超出屏幕的 Dashboard 可以滚动。窄窗口或高度不足时临时使用单列展示，保留原始布局；组件高度适应可用窗口，终端提示符不会因组件过高而被截断。
- **配色**：界面和终端默认文字、背景跟随宿主终端的颜色主题，支持浅色、深色和运行中切换；强调色使用宿主 ANSI 调色板。子程序明确指定的 RGB 颜色保持原样。
- **终端**：使用真正的 PTY 和登录 Shell，加载原来的 `.zshrc` / `.bashrc` 等 Shell 配置。支持命令、Ctrl+C、补全、颜色、中文、粘贴、历史输出回看和常见全屏程序。
- **保存**：Tab、组件、名称、启动目录、Shell 和便笺自动写入本地 JSON；界面退出后，后台仍保持终端进程、工作目录和环境变量。

### 快捷键

`Ctrl+B` 是前缀键：先按下并松开，再按第二个键，没有时间限制；`Esc` 取消前缀。`Ctrl+B → ?` 打开完整快捷键表，通常第二步按 `Shift+/`，也支持中文问号 `？`。小窗口下可用方向键或 PageUp / PageDown 滚动，Esc 关闭。

进入前缀状态时，底部命令栏显示 `Ctrl+B › 等待命令`；执行命令或取消后清空。命令栏固定占一行，切换状态不会挤动终端内容。

前缀后的字母命令不区分大小写；例如 `Ctrl+B → d`、`Ctrl+B → D` 和保持 Ctrl 按住的 `Ctrl+B → Ctrl+D` 都会保存并离开。无法识别的第二个按键会显示提示，便于检查终端的按键编码。

| 操作 | 快捷键 |
| --- | --- |
| 新建 Tab | Ctrl+B → C |
| 下一个 / 上一个 Tab | Ctrl+B → N / P |
| 跳转第 1–9 个 Tab | Ctrl+B → 1–9 |
| 重命名 Tab | Ctrl+B → , |
| 新增 Shell 窗口 | Ctrl+B → A |
| 切换组件焦点 | Ctrl+B → Tab |
| 编辑布局 | Ctrl+B → E |
| 放大 / 还原组件 | Ctrl+B → Z |
| 删除组件 / Tab | Ctrl+B → X / W |
| 重启当前 Shell | Ctrl+B → R |
| 保存并离开 | Ctrl+B → D |
| 帮助 | Ctrl+B → ? |
| 发送 Ctrl+B 给 Shell | Ctrl+B → B，或 Ctrl+B → Ctrl+B |

布局编辑模式中，方向键移动当前组件，`Shift+方向键` 调整尺寸，`Ctrl+B → Tab` 切换组件，`Esc` 完成。终端中的程序启用鼠标支持时，内容区的点击、拖动和滚轮会转发给程序；组件边框仍用于调整布局。普通 Shell 中滚轮回看输出；应用占用滚轮时可用 `Shift+滚轮` 或 `Shift+PageUp/PageDown` 回看历史，输入时回到底部。工作台不占用 F1–F12。

终端组件支持 Shift / Alt / Ctrl 与字母、数字、符号、方向键及 F1–F12 的组合。`Shift+Enter`、`Ctrl+Enter`、`Ctrl+Tab`、`Ctrl+Shift+Tab` 和终端实际送达的 Super / Meta / Hyper 组合按 CSI-u 等扩展序列转发，具体动作需要组件内的程序支持并绑定。普通 Ctrl 组合沿用传统终端控制码，因此 `Ctrl+I` 与 Tab、`Ctrl+M` 与 Enter、`Ctrl+Shift+字母` 与 `Ctrl+字母` 仍可能相同；这不等于完整的 Kitty 键盘协议协商支持。

删除组件或 Tab 会直接执行并结束对应的终端会话。重启 Shell 会显示确认框。**Ctrl+B → D 只离开界面，不结束后台进程。**

## 持久化与后台管理

默认数据目录为 `~/.local/state/term-station/`，可通过 `--state-dir PATH` 或 `TERM_STATION_STATE_DIR` 修改。

```sh
term-station sessions         # 查看后台会话及 PID
term-station stop --yes       # 结束所有后台会话，保留配置
term-station --state-dir ./my-workspace
```

同一个数据目录只允许一个 TUI 界面写入布局，避免同时打开时互相覆盖。不同目录拥有独立的布局和后台服务。

| 场景 | 恢复行为 |
| --- | --- |
| 切换 Tab / 退出后重新打开 TUI | 接回原有 Shell、进程、当前目录、环境和屏幕内容 |
| TUI 意外断开 | 后台会话保持，已保存的布局和便笺可恢复 |
| 停止后台 / 电脑重启 | 恢复布局、便笺、启动目录和 Shell 配置；打开对应 Tab 时创建新会话 |
| Shell 内执行 `exit` | 保留组件与退出状态，用 Ctrl+B → R 创建新 Shell |

这里的“持久化”不会保存重启前的进程内存；重启后不会自动重放历史命令。初始目录保存在组件配置中，运行中的 `cd` 由后台 Shell 保留，后台停止后回到组件的启动目录。

## 实现

```text
Textual TUI  ── Unix socket（仅本用户） ── Python 后台服务
  Tab / Dashboard                         ├─ PTY → 登录 Shell
  终端 / 便笺 / 系统 / 时钟                ├─ PTY → 登录 Shell
         │                               └─ pyte 屏幕与回看缓冲
   workspace.json
```

- `model.py`：版本化配置、原子保存、网格碰撞处理。
- `daemon.py`：PTY 生命周期、终端模拟、Unix socket RPC；不监听网络端口。
- `terminal.py`：终端屏幕绘制、按键和粘贴转发。
- `widgets.py`：组件、拖拽、缩放和网格布局。
- `dividers.py`：相邻组件共享边界的联动缩放。
- `processes.py`：识别 PTY 的前台进程，更新 Shell 标题。
- `app.py` / `dialogs.py`：Tab、快捷键、自动保存和管理操作。

首版使用内置的轻量复用服务，不依赖或修改 tmux 配置。支持 ANSI / VT 常用序列、备用屏幕，以及应用鼠标跟踪（9/1000/1002/1003、SGR 1006 和传统字节编码）。尚未实现终端图像协议、像素鼠标坐标和与 tmux 的完整协议兼容。可在组件 Shell 中按需运行 tmux。

鼠标转发需要新版界面和新版后台同时运行。仅退出再打开界面会接回原有后台；升级前请先保存后台程序的工作，再结束旧后台并重新启动。`stop --yes` 会结束该工作空间的所有终端会话，不能保留正在运行的进程；使用新的 `--state-dir` 可以先验证新版而不影响原有会话。

如果底栏显示“旧后台不支持鼠标转发”，请先保存各程序中的工作，按 `Ctrl+B → D` 退出界面，再运行 `term-station stop --yes`，最后重新启动 `term-station`。使用自定义工作空间时，停止和启动都要带上相同的 `--state-dir PATH`。`Ctrl+B → R` 只重启组件内的 Shell，无法更新后台。

技术参考：[Textual 文档](https://textual.textualize.io/guide/widgets/)、[pyte API](https://pyte.readthedocs.io/en/latest/api.html)。

## 开发与验证

```sh
.venv/bin/python -m pytest
# 打包后验证真实可执行程序的启动、快捷键、退出及会话恢复
TERM_STATION_BUNDLE="$PWD/dist/term-station" .venv/bin/python -m pytest tests/test_bundle.py
```

测试覆盖真实 PTY 输入、断开后重连、子进程保活、窗口尺寸、备用屏幕、配置恢复，以及通过 Textual Pilot 模拟的 Tab、键盘布局和鼠标拖拽。测试使用临时状态目录，并停止自己创建的后台服务。
