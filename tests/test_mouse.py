import shlex
import sys
from unittest.mock import AsyncMock

import pyte
import pytest
from textual._xterm_parser import XTermParser

from term_station.app import TermStation
from term_station.daemon import TerminalScreen
from term_station.model import Component, Workspace
from term_station.mouse import encode_mouse
from term_station.terminal import TerminalView
from term_station.widgets import Dashboard, Panel
from conftest import mouse_input, screen_lines, wait_frame


@pytest.mark.parametrize("action,button,mode,expected", [
    ("down", 0, 1000, b"\x1b[<0;5;6M"),
    ("up", 0, 1000, b"\x1b[<0;5;6m"),
    ("down", 2, 1003, b"\x1b[<2;5;6M"),
    ("scroll", 64, 1000, b"\x1b[<64;5;6M"),
    ("scroll", 65, 1003, b"\x1b[<65;5;6M"),
    ("move", 0, 1002, b"\x1b[<32;5;6M"),
    ("move", -1, 1003, b"\x1b[<35;5;6M"),
    ("move", -1, 1002, b""),
    ("move", 0, 1000, b""),
    ("down", 0, 0, b""),
    ("up", 0, 9, b""),
])
def test_mouse_protocol(action, button, mode, expected):
    assert encode_mouse(action, button, 4, 5, mode, True) == expected


def test_modifiers_legacy_bytes_and_large_coordinates():
    assert encode_mouse("down", 0, 399, 149, 1000, True, True, True, True) == b"\x1b[<28;400;150M"
    assert encode_mouse("down", 0, 120, 100, 1000, False) == b"\x1b[M\x20\x99\x85"
    assert encode_mouse("up", 2, 4, 5, 1000, False) == b"\x1b[M#%&"
    assert encode_mouse("down", 0, 223, 5, 1000, False) == b""


def test_tracking_modes_switch_reset_and_survive_alternate_screen_restore():
    screen = TerminalScreen(80, 24, lambda _: None)
    stream = pyte.ByteStream(screen)
    stream.feed(b"\x1b[?1000h\x1b[?1003h\x1b[?1006h")
    assert screen.mouse_tracking == 1003
    stream.feed(b"\x1b[?1002h")
    assert screen.mouse_tracking == 1002
    stream.feed(b"\x1b[?1049h")
    assert screen.mouse_tracking == 1002
    assert (1006 << 5) in screen.mode
    stream.feed(b"\x1b[?1003h\x1b[?1049l")
    assert screen.mouse_tracking == 1003
    stream.feed(b"\x1b[?1003l")
    assert screen.mouse_tracking == 0


@pytest.mark.parametrize("alternate_mode", [47, 1047, 1049])
@pytest.mark.parametrize("disable_before_restore", [True, False])
def test_wheel_does_not_reach_shell_after_application_exit(alternate_mode, disable_before_restore):
    screen = TerminalScreen(80, 24, lambda _: None)
    stream = pyte.ByteStream(screen)
    stream.feed(b"\x1b[?1000h\x1b[?1006h")
    stream.feed(f"\x1b[?{alternate_mode}h".encode())
    disable = b"\x1b[?1000l\x1b[?1006l"
    restore = f"\x1b[?{alternate_mode}l".encode()
    stream.feed(disable + restore if disable_before_restore else restore + disable)
    assert encode_mouse("scroll", 65, 31, 13, screen.mouse_tracking,
                        (1006 << 5) in screen.mode) == b""
    assert (1006 << 5) not in screen.mode


async def post_mouse(pilot, code, x, y, suffix="M"):
    for event in XTermParser().feed(f"\x1b[<{code};{x+1};{y+1}{suffix}"):
        pilot.app.post_message(event)
    await pilot.pause()


