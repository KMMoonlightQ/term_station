"""Textual viewport for the daemon's VT screen and terminal key encoding."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from rich.cells import cell_len
from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.keys import REPLACED_KEYS, key_to_character
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
# US-layout fallback only when the terminal did not supply the resulting text.
SHIFTED_CHARACTERS = str.maketrans("`1234567890-=[]\\;',./", '~!@#$%^&*()_+{}|:"<>?')
MODIFIER_BITS = {"shift": 1, "alt": 2, "ctrl": 4, "super": 8, "hyper": 16, "meta": 32}
CONTROL_KEY_CODES = {"enter": 13, "tab": 9, "backspace": 127, "escape": 27}
CONTROL_KEY_NAMES = {chr(code): name for name, code in CONTROL_KEY_CODES.items()}
# Legacy terminal Ctrl mappings beyond the alphabet and ASCII @ through _.
# https://sw.kovidgoyal.net/kitty/keyboard-protocol/#legacy-ctrl-mapping-of-ascii-keys
CONTROL_CHARACTERS = {" ": 0, "2": 0, "3": 27, "4": 28, "5": 29, "6": 30,
                      "7": 31, "8": 127, "/": 31, "?": 127, "~": 30}


def encode_extended_key(base: str, modifier: int) -> str | None:
    """Keep modifiers on keys with no suitable legacy byte sequence."""
    code = CONTROL_KEY_CODES.get(base)
    if code is None:
        character = key_to_character(REPLACED_KEYS.get(base, base))
        if character is None or len(character) != 1 or not character.isprintable():
            return None
        code = ord(character)
    return f"\x1b[{code};{modifier}u"


def encode_key(key: str, character: str | None = None, application_cursor: bool = False) -> str | None:
    if key in KEYS:
        data = KEYS[key]
        if application_cursor and key in ("up", "down", "left", "right", "home", "end"):
            data = data.replace("\x1b[", "\x1bO")
        return data
    parts = key.split("+")
    # xterm modifyOtherKeys may arrive as ctrl+\r rather than ctrl+enter.
    base = CONTROL_KEY_NAMES.get(parts[-1], parts[-1])
    modifiers = set(parts[:-1])
    if modifiers - MODIFIER_BITS.keys():
        return character
    modifier = 1 + sum(MODIFIER_BITS[name] for name in modifiers)
    if modifiers and base in KEYS:
        if base in ("up", "down", "left", "right", "home", "end", "f1", "f2", "f3", "f4"):
            return f"\x1b[1;{modifier}{KEYS[base][-1]}"
        if KEYS[base].endswith("~"):
            return f"{KEYS[base][:-1]};{modifier}~"
    if modifiers & {"super", "hyper", "meta"}:
        return encode_extended_key(base, modifier)
    if (base in ("enter", "tab", "escape") and "ctrl" in modifiers
            or base == "enter" and "shift" in modifiers):
        # Preserve the whole chord, including Alt, in a single CSI-u sequence.
        return encode_extended_key(base, modifier)
    if base in KEYS:
        data = KEYS["shift+tab" if base == "tab" and "shift" in modifiers else base]
        if base == "backspace" and "ctrl" in modifiers:
            data = "\x17"  # Retain the workbench's existing delete-word binding.
    elif "ctrl" in modifiers:
        value = key_to_character(REPLACED_KEYS.get(base, base))
        if value is None or len(value) != 1 or not " " <= value <= "~":
            return encode_extended_key(base, modifier)
        if "a" <= value <= "z" or "@" <= value <= "_":
            data = chr(ord(value) & 31)
        else:
            data = chr(CONTROL_CHARACTERS.get(value, ord(value)))
    else:
        # Kitty / CSI-u may omit text. Prefer supplied text for layouts / IMEs.
        data = character
        if data is None:
            data = key_to_character(REPLACED_KEYS.get(base, base))
            if data is not None and "shift" in modifiers:
                data = data.translate(SHIFTED_CHARACTERS).upper()
    if data is not None and "alt" in modifiers:
        data = "\x1b" + data
    return data


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
    return Style(color=terminal_color(fg, "default"), bgcolor=terminal_color(bg, "default"),
                 bold=bold, italic=italic, underline=underline, reverse=reverse, strike=strike)


class TerminalView(Widget, can_focus=True, inherit_bindings=False):
    DEFAULT_CSS = """
    TerminalView { width: 1fr; height: 1fr; background: ansi_default; color: ansi_default; overflow: hidden hidden; }
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
        self.mouse_buttons: set[int] = set()

    def apply_frame(self, frame: dict) -> None:
        self.frame = frame
        self.last_offset = self.history_offset = frame["offset"]
        self.lines = [Strip([Segment(run[0], cell_style(*run[1:])) for run in line]) for line in frame["lines"]]
        self.error = ""
        self.refresh()

    def render_line(self, y: int) -> Strip:
        background = Style(color="default", bgcolor="default")
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
                cursor = Strip([
                    Segment(segment.text, (segment.style or Style()) +
                            Style(reverse=not (segment.style and segment.style.reverse)))
                    for segment in line.crop(x, x+cursor_width)
                ])
                line = Strip.join([line.crop(0, x), cursor, line.crop(x+cursor_width)])
        return line

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()

    @property
    def application_mouse(self) -> bool:
        return bool(self.ready and self.frame.get("mouse_tracking") and not self.history_offset
                    and not self.app.layout_mode)

    async def send_mouse(self, event: events.MouseEvent, action: str, button: int) -> None:
        # Textual coordinates include panel borders and dashboard scrolling.
        region = self.content_region
        x = max(0, min(int(event.screen_x) - region.x, self.size.width - 1))
        y = max(0, min(int(event.screen_y) - region.y, self.size.height - 1))
        try:
            await self.app.client.call("mouse", id=self.component.id, action=action, button=button,
                                       x=x, y=y, shift=event.shift, alt=event.meta, ctrl=event.ctrl)
        except (ValueError, ConnectionError) as error:
            self.app.notify(str(error), severity="error")
        event.stop()
        event.prevent_default()

    async def on_mouse_down(self, event: events.MouseDown) -> None:
        if self.application_mouse and event.button in (1, 2, 3):
            self.mouse_buttons.add(event.button)
            self.capture_mouse()
            self.focus()
            await self.send_mouse(event, "down", event.button - 1)

    async def on_mouse_up(self, event: events.MouseUp) -> None:
        if event.button not in self.mouse_buttons:
            return
        self.mouse_buttons.discard(event.button)
        if not self.mouse_buttons:
            self.release_mouse()
        if self.application_mouse:
            await self.send_mouse(event, "up", event.button - 1)

    async def on_mouse_move(self, event: events.MouseMove) -> None:
        tracking = self.frame.get("mouse_tracking", 0)
        if self.application_mouse and (tracking == 1003 or tracking == 1002 and self.mouse_buttons):
            button = min(self.mouse_buttons) - 1 if self.mouse_buttons else -1
            await self.send_mouse(event, "move", button)

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

    async def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if self.application_mouse and not event.shift:
            await self.send_mouse(event, "scroll", 64)
        else:
            self.scroll_history(3)
        event.stop()
        event.prevent_default()

    async def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if self.application_mouse and not event.shift:
            await self.send_mouse(event, "scroll", 65)
        else:
            self.scroll_history(-3)
        event.stop()
        event.prevent_default()
