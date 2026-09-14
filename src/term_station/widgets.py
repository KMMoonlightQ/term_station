from __future__ import annotations

import platform
from datetime import datetime

import psutil
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import ScrollableContainer
from textual.geometry import Offset, Region, Size, Spacing
from textual.layout import Layout, WidgetPlacement
from textual.widget import Widget
from textual.widgets import Static, TextArea

from .model import Component
from .terminal import TerminalView

DEFAULT_TITLES = {"终端", "主终端", "便笺", "随手记", "系统状态", "时钟"}


class DashboardLayout(Layout):
    name = "dashboard"

    def arrange(self, parent: Widget, children: list[Widget], size: Size, greedy: bool = True):
        placements = []
        zoomed = parent.app.zoomed
        narrow_y = 0
        panels = list(parent.children)
        rows = max((child.component.y + child.component.h for child in panels), default=1)
        canvas_height = max(size.height, rows * 2)
        for widget in children:
            item = widget.component
            if zoomed or len(panels) == 1:
                if zoomed and item.id != zoomed:
                    continue
                region = Region(0, 0, size.width, size.height)
            elif size.width < 72 or size.height < 16:
                # Small terminals remain usable, without rewriting the saved desktop layout.
                height = size.height if item.kind == "terminal" else min(item.h * 2 + 1, max(6, size.height))
                region = Region(0, narrow_y, size.width, height)
                narrow_y += height
            else:
                # Each component owns both edges; adjacent border cells never overlap.
                x = item.x * size.width // 12
                right = (item.x + item.w) * size.width // 12
                y = item.y * canvas_height // rows
                bottom = (item.y + item.h) * canvas_height // rows
                region = Region(x, y, max(1, right-x), min(bottom-y, max(6, size.height)))
            placements.append(WidgetPlacement(region, Offset(), Spacing(), widget))
        return placements


class Dashboard(ScrollableContainer):
    def __init__(self):
        super().__init__(id="dashboard")
        self.dashboard_layout = DashboardLayout()

    @property
    def layout(self) -> Layout:
        return self.dashboard_layout

    def reflow(self) -> None:
        # Textual invalidates a parent's arrangement when a child asks for layout.
        # Grid coordinates live in our model, outside Textual's style reactives.
        for child in self.children:
            child.refresh(layout=True)
        self.refresh(layout=True)

    @property
    def compact(self) -> bool:
        return self.content_size.width < 72 or self.content_size.height < 16

    @property
    def grid_step(self) -> tuple[float, float]:
        rows = max((child.component.y + child.component.h for child in self.children), default=1)
        return (max(1, self.content_size.width)/12,
                max(self.content_size.height, rows*2)/rows)


class SystemView(Static):
    def on_mount(self) -> None:
        psutil.cpu_percent()
        self.set_interval(2, self.refresh)

    def render(self) -> Text:
        cpu = psutil.cpu_percent()
        memory = psutil.virtual_memory()
        width = max(3, min(14, self.size.width - 18))

        def bar(percent):
            filled = round(width * percent / 100)
            return "━" * filled + "╺" * (width-filled)

        text = Text()
        text.append(f"CPU  {bar(cpu)} {cpu:4.0f}%\n", "#69d5bd")
        text.append(f"RAM  {bar(memory.percent)} {memory.percent:4.0f}%\n", "#90aefa")
        text.append(f"{memory.used/1024**3:.1f} / {memory.total/1024**3:.1f} GB\n", "#7890a6")
        text.append(f"{platform.system()} · {platform.machine()}", "#7890a6")
        return text


class ClockView(Static):
    def on_mount(self) -> None:
        self.set_interval(1, self.refresh)

    def render(self) -> Text:
        now = datetime.now().astimezone()
        return Text.assemble((now.strftime("%H:%M:%S\n"), "bold #69d5bd"),
                             (now.strftime("%Y.%m.%d\n"), "#bacbdc"),
                             (f"{'一二三四五六日'[now.weekday()]} · {now.tzname()}", "#7890a6"))


class NotesView(TextArea):
    def __init__(self, component: Component):
        super().__init__(component.text, soft_wrap=True, show_line_numbers=False, tab_behavior="indent",
                         id=f"notes-{component.id}", highlight_cursor_line=False)


class Panel(Widget, can_focus=True):
    def __init__(self, component: Component):
        super().__init__(id=f"panel-{component.id}")
        self.component = component
        self.border_title = Text(component.title if component.title not in DEFAULT_TITLES else "")
        self.dragging = False
        self.resizing = False
        self.drag_origin = (0, 0)
        self.drag_step = (1.0, 1.0)
        self.start_position = (0, 0, 0, 0)

    def compose(self) -> ComposeResult:
        if self.component.kind == "terminal":
            yield TerminalView(self.component)
        elif self.component.kind == "notes":
            yield NotesView(self.component)
        elif self.component.kind == "system":
            yield SystemView(classes="info-body")
        else:
            yield ClockView(classes="info-body")

    def focus_content(self, scroll_visible: bool = True) -> None:
        self.app.active_panel = self.component.id
        if self.app.layout_mode or self.component.kind in ("system", "clock"):
            self.focus(scroll_visible=scroll_visible)
        elif self.component.kind == "terminal":
            self.query_one(TerminalView).focus(scroll_visible=scroll_visible)
        else:
            self.query_one(NotesView).focus(scroll_visible=scroll_visible)

    def update_state(self) -> None:
        subtitle = ""
        if self.component.kind == "terminal":
            terminal = self.query_one(TerminalView)
            if terminal.frame and not terminal.frame.get("alive", True):
                subtitle = f"exit {terminal.frame.get('exit_code')}"
            elif terminal.history_offset:
                subtitle = f"↑{terminal.history_offset}"
        if self.border_subtitle != subtitle:
            self.border_subtitle = subtitle

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 1 or self.app.zoomed:
            return
        self.resizing = event.screen_y == self.region.bottom - 1 and event.screen_x >= self.region.right - 3
        if not self.resizing and event.screen_y != self.region.y:
            return
        self.focus_content(scroll_visible=False)
        if self.parent.compact:
            return
        item = self.component
        self.dragging = True
        self.drag_origin = (event.screen_x, event.screen_y)
        self.drag_step = self.parent.grid_step
        self.start_position = (item.x, item.y, item.w, item.h)
        self.capture_mouse()
        self.add_class("dragging")
        event.stop()
        event.prevent_default()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self.dragging:
            return
        dx = round((event.screen_x - self.drag_origin[0]) / self.drag_step[0])
        dy = round((event.screen_y - self.drag_origin[1]) / self.drag_step[1])
        x, y, w, h = self.start_position
        item = self.component
        if self.resizing:
            item.w = max(3, min(12-x, w+dx))
            item.h = max(3, min(40, h+dy))
        else:
            item.x = max(0, min(12-w, x+dx))
            item.y = max(0, min(1000, y+dy))
        self.parent.reflow()
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self.dragging:
            self.dragging = False
            self.release_mouse()
            self.remove_class("dragging")
            item = self.component
            self.app.workspace.current.place(item, item.x, item.y)
            self.parent.reflow()
            self.app.save_workspace()
            event.stop()

    def on_focus(self) -> None:
        self.app.active_panel = self.component.id

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self.app.active_panel = self.component.id

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self.component.text = event.text_area.text
        self.app.queue_save()