async def test_terminal_mouse_coordinates_capture_wheel_and_layout_drag(tmp_path, monkeypatch):
    workspace = Workspace.default()
    workspace.current.components = [Component(kind="terminal", x=3, y=1, w=6, h=8),
                                    Component(kind="notes", x=0, y=10, w=12, h=3)]
    app = TermStation(tmp_path, workspace)
    monkeypatch.setattr(app, "poll_loop", AsyncMock())
    calls = AsyncMock(return_value={})
    monkeypatch.setattr(app.client, "call", calls)
    async with app.run_test(size=(120, 40)) as pilot:
        terminal = app.query_one(TerminalView)
        terminal.ready = True
        terminal.frame = {"mouse_tracking": 1003, "history": 30}
        x, y = terminal.content_region.x+8, terminal.content_region.y+4
        await mouse_input(pilot, "down", (x, y))
        assert app.mouse_captured is terminal
        await post_mouse(pilot, 32, x+2, y)
        # Release at the panel edge must reach the child, rather than resize the panel.
        await mouse_input(pilot, "up", (terminal.region.right, y))
        assert app.mouse_captured is None
        inputs = [call.kwargs for call in calls.call_args_list if call.args[0] == "mouse"]
        assert [(p["action"], p["button"]) for p in inputs] == [("down", 0), ("move", 0), ("up", 0)]
        assert [(p["x"], p["y"]) for p in inputs] == [(8, 4), (10, 4), (terminal.size.width-1, 4)]
        assert not app.query_one(Dashboard).drag_target
        await post_mouse(pilot, 64, x, y)
        assert calls.call_args.kwargs["button"] == 64
        assert terminal.history_offset == 0
        await post_mouse(pilot, 68, x, y)  # Shift+wheel keeps local scrollback.
        assert terminal.history_offset == 3
        terminal.history_offset = 0
        calls.reset_mock()
        panel = terminal.parent
        await mouse_input(pilot, "down", (panel.region.x+6, panel.region.y))
        assert app.query_one(Dashboard).drag_target is not None
        await mouse_input(pilot, "up", (panel.region.x+6, panel.region.y))
        assert not calls.called
        terminal.frame["mouse_tracking"] = 0
        await mouse_input(pilot, "down", (x, y))
        await mouse_input(pilot, "up", (x, y))
        await post_mouse(pilot, 64, x, y)
        assert terminal.history_offset == 3
        await post_mouse(pilot, 65, x, y)
        assert terminal.history_offset == 0
        assert not calls.called


