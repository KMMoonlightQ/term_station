from __future__ import annotations

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from .shortcuts import PREFIX_COMMANDS


class FormScreen(ModalScreen):
    BINDINGS = [("escape", "cancel", "取消")]

    def action_cancel(self) -> None:
        self.dismiss(None)


class NameScreen(FormScreen):
    def __init__(self, title: str, initial: str = ""):
        super().__init__()
        self.heading, self.initial = title, initial

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog name-dialog"):
            yield Label(self.heading, classes="dialog-title")
            yield Input(value=self.initial, placeholder="名称", id="name-input", max_length=40)
            with Horizontal(classes="dialog-actions"):
                yield Button("取消", id="cancel")
                yield Button("保存", variant="primary", id="save")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def submit(self) -> None:
        value = self.query_one(Input).value.strip()
        if value:
            self.dismiss(value)
        else:
            self.notify("请输入名称", severity="warning")

    def on_input_submitted(self, _: Input.Submitted) -> None:
        self.submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.submit()
        else:
            self.dismiss(None)


class ConfirmScreen(FormScreen):
    def __init__(self, title: str, message: str):
        super().__init__()
        self.heading, self.message = title, message

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog confirm-dialog"):
            yield Label(self.heading, classes="dialog-title")
            if self.message:
                yield Static(self.message, markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button("取消", id="cancel")
                yield Button("确认", variant="error", id="confirm")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")


class HelpScreen(FormScreen):
    @staticmethod
    def reference() -> Group:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold default", no_wrap=True)
        table.add_column(style="default")
        for _, _, key, description in PREFIX_COMMANDS:
            table.add_row(key, description)
        table.add_row("1–9", "切换到第 1–9 个 Tab")
        return Group(
            Text("Ctrl+B →\n", style="bold cyan"),
            table,
            Text("\n布局：↑↓←→ 移动；Shift+方向键 调整尺寸\n"
                 "终端：Shift+PageUp / Shift+PageDown 回看输出\n"
                 "      Ctrl+C 中断；Ctrl+D 结束输入；Tab 补全\n"
                 "Esc：取消前缀 / 结束布局 / 还原放大 / 关闭弹窗\n"
                 "弹窗：Tab / Shift+Tab 切换，Enter 确认\n"
                 "帮助：↑↓ / PageUp / PageDown 滚动，Home / End 跳转\n"
                 "鼠标：交界调整比例，独立上边框移动，其余边缘调整尺寸\n"
                 "      滚轮回看输出", style="default"),
        )

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog help-dialog"):
            yield Label("快捷键", classes="dialog-title")
            with VerticalScroll(id="help-scroll"):
                yield Static(self.reference(), id="help-content")
            with Horizontal(classes="dialog-actions"):
                yield Button("关闭", id="close-help")

    def on_mount(self) -> None:
        self.query_one("#help-scroll").focus()

    def on_button_pressed(self, _: Button.Pressed) -> None:
        self.dismiss(None)
