from __future__ import annotations

import platform
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psutil
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import ScrollableContainer
from textual.geometry import Offset, Region, Size, Spacing
from textual.layout import Layout, WidgetPlacement
from textual.widget import Widget
from textual.widgets import Static, TextArea

from .dividers import Divider
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


@dataclass
class DragTarget:
    panel: Panel
    kind: str
    divider: Divider | None = None

    @property
    def pointer(self) -> str:
        return {"move": "grab", "x": "ew-resize", "y": "ns-resize",
                "e": "ew-resize", "w": "ew-resize", "s": "ns-resize",
                "se": "nwse-resize", "sw": "nesw-resize"}[self.kind]


class Dashboard(ScrollableContainer):
    HOVER_HIGHLIGHT_DELAY = 0.12

    def __init__(self):
        super().__init__(id="dashboard")
        self.dashboard_layout = DashboardLayout()
        self.drag_target: DragTarget | None = None
        self.hovered: list[Panel] = []
        self.hover_pointer = "default"
        self.hover_key = None
        self.hover_highlighted = False
        self.hover_timer = None

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

    def drag_target_at(self, x: int, y: int) -> DragTarget | None:
        if self.app.zoomed or self.compact or not self.region.contains(x, y):
            return None
        panels = [p for p in self.children if p.display]
        if len(panels) < 2:
            return None
        candidates = []
        for before in panels:
            a, ar = before.component, before.region
            for after in panels:
                if before is after:
                    continue
                b, br = after.component, after.region
                if a.x+a.w == b.x and ar.right == br.x and max(ar.y, br.y) <= y < min(ar.bottom, br.bottom):
                    # Both border columns plus one inner column on each side.
                    distance = abs(x - (br.x - 0.5))
                    if distance <= 1.5:
                        candidates.append((distance, "x", before, after))
                if a.y+a.h == b.y and ar.bottom == br.y and max(ar.x, br.x) <= x < min(ar.right, br.right):
                    distance = abs(y - (br.y - 0.5))
                    if distance <= 1.5:
                        candidates.append((distance, "y", before, after))
        if candidates:
            _, axis, before, after = min(candidates, key=lambda c: c[0])
            return DragTarget(before, axis, Divider(axis, before.component, after.component,
                                                    [p.component for p in panels]))
        for panel in reversed(panels):
            region = panel.region
            if not region.contains(x, y):
                continue
            # The title / top border is the move handle. Other free edges resize.
            if y == region.y:
                return DragTarget(panel, "move")
            west, east = x <= region.x+1, x >= region.right-2
            south = y >= region.bottom-2
            if south:
                return DragTarget(panel, "sw" if west else "se" if east else "s")
            if west or east:
                return DragTarget(panel, "w" if west else "e")
        return None

    @staticmethod
    def drag_target_key(target: DragTarget | None):
        if target is None:
            return None
        members = target.divider.members if target.divider else [target.panel.component]
        return target.kind, tuple(sorted(component.id for component in members))

    def cancel_hover_timer(self) -> None:
        if self.hover_timer is not None:
            self.hover_timer.stop()
            self.hover_timer = None

    def show_drag_target(self, target: DragTarget | None, dragging: bool = False,
                         highlight: bool = True) -> None:
        self.cancel_hover_timer()
        key = self.drag_target_key(target)
        ids = {c.id for c in target.divider.members} if target and target.divider else {target.panel.component.id} if target else set()
        hovered = [p for p in self.children if p.component.id in ids]
        pointer = ("grabbing" if dragging and target.kind == "move" else target.pointer) if target else "default"
        highlighted = bool(target and (highlight or dragging))
        # A press can arrive before the terminal reports any hover position.
        # Keep the resize pointer while mouse capture carries us off the edge.
        self.screen.styles.pointer = pointer if dragging else None
        if (key == self.hover_key and hovered == self.hovered and pointer == self.hover_pointer
                and highlighted == self.hover_highlighted):
            self.screen.update_pointer_shape()
            return
        for panel in self.hovered:
            panel.remove_class("edge-hover")
            panel.styles.pointer = None
            for child in panel.children:
                child.styles.pointer = None
        for panel in hovered:
            if highlighted:
                panel.add_class("edge-hover")
            panel.styles.pointer = pointer
            for child in panel.children:
                child.styles.pointer = pointer
        self.hovered, self.hover_pointer = hovered, pointer
        self.hover_key, self.hover_highlighted = key, highlighted
        self.screen.update_pointer_shape()

    def hover_drag_target(self, target: DragTarget | None) -> None:
        key = self.drag_target_key(target)
        if key == self.hover_key:
            return
        self.show_drag_target(target, highlight=False)
        if target is not None:
            def show_highlight() -> None:
                self.hover_timer = None
                if self.drag_target is None and self.hover_key == key:
                    self.show_drag_target(target)

            self.hover_timer = self.set_timer(self.HOVER_HIGHLIGHT_DELAY, show_highlight)

    def handle_mouse(self, event: events.MouseEvent) -> bool:
        x, y = event.screen_x, event.screen_y
        if isinstance(event, events.MouseMove):
            if self.drag_target:
                self.move_drag(x, y)
                return True
            self.hover_drag_target(self.drag_target_at(x, y))
        elif isinstance(event, events.MouseDown) and event.button == 1:
            target = self.drag_target_at(x, y)
            if target:
                self.drag_target = target
                panel = target.panel
                panel.focus_content(scroll_visible=False)
                panel.dragging = True
                panel.resizing = target.kind != "move"
                panel.drag_origin = (x, y)
                panel.drag_step = self.grid_step
                item = panel.component
                panel.start_position = (item.x, item.y, item.w, item.h)
                if target.kind == "move":
                    panel.add_class("dragging")
                panel.capture_mouse()
                self.show_drag_target(target, dragging=True)
                return True
        elif isinstance(event, events.MouseUp) and event.button == 1 and self.drag_target:
            self.move_drag(x, y)
            self.finish_drag()
            self.hover_drag_target(self.drag_target_at(x, y))
            return True
        return False

    def move_drag(self, mouse_x: int, mouse_y: int) -> None:
        target = self.drag_target
        panel = target.panel
        dx = round((mouse_x - panel.drag_origin[0]) / panel.drag_step[0])
        dy = round((mouse_y - panel.drag_origin[1]) / panel.drag_step[1])
        if target.divider:
            target.divider.resize(dx if target.kind == "x" else dy)
        else:
            x, y, w, h = panel.start_position
            item = panel.component
            if target.kind == "move":
                item.x, item.y = max(0, min(12-w, x+dx)), max(0, min(1000, y+dy))
            else:
                if "e" in target.kind:
                    item.w = max(3, min(12-x, w+dx))
                if "w" in target.kind:
                    item.x = max(0, min(x+w-3, x+dx))
                    item.w = x+w-item.x
                if "s" in target.kind:
                    item.h = max(3, min(40, h+dy))
        self.reflow()

    def finish_drag(self, save: bool = True) -> None:
        target = self.drag_target
        if target is None:
            return
        panel = target.panel
        panel.dragging = False
        panel.release_mouse()
        panel.remove_class("dragging")
        if not target.divider:
            item = panel.component
            self.app.workspace.current.place(item, item.x, item.y)
        self.drag_target = None
        self.show_drag_target(None)
        self.reflow()
        if save:
            self.app.save_workspace()


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
        self.border_title = Text(Path(component.shell).name if component.kind == "terminal"
                                 else component.title if component.title not in DEFAULT_TITLES else "")
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
            if self.border_title != terminal.command_title:
                self.border_title = Text(terminal.command_title)
            if terminal.frame and not terminal.frame.get("alive", True):
                subtitle = f"exit {terminal.frame.get('exit_code')}"
            elif terminal.history_offset:
                subtitle = f"↑{terminal.history_offset}"
        if self.border_subtitle != subtitle:
            self.border_subtitle = subtitle

    def on_focus(self) -> None:
        self.app.active_panel = self.component.id

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self.app.active_panel = self.component.id

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self.component.text = event.text_area.text
        self.app.queue_save()
