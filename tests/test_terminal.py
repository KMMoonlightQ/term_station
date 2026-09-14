import pyte
import pytest

from term_station.daemon import TerminalScreen
from term_station.terminal import cell_style, encode_key


@pytest.mark.parametrize("key,character,expected", [
    ("ctrl+c", None, "\x03"), ("ctrl+d", None, "\x04"), ("tab", "\t", "\t"),
    ("up", None, "\x1b[A"), ("ctrl+left", None, "\x1b[1;5D"),
    ("backspace", None, "\x7f"), ("alt+b", "b", "\x1bb"),
    ("中", "中", "中"), ("space", " ", " "),
])
def test_shell_key_encoding(key, character, expected):
    assert encode_key(key, character) == expected


def test_application_cursor_mode():
    assert encode_key("up", application_cursor=True) == "\x1bOA"


def test_alternate_screen_restores_main_screen_and_cursor():
    replies = []
    screen = TerminalScreen(40, 10, replies.append)
    stream = pyte.ByteStream(screen)
    stream.feed(b"main shell\x1b[?1049h\x1b[2J\x1b[Heditor")
    assert screen.alternate
    assert "editor" in screen.display[0]
    stream.feed(b"\x1b[?1049l")
    assert not screen.alternate
    assert screen.display[0].rstrip() == "main shell"
    assert (screen.cursor.x, screen.cursor.y) == (10, 0)
    stream.feed(b"\x1b[6n")
    assert replies == [b"\x1b[1;11R"]


def test_ansi_colors_and_wide_text():
    screen = TerminalScreen(40, 10, lambda _: None)
    pyte.ByteStream(screen).feed("\x1b[31m红色\x1b[0m normal".encode())
    assert screen.buffer[0][0].fg == "red"
    assert screen.cursor.x == 11
    assert cell_style("abcdef", "default", False, False, False, False, False).color.triplet.hex == "#abcdef"


def test_resizing_an_alternate_screen_keeps_restored_shell_usable():
    screen = TerminalScreen(80, 24, lambda _: None)
    stream = pyte.ByteStream(screen)
    stream.feed(b"shell\x1b[?1049h")
    screen.resize(lines=8, columns=30)
    stream.feed(b"editor\x1b[?1049l")
    assert (screen.columns, screen.lines) == (30, 8)
    stream.feed(b"\r\n" * 20 + b"resized shell")
    assert any("resized shell" in line for line in screen.display)


@pytest.mark.parametrize("text,cursor_x,reverse,highlight_start,highlight_width", [
    ("prompt> ", 8, False, 8, 1),
    ("prompt> ", 8, True, 8, 1),
    ("prompt> 中文", 8, False, 8, 2),
    ("prompt> 中文", 9, False, 8, 2),
    ("x" * 20, 20, False, 19, 1),
])
async def test_cursor_overrides_terminal_styles_without_changing_text(text, cursor_x, reverse, highlight_start, highlight_width):
    from textual.app import App
    from textual.widget import Widget
    from term_station.model import Component
    from term_station.terminal import TerminalView

    class FocusTarget(Widget, can_focus=True):
        pass

    class CursorApp(App):
        CSS = "Screen { overflow: hidden hidden; } #other { height: 1; }"

        def compose(self):
            yield TerminalView(Component(kind="terminal"))
            yield FocusTarget(id="other")

    app = CursorApp()
    async with app.run_test(size=(20, 6)) as pilot:
        terminal = app.query_one(TerminalView)
        terminal.focus()
        frame = {"lines": [[[text, "default", "default", False, False, False, reverse, False]]],
                 "offset": 0, "cursor": [cursor_x, 0], "cursor_hidden": False}
        terminal.apply_frame(frame)
        await pilot.pause()
        strip = terminal.render_line(0)
        assert strip.text.rstrip() == text.rstrip()
        assert strip.cell_length == 20
        cursor = list(strip.crop(highlight_start, highlight_start + highlight_width))
        assert all(s.style.bgcolor.triplet.hex == "#8be2cc" and not s.style.reverse for s in cursor)

        frame["cursor_hidden"] = True
        terminal.apply_frame(frame)
        assert not any(s.style.bgcolor.triplet.hex == "#8be2cc" for s in terminal.render_line(0))
        frame["cursor_hidden"] = False
        terminal.apply_frame(frame)
        app.query_one("#other").focus()
        await pilot.pause()
        assert not any(s.style.bgcolor.triplet.hex == "#8be2cc" for s in terminal.render_line(0))
