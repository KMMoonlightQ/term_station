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


@pytest.mark.parametrize("key,character,expected", [
    ("shift+h", None, "H"), ("shift+l", None, "L"), ("shift+z", None, "Z"),
    ("shift+slash", None, "?"), ("shift+question_mark", None, "?"),
    ("shift+1", None, "!"), ("shift+minus", None, "_"),
    ("shift+equals_sign", None, "+"), ("shift+backslash", None, "|"),
    ("shift+space", None, " "),
    # Text supplied by the terminal already reflects the keyboard layout / IME.
    ("shift+2", "\"", "\""), ("shift+h", "h", "h"), ("shift+h", "中", "中"),
    ("alt+b", None, "\x1bb"), ("alt+shift+h", None, "\x1bH"),
    ("shift+alt+h", None, "\x1bH"), ("alt+shift+slash", None, "\x1b?"),
    ("alt+ctrl+h", None, "\x1b\x08"), ("ctrl+alt+h", None, "\x1b\x08"),
    ("ctrl+shift+h", None, "\x08"), ("shift+ctrl+h", None, "\x08"),
    ("ctrl+space", None, "\x00"), ("ctrl+at", None, "\x00"),
    ("ctrl+backspace", None, "\x17"), ("ctrl+backslash", None, "\x1c"),
    ("ctrl+underscore", None, "\x1f"),
    ("shift+f1", None, "\x1b[1;2P"), ("ctrl+f5", None, "\x1b[15;5~"),
    ("alt+shift+f12", None, "\x1b[24;4~"), ("ctrl+delete", None, "\x1b[3;5~"),
    ("alt+pageup", None, "\x1b[5;3~"), ("shift+tab", None, "\x1b[Z"),
    ("left_shift", None, None), ("right_control", None, None),
    ("unknown_key", None, None),
])
def test_modified_shell_key_encoding(key, character, expected):
    assert encode_key(key, character) == expected


@pytest.mark.parametrize("key,expected", [
    ("ctrl+2", "\x00"), ("ctrl+3", "\x1b"), ("ctrl+4", "\x1c"),
    ("ctrl+5", "\x1d"), ("ctrl+6", "\x1e"), ("ctrl+7", "\x1f"), ("ctrl+8", "\x7f"),
    ("ctrl+slash", "\x1f"), ("ctrl+question_mark", "\x7f"), ("ctrl+tilde", "\x1e"),
    ("ctrl+1", "1"), ("ctrl+semicolon", ";"), ("ctrl+minus", "-"),
    ("alt+ctrl+2", "\x1b\x00"), ("ctrl+alt+slash", "\x1b\x1f"),
    ("shift+enter", "\x1b[13;2u"), ("ctrl+enter", "\x1b[13;5u"),
    ("alt+shift+enter", "\x1b[13;4u"), ("ctrl+alt+enter", "\x1b[13;7u"),
    ("ctrl+tab", "\x1b[9;5u"), ("ctrl+shift+tab", "\x1b[9;6u"),
    ("alt+ctrl+shift+tab", "\x1b[9;8u"), ("alt+shift+tab", "\x1b\x1b[Z"),
    ("alt+enter", "\x1b\r"), ("ctrl+escape", "\x1b[27;5u"),
    ("shift+backspace", "\x7f"), ("alt+shift+backspace", "\x1b\x7f"),
    ("alt+ctrl+backspace", "\x1b\x17"),
    ("super+h", "\x1b[104;9u"), ("super+ctrl+h", "\x1b[104;13u"),
    ("super+enter", "\x1b[13;9u"), ("hyper+tab", "\x1b[9;17u"),
    ("meta+up", "\x1b[1;33A"), ("super+f5", "\x1b[15;9~"),
    ("ctrl+é", "\x1b[233;5u"), ("ctrl+ß", "\x1b[223;5u"),
    ("alt+ctrl+ß", "\x1b[223;7u"), ("ctrl+shift+ß", "\x1b[223;6u"),
    ("super+unknown_key", None), ("left_super", None),
])
def test_remaining_control_and_extended_chords(key, expected):
    assert encode_key(key) == expected


@pytest.mark.parametrize("modifiers,number", [
    ("shift", 2), ("alt", 3), ("shift+alt", 4), ("ctrl", 5),
    ("ctrl+shift", 6), ("ctrl+alt", 7), ("ctrl+alt+shift", 8), ("super", 9),
])
def test_all_navigation_and_function_key_modifiers(modifiers, number):
    # Protocol fixtures are independent of the encoder's key tables.
    suffixes = {"up": "A", "down": "B", "right": "C", "left": "D", "home": "H", "end": "F",
                "f1": "P", "f2": "Q", "f3": "R", "f4": "S"}
    tilde_codes = {"insert": 2, "delete": 3, "pageup": 5, "pagedown": 6,
                   "f5": 15, "f6": 17, "f7": 18, "f8": 19, "f9": 20, "f10": 21, "f11": 23, "f12": 24}
    for application_cursor in (False, True):
        for key, suffix in suffixes.items():
            assert encode_key(f"{modifiers}+{key}", application_cursor=application_cursor) == f"\x1b[1;{number}{suffix}"
        for key, code in tilde_codes.items():
            assert encode_key(f"{modifiers}+{key}", application_cursor=application_cursor) == f"\x1b[{code};{number}~"


@pytest.mark.parametrize("modifiers,prefix", [
    ("ctrl", ""), ("ctrl+shift", ""), ("alt+ctrl", "\x1b"), ("shift+ctrl+alt", "\x1b"),
])
def test_control_alphabet_keeps_legacy_shell_bindings(modifiers, prefix):
    # Preserve terminal control bytes (including SIGINT / EOF), and the existing
    # legacy alias of Ctrl+Shift+letter to Ctrl+letter.
    for character, code in zip("abcdefghijklmnopqrstuvwxyz", range(1, 27)):
        assert encode_key(f"{modifiers}+{character}") == prefix + chr(code)


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
