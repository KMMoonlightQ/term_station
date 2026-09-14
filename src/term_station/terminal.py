"""Textual viewport for the daemon's VT screen and terminal key encoding."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from rich.cells import cell_len
from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.strip import Strip
from textual.widget import Widget

from .model import Component

KEYS = {
    "enter": "\r", "tab": "\t", "shift+tab": "\x1b[Z", "backspace": "\x7f", "escape": "\x1b",
    "up": "\x1b[A", "down": "\x1b[B", "right": "\x1b[C", "left": "\x1b[D",
    "home": "\x1b[H", "end": "\x1b[F", "insert": "\x1b[2~", "delete": "\x1b[3~",
    "pageup": "\x1b[5~", "pagedown": "\x1b[6~",
    "f1": "\x1bOP", "f2": "\x1bOQ", "f3": "\x1bOR", "f4": "\x1bOS",
    "f5": "\x1b[15~", "f6": "\x1b[17~", "f7": "\x1b[18~", "f8": "\x1b[19~",
    "f9": "\x1b[20~", "f10": "\x1b[21~", "f11": "\x1b[23~", "f12": "\x1b[24~",
}


def encode_key(key: str, character: str | None = None, application_cursor: bool = False) -> str | None:
    if key in KEYS:
        data = KEYS[key]
        if application_cursor and key in ("up", "down", "left", "right", "home", "end"):
            data = data.replace("\x1b[", "\x1bO")
        return data
    parts = key.split("+")
    base = parts[-1]
    modifiers = set(parts[:-1])
    if base in ("up", "down", "left", "right", "home", "end") and modifiers:
        modifier = 1 + ("shift" in modifiers) + 2*("alt" in modifiers) + 4*("ctrl" in modifiers)
        return f"\x1b[1;{modifier}{KEYS[base][-1]}"
    if key in ("ctrl+space", "ctrl+at"):
        return "\x00"
    if key == "ctrl+backspace":
        return "\x17"
    if key.startswith("ctrl+"):
        controls = {"left_square_bracket": "[", "backslash": "\\", "right_square_bracket": "]", "circumflex_accent": "^", "underscore": "_"}
        base = controls.get(base, base)
        if len(base) == 1 and "@" <= base.upper() <= "_":
            return chr(ord(base.upper()) & 31)
    if key.startswith("alt+"):
        inner = encode_key(key[4:], character)
        return "\x1b" + inner if inner else None
    return character


def terminal_color(value: str, default: str) -> str:
    if value == "default":
        return default
    if len(value) == 6 and all(c in "0123456789abcdefABCDEF" for c in value):
        return "#" + value
    return {"brown": "yellow", "brightbrown": "bright_yellow", "brightblack": "bright_black",
            "brightred": "bright_red", "brightgreen": "bright_green", "brightyellow": "bright_yellow",
            "brightblue": "bright_blue", "brightmagenta": "bright_magenta", "brightcyan": "bright_cyan",
            "brightwhite": "bright_white"}.get(value, value)


@lru_cache(maxsize=4096)
def cell_style(fg: str, bg: str, bold: bool, italic: bool, underline: bool, reverse: bool, strike: bool) -> Style:
    return Style(color=terminal_color(fg, "#d8e2ed"), bgcolor=terminal_color(bg, "#101821"),
                 bold=bold, italic=italic, underline=underline, reverse=reverse, strike=strike)


class TerminalView(Widget, can_focus=True, inherit_bindings=False):
    DEFAULT_CSS = """
    TerminalView { width: 1fr; height: 1fr; background: #101821; overflow: hidden hidden; }
    """
    ALLOW_SELECT = False

    def __init__(self, component: Component):
        super().__init__(id=f"terminal-{component.id}")
        self.component = component
        self.command_title = Path(component.shell).name
        self.frame: dict = {}
        self.lines: list[Strip] = []
        self.history_offset = 0
        self.last_offset = 0
        self.last_size = (0, 0)
        self.error = "正在连接 Shell…"
        self.ready = False

    def apply_frame(self, frame: dict) -> None:
        self.frame = frame
        self.last_offset = self.history_offset = frame["offset"]
        self.lines = [Strip([Segment(run[0], cell_style(*run[1:])) for run in line]) for line in frame["lines"]]
        self.error = ""
        self.refresh()

    def render_line(self, y: int) -> Strip:
        background = Style(color="#62768c", bgcolor="#101821")
        if self.error and y == 0:
            return Strip([Segment(self.error, background)]).crop_extend(0, self.size.width, background)
        line = self.lines[y] if y < len(self.lines) else Strip.blank(self.size.width, background)
        line = line.crop_extend(0, self.size.width, background)
        if self.has_focus and self.frame and not self.frame.get("cursor_hidden", True) and self.size.width:
            x, cursor_y = self.frame["cursor"]
            if y == cursor_y:
                # A pending terminal wrap leaves x one cell beyond the right edge.
                x = max(0, min(x, self.size.width - 1))
                column, cursor_width = 0, 1
                for character in line.text:
                    width = cell_len(character)
                    if column <= x < column + width:
                        x, cursor_width = column, width
                        break
                    column += width
                cursor = Strip(Segment.apply_style(
                    line.crop(x, x+cursor_width),
                    post_style=Style(color="#101821", bgcolor="#8be2cc", reverse=False),
                ))
                line = Strip.join([line.crop(0, x), cursor, line.crop(x+cursor_width)])
        return line

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()

    async def send(self, data: str) -> None:
        if not self.ready or self.app.layout_mode:
            return
        self.history_offset = 0
        try:
            await self.app.client.call("input", id=self.component.id, data=data)
        except (ValueError, ConnectionError) as error:
            self.app.notify(str(error), severity="error")

    async def on_key(self, event: events.Key) -> None:
        if self.app.layout_mode:
            return
        if event.key in ("shift+pageup", "shift+pagedown"):
            self.scroll_history(self.size.height * (1 if event.key == "shift+pageup" else -1))
        else:
            data = encode_key(event.key, event.character, self.frame.get("application_cursor", False))
            if data is not None:
                await self.send(data)
        event.stop()
        event.prevent_default()

    async def on_paste(self, event: events.Paste) -> None:
        event.stop()
        event.prevent_default()
        text = event.text
        if self.frame.get("bracketed_paste", False):
            text = "\x1b[200~" + text + "\x1b[201~"
        await self.send(text)

    def scroll_history(self, amount: int) -> None:
        self.history_offset = max(0, min(self.frame.get("history", 0), self.history_offset + amount))

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self.scroll_history(3)
        event.stop()
        event.prevent_default()

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self.scroll_history(-3)
        event.stop()
        event.prevent_default()