@pytest.mark.parametrize("alternate", [False, True])
async def test_real_child_receives_click_and_mouse_modes_return_to_shell(service, tmp_path, alternate):
    # The child enables application mouse reporting and prints the exact bytes
    # it receives from Textual -> RPC -> PTY. No mock clipboard or input writer.
    child = tmp_path / "mouse_child.py"
    child.write_text(f"""import os, sys, termios, tty
fd = sys.stdin.fileno()
saved = termios.tcgetattr(fd)
try:
    tty.setraw(fd)
    os.write(1, b'\\x1b[?1000h\\x1b[?1002h\\x1b[?1003h\\x1b[?1006hREADY_MOUSE\\r\\n')
    if {alternate!r}:
        os.write(1, b'\\x1b[?1049h')
    data = bytearray()
    while not data.endswith(b'm'):
        data.extend(os.read(fd, 1))
    os.write(1, b'\\x1b[?1000l\\x1b[?1002l\\x1b[?1003l\\x1b[?1006l')
    if {alternate!r}:
        os.write(1, b'\\x1b[?1049l')
    os.write(1, b'RESULT:' + data.hex().encode() + b'\\r\\n')
finally:
    termios.tcsetattr(fd, termios.TCSANOW, saved)
""")
    app = TermStation(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        terminal = app.query_one(TerminalView)
        for _ in range(60):
            await pilot.pause(.05)
            if terminal.ready:
                break
        command = shlex.join([sys.executable, str(child)]) + "\r"
        await terminal.send(command)
        for _ in range(60):
            await pilot.pause(.05)
            if terminal.frame.get("mouse_tracking") == 1003:
                break
        assert terminal.frame.get("mouse_tracking") == 1003
        x, y = terminal.content_region.x+8, terminal.content_region.y+4
        await mouse_input(pilot, "down", (x, y))
        await post_mouse(pilot, 65, x, y)
        await mouse_input(pilot, "up", (x, y))
        expected = (b"\x1b[<0;9;5M" + b"\x1b[<65;9;5M" + b"\x1b[<0;9;5m").hex()
        frame = await wait_frame(service, terminal.component.id, lambda f: "RESULT:" + expected in screen_lines(f))
        assert frame["mouse_tracking"] == 0
    await app.client.close()


@pytest.mark.parametrize("tracking,shift,offset", [(0, False, 0), (1003, True, 0), (1003, False, 3)])
@pytest.mark.parametrize("backwards", [False, True])
async def test_drag_selects_terminal_text_without_copying(tmp_path, monkeypatch, tracking, shift, offset, backwards):
    app = TermStation(tmp_path)
    monkeypatch.setattr(app, "poll_loop", AsyncMock())
    calls = AsyncMock(return_value={})
    monkeypatch.setattr(app.client, "call", calls)
    async with app.run_test(size=(120, 40)) as pilot:
        terminal = app.query_one(TerminalView)
        terminal.ready = True
        frame = {"lines": [[[text, "default", "default", False, False, False, False, False]]
                           for text in ["prompt> 中文 abc", "second line"]],
                 "offset": offset, "history": 30, "cursor_hidden": True,
                 "mouse_tracking": tracking}
        terminal.apply_frame(frame)
        x, y = terminal.content_region.x, terminal.content_region.y
        start, end = (x+8, y), (x+6, y+1)
        if backwards:
            start, end = end, start
        modifier = 4 if shift else 0
        await post_mouse(pilot, modifier, *start)
        await post_mouse(pilot, 32+modifier, *end)
        assert app.mouse_captured is terminal
        assert all(segment.style.reverse for segment in terminal.render_line(0).crop(8, 12))
        await post_mouse(pilot, modifier, *end, suffix="m")
        assert app.mouse_captured is None
        assert app.clipboard == ""
        assert all(segment.style.reverse for segment in terminal.render_line(0).crop(8, 15))
        assert all(segment.style.reverse for segment in terminal.render_line(1).crop(0, 6))
        assert not any(segment.style.reverse for segment in terminal.render_line(1).crop(6, 11))
        assert not any(call.args[0] == "mouse" for call in calls.call_args_list)
        assert app.query_one(Dashboard).drag_target is None
        await pilot.press("ctrl+c")
        assert calls.call_args.args == ("input",)
        assert calls.call_args.kwargs["data"] == "\x03"


async def test_selection_keeps_drag_text_stable_and_captures_panel_edge(tmp_path, monkeypatch):
    app = TermStation(tmp_path)
    monkeypatch.setattr(app, "poll_loop", AsyncMock())
    async with app.run_test(size=(120, 40)) as pilot:
        terminal = app.query_one(TerminalView)
        frame = {"lines": [[["prompt> 中文 abc", "default", "default", False, False, False, False, False]]],
                 "offset": 0, "history": 30, "cursor_hidden": True}
        terminal.apply_frame(frame)
        app.copy_to_clipboard("previous")
        x, y = terminal.content_region.x, terminal.content_region.y
        await post_mouse(pilot, 0, x+9, y)
        await post_mouse(pilot, 0, x+9, y, suffix="m")
        assert app.clipboard == "previous"  # A click is not a selection.
        await post_mouse(pilot, 0, x+9, y)  # Inside the second cell of 中.
        changed = {**frame, "lines": [[["new output", "default", "default", False, False, False, False, False]]]}
        terminal.apply_frame(changed)
        await post_mouse(pilot, 32, terminal.region.right, y)
        await post_mouse(pilot, 0, terminal.region.right, y, suffix="m")
        assert app.clipboard == "previous"
        assert all(segment.style.reverse for segment in terminal.render_line(0).crop(8, 15))
        assert not any(segment.style.reverse for segment in terminal.render_line(0).crop(0, 8))
        assert app.mouse_captured is None
        assert app.query_one(Dashboard).drag_target is None
        terminal.apply_frame(changed)
        assert terminal.render_line(0).text.rstrip() == "new output"
        assert terminal.selection_start is None
