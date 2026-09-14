"""Workbench commands shared by key bindings and the help panel."""

# key, action, displayed key, description
PREFIX_COMMANDS = (
    ("c", "new_tab", "C", "新建 Tab"),
    ("n", "next_tab", "N", "下一个 Tab"),
    ("p", "previous_tab", "P", "上一个 Tab"),
    ("comma", "rename_tab", ",", "重命名 Tab"),
    ("a", "add_component", "A", "新增 Shell 窗口"),
    ("tab", "next_panel", "Tab", "切换组件焦点"),
    ("e", "layout", "E", "进入 / 结束布局编辑"),
    ("z", "zoom", "Z", "放大 / 还原组件"),
    ("x", "remove_component", "X", "删除当前组件"),
    ("w", "remove_tab", "W", "删除当前 Tab"),
    ("r", "restart_shell", "R", "重启当前 Shell"),
    ("b", "send_prefix", "B / Ctrl+B", "发送 Ctrl+B 给 Shell"),
    ("d", "detach", "D", "保存并离开，保留后台会话"),
    ("question_mark", "help", "?", "查看全部快捷键"),
)
PREFIX_ACTIONS = {key: action for key, action, _, _ in PREFIX_COMMANDS}
# Depending on the input method and terminal keyboard protocol, the same
# question-mark gesture may arrive with a different key name.
PREFIX_ALIASES = {key: "question_mark" for key in (
    "fullwidth_question_mark", "shift+question_mark", "shift+slash",
    "ctrl+question_mark", "ctrl+shift+question_mark", "ctrl+shift+slash", "ctrl+underscore",
)}
# Prefix commands describe letters, independent of case. Accept a still-held
# Control key on the second stroke as well (Ctrl+B, Ctrl+D).
for command in PREFIX_ACTIONS:
    if len(command) == 1:
        for letter in (command, command.upper()):
            for modifiers in ("", "shift+", "ctrl+", "ctrl+shift+", "shift+ctrl+"):
                PREFIX_ALIASES[modifiers + letter] = command
PREFIX_KEYS = [*PREFIX_ACTIONS, *"123456789"]
MODIFIER_KEYS = {f"{side}_{modifier}" for side in ("left", "right")
                 for modifier in ("shift", "control", "alt", "super", "hyper", "meta")} | {
    "iso_level3_shift", "iso_level5_shift",
}
LAYOUT_KEYS = ["up", "down", "left", "right", "shift+up", "shift+down", "shift+left", "shift+right"]
