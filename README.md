# Term Station

纯终端工作台：持久 Shell 会话、多个 Tab，以及可以拖拽和调整尺寸的组件 Dashboard。支持 macOS 和 Linux，Windows 可以在 WSL 中运行。

## 启动

需要 Python 3.9 或更新版本，以及支持鼠标和真彩色的终端，例如 iTerm2、WezTerm、Ghostty 或 macOS Terminal。建议窗口至少 100 列 × 32 行。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/term-station
```

也可以通过 `pipx install .` 安装，然后直接运行 `term-station`。在本项目里，已安装依赖后可使用 `./run.sh`。

## 使用

主界面保留组件线框和底部一行命令栏；Tab 数量达到两个时才展示 Tab 栏。默认只有终端和便笺，自定义组件名称放在边框上。

- **Tab**：每个 Tab 有独立名称和组件布局。点击 Tab 或用快捷键切换，按 `Ctrl+B → C` 新建。
- **组件**：按 `Ctrl+B → A` 直接新增 Shell 窗口并自动聚焦。使用当前终端配置的 Shell 和启动目录；没有选中终端时使用工作台的默认值。
- **布局**：拖动组件上边框移动；拖动右下角调整大小。采用 12 列网格，线框贴合窗口，相邻组件各自保留完整边框，释放时吸附，冲突组件自动下移。超出屏幕的 Dashboard 可以滚动。窄窗口或高度不足时临时使用单列展示，保留原始布局；组件高度适应可用窗口，终端提示符不会因组件过高而被截断。
- **终端**：使用真正的 PTY 和登录 Shell，加载原来的 `.zshrc` / `.bashrc` 等 Shell 配置。支持命令、Ctrl+C、补全、颜色、中文、粘贴、历史输出回看和常见全屏程序。
- **保存**：Tab、组件、名称、启动目录、Shell 和便笺自动写入本地 JSON；界面退出后，后台仍保持终端进程、工作目录和环境变量。

### 快捷键

`Ctrl+B` 是前缀键：先按下并松开，再按第二个键，没有时间限制；`Esc` 取消前缀。`Ctrl+B → ?` 打开完整快捷键表，通常第二步按 `Shift+/`，也支持中文问号 `？`。小窗口下可用方向键或 PageUp / PageDown 滚动，Esc 关闭。

进入前缀状态时，底部命令栏显示 `Ctrl+B › 等待命令`；执行命令或取消后清空。命令栏固定占一行，切换状态不会挤动终端内容。

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

布局编辑模式中，方向键移动当前组件，`Shift+方向键` 调整尺寸，`Ctrl+B → Tab` 切换组件，`Esc` 完成。终端中，滚轮或 `Shift+PageUp/PageDown` 回看输出，输入时回到底部。工作台不占用 F1–F12。

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
- `app.py` / `dialogs.py`：Tab、快捷键、自动保存和管理操作。

首版使用内置的轻量复用服务，不依赖或修改 tmux 配置。支持 ANSI / VT 常用序列和备用屏幕，但尚未实现终端图像协议、完整的应用鼠标协议或与 tmux 的协议兼容。可在组件 Shell 中按需运行 tmux。

技术参考：[Textual 文档](https://textual.textualize.io/guide/widgets/)、[pyte API](https://pyte.readthedocs.io/en/latest/api.html)。

## 开发与验证

```sh
.venv/bin/python -m pytest
```

测试覆盖真实 PTY 输入、断开后重连、子进程保活、窗口尺寸、备用屏幕、配置恢复，以及通过 Textual Pilot 模拟的 Tab、键盘布局和鼠标拖拽。测试使用临时状态目录，并停止自己创建的后台服务。
