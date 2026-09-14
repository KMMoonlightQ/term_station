import shlex
import sys
from unittest.mock import Mock

import pytest
from textual._xterm_parser import XTermParser
from textual.widgets import Input, Tabs

from term_station.app import TermStation
from term_station.dialogs import ConfirmScreen, HelpScreen, NameScreen
from term_station.model import Component, Workspace, WorkspaceStore
from term_station.terminal import TerminalView
from term_station.widgets import Dashboard, NotesView, Panel
from conftest import mouse_input, screen_lines, wait_frame


async def ready(app, pilot):
    for _ in range(40):
        await pilot.pause(0.05)
        views = list(app.query(TerminalView))
        if app.connected and all(v.frame for v in views):
            return
    raise AssertionError("TUI 未连接终端")


async def test_old_daemon_warns_once_keeps_shell_usable_and_clears_after_upgrade(service, tmp_path, monkeypatch):
    app = TermStation(tmp_path)
    original_call = app.client.call
    legacy = True

    async def call(operation, **parameters):
        response = await original_call(operation, **parameters)
        if legacy and operation == "snapshot":
            for frame in response["frames"]:
                frame.pop("mouse_tracking")
        return response

    monkeypatch.setattr(app.client, "call", call)
    notify = Mock()
    monkeypatch.setattr(app, "notify", notify)
    async with app.run_test(size=(120, 38)) as pilot:
        await ready(app, pilot)
        bar = app.query_one("#command-bar")
        assert "旧后台不支持鼠标转发" in bar.render_line(0).text
        notify.assert_called_once()
        assert "结束所有终端会话" in notify.call_args.args[0]
        terminal = app.query_one(TerminalView)
        await pilot.press(*"printf 'LEGACY_%s\\n' works", "enter")
        await wait_frame(service, terminal.component.id, lambda f: "LEGACY_works" in screen_lines(f))
        await pilot.press("ctrl+b")
        assert "等待命令" in bar.render_line(0).text
        await pilot.press("escape")
        assert "旧后台不支持鼠标转发" in bar.render_line(0).text
        notify.assert_called_once()

        # Reconnecting to a mouse-capable daemon clears the warning even when
        # no child has enabled mouse tracking (mouse_tracking == 0).
        legacy = False
        await app.client.close()
        app.connected = False
        await ready(app, pilot)
        for _ in range(40):
            await pilot.pause(0.05)
            if app.daemon_mouse_supported is True:
                break
        assert app.daemon_mouse_supported is True
        assert terminal.frame["mouse_tracking"] == 0
        assert bar.render_line(0).text.strip() == ""
        assert (await service.call("list"))["sessions"][0]["alive"]


async def test_tabs_keyboard_shell_input_and_reopen(service, tmp_path):
    app = TermStation(tmp_path)
    async with app.run_test(size=(120, 38)) as pilot:
        await ready(app, pilot)
        assert not app.query_one("#tab-row").display
        assert app.query_one(Dashboard).region.y == 0
        terminal = app.query_one(TerminalView)
        assert app.focused == terminal
        identifier = terminal.component.id
        await pilot.press(*"printf 'TUI_%s\\n' works", "enter")
        await wait_frame(service, identifier, lambda f: "TUI_works" in screen_lines(f))
        await pilot.press("ctrl+c")
        assert app.is_running
        app.save_screenshot("/tmp/term-station-smoke.svg")
        await pilot.press("ctrl+b", "c")
        assert isinstance(app.screen, NameScreen)
        app.screen.query_one(Input).value = "服务"
        await pilot.press("enter")
        await ready(app, pilot)
        assert len(app.workspace.tabs) == 2
        assert app.query_one("#tab-row").display
        assert app.query_one(Dashboard).region.y == 1
        assert app.workspace.current.name == "服务"
        await pilot.press("ctrl+b", "1")
        await ready(app, pilot)
        assert app.workspace.current.name == "开发"
        assert app.query_one(TerminalView).component.id == identifier
        await pilot.press("ctrl+b", "n")
        await ready(app, pilot)
        assert app.workspace.current.name == "服务"
        await pilot.press("ctrl+b", "comma")
        app.screen.query_one(Input).value = "运行中的服务"
        await pilot.press("enter")
        await pilot.pause()
        assert app.workspace.current.name == "运行中的服务"
        await pilot.press("ctrl+b", "d")
    assert len((await service.call("list"))["sessions"]) == 2
    restored = TermStation(tmp_path)
    async with restored.run_test(size=(120, 38)) as pilot:
        await ready(restored, pilot)
        assert restored.workspace.current.name == "运行中的服务"
        await pilot.press("ctrl+b", "1")
        await ready(restored, pilot)
        assert "TUI_works" in screen_lines(restored.query_one(TerminalView).frame)


async def test_shift_chords_reach_shell_as_text(service, tmp_path):
    app = TermStation(tmp_path)
    async with app.run_test(size=(120, 38)) as pilot:
        await ready(app, pilot)
        terminal = app.query_one(TerminalView)
        await pilot.press(*"printf 'SHIFT_%s\\n' '")
        # Real terminal input: a standalone Shift press, Shift+H / Shift+/
        # without text, then Shift+H with text. The shell must receive H?H.
        for event in XTermParser().feed("\x1b[57441;2u\x1b[104;2u\x1b[47;2u\x1b[104;2;72u"):
            app.post_message(event)
        await pilot.pause()
        await pilot.press("'", "enter")
        await wait_frame(service, terminal.component.id, lambda f: "SHIFT_H?H" in screen_lines(f))


@pytest.mark.parametrize("command", ["d", "D", "\x04", "\x1b[100;2;68u"])
async def test_detach_from_child_prompt_preserves_input_and_running_session(service, tmp_path, command):
    child = tmp_path / "prompt_child.py"
    child.write_text("""import os, termios, tty
saved = termios.tcgetattr(0)
try:
    tty.setraw(0)
    os.write(1, b'READY_PROMPT\\r\\n')
    data = bytearray()
    while True:
        data.extend(os.read(0, 1))
        os.write(1, b'BUF:' + data.hex().encode() + b'\\r\\n')
finally:
    termios.tcsetattr(0, termios.TCSANOW, saved)
""")
    app = TermStation(tmp_path)
    async with app.run_test(size=(120, 38)) as pilot:
        await ready(app, pilot)
        terminal = app.query_one(TerminalView)
        await terminal.send(shlex.join([sys.executable, str(child)]) + "\r")
        await wait_frame(service, terminal.component.id, lambda f: "READY_PROMPT" in screen_lines(f))
        await pilot.press("h", "i")
        await wait_frame(service, terminal.component.id, lambda f: "BUF:6869" in screen_lines(f))
        sessions = (await service.call("list"))["sessions"]
        await pilot.press("ctrl+b")
        assert app.prefix_active
        for event in XTermParser().feed(command):
            app.post_message(event)
        await pilot.pause()
        assert not app.is_running
    assert (await service.call("list"))["sessions"] == sessions
    # Reconnect to the same raw-input child and prove the detach gesture did
    # not append d/D or send Ctrl+D. The child's existing input is preserved.
    await service.call("input", id=terminal.component.id, data="!")
    await wait_frame(service, terminal.component.id, lambda f: "BUF:686921" in screen_lines(f))


async def test_modified_chords_deliver_exact_bytes_to_child(service, tmp_path):
    child = tmp_path / "keyboard_child.py"
    child.write_text("""import os, termios, tty
saved = termios.tcgetattr(0)
try:
    tty.setraw(0)
    os.write(1, b'READY_KEYS\\r\\n')
    data = bytearray()
    while not data.endswith(b'!'):
        data.extend(os.read(0, 1))
    os.write(1, b'KEYS:' + data[:-1].hex().encode() + b'\\r\\n')
finally:
    termios.tcsetattr(0, termios.TCSANOW, saved)
""")
    app = TermStation(tmp_path)
    async with app.run_test(size=(120, 38)) as pilot:
        await ready(app, pilot)
        terminal = app.query_one(TerminalView)
        await terminal.send(shlex.join([sys.executable, str(child)]) + "\r")
        await wait_frame(service, terminal.component.id, lambda f: "READY_KEYS" in screen_lines(f))
        sequence = ("\x1b[13;2u\x1b[9;6u\x1b[47;5u\x1b[50;5u"
                    "\x1b[55;7u\x1b[127;2u\x1b[27;2;13~!")
        for event in XTermParser().feed(sequence):
            app.post_message(event)
        expected = b"\x1b[13;2u\x1b[9;6u\x1f\x00\x1b\x1f\x7f\x1b[13;2u".hex()
        await wait_frame(service, terminal.component.id, lambda f: "KEYS:" + expected in screen_lines(f))
        await pilot.press(*"printf 'AFTER_KEYS\\n'", "enter")
        await wait_frame(service, terminal.component.id, lambda f: "AFTER_KEYS" in screen_lines(f))


async def test_notes_add_component_layout_zoom_and_mouse_drag(service, tmp_path):
    app = TermStation(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app, pilot)
        await pilot.press("ctrl+b", "tab")
        assert isinstance(app.focused, NotesView)
        app.focused.load_text("便笺恢复验证\n第二行")
        await pilot.pause(0.45)
        assert WorkspaceStore(tmp_path).load().current.components[1].text == "便笺恢复验证\n第二行"
        await pilot.press("ctrl+b")
        assert app.prefix_active
        assert not app.query_one(Dashboard).compact, app.query_one(Dashboard).content_size
        await pilot.press("e")
        assert app.layout_mode
        item = app.current_panel().component
        initial_y, initial_h = item.y, item.h
        await pilot.press("down", "shift+down")
        await pilot.pause()
        assert item.y == initial_y + 1
        assert item.h == initial_h + 1
        await pilot.press("escape", "ctrl+b", "z")
        await pilot.pause()
        assert app.zoomed == item.id
        assert app.current_panel().size.width >= 110
        await pilot.press("ctrl+b", "z")
        await pilot.pause()
        panel = app.current_panel()
        before = item.y
        await mouse_input(pilot, "down", (panel.region.x+6, panel.region.y))
        assert panel.dragging
        target = (panel.region.x+6, panel.region.y+round(panel.drag_step[1]*2))
        await mouse_input(pilot, "move", target)
        await mouse_input(pilot, "up", target)
        await pilot.pause()
        assert not panel.dragging
        assert item.y == before + 2
        saved = WorkspaceStore(tmp_path).load()
        assert saved.current.components[1].y == item.y
        await pilot.press("ctrl+b", "a")
        await ready(app, pilot)
        assert len(app.screen_stack) == 1
        assert app.workspace.current.components[-1].kind == "terminal"
        assert isinstance(app.focused, TerminalView)
        assert len(list(app.query(Panel))) == 3


async def test_help_narrow_layout_and_direct_delete(service, tmp_path):
    app = TermStation(tmp_path)
    async with app.run_test(size=(64, 28)) as pilot:
        await ready(app, pilot)
        panels = list(app.query(Panel))
        assert all(p.region.x == panels[0].region.x for p in panels)
        assert app.workspace.current.components[1].x == 8
        await pilot.press("ctrl+b", "question_mark")
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.press("ctrl+b", "x")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert len(app.workspace.current.components) == 1
        assert len(WorkspaceStore(tmp_path).load().current.components) == 1
        assert (await service.call("list"))["sessions"] == []
        await pilot.press("ctrl+b", "x")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert app.workspace.current.components == []


async def test_tab_bar_disappears_when_second_tab_is_deleted(service, tmp_path):
    app = TermStation(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app, pilot)
        await pilot.press("ctrl+b", "c", "enter")
        await ready(app, pilot)
        assert app.query_one("#tab-row").display
        await pilot.press("ctrl+b", "w")
        await ready(app, pilot)
        assert len(app.screen_stack) == 1
        assert len(app.workspace.tabs) == 1
        assert len((await service.call("list"))["sessions"]) == 1
        assert not app.query_one("#tab-row").display
        board = app.query_one(Dashboard).region
        assert (board.x, board.y, board.right, board.bottom) == (0, 0, 120, 39)


async def test_literal_question_mark_opens_complete_scrollable_help_from_notes(service, tmp_path):
    from io import StringIO
    from rich.console import Console
    from term_station.shortcuts import PREFIX_COMMANDS

    app = TermStation(tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        await ready(app, pilot)
        await pilot.press("ctrl+b", "tab", "ctrl+b", "?")
        assert isinstance(app.screen, HelpScreen)
        output = StringIO()
        Console(file=output, width=80, color_system=None).print(app.screen.reference())
        help_text = output.getvalue()
        assert all(description in help_text for _, _, _, description in PREFIX_COMMANDS)
        assert all(key in help_text for key in ("1–9", "Shift+方向键", "Shift+PageUp", "Shift+PageDown", "Esc", "Enter"))
        scroller = app.screen.query_one("#help-scroll")
        assert app.focused == scroller
        assert scroller.scroll_y == 0
        await pilot.press("pagedown")
        await pilot.pause(0.2)
        assert scroller.scroll_y > 0
        await pilot.press("escape")
        assert isinstance(app.focused, NotesView)
        assert app.focused.text == ""


async def test_mouse_resize_restart_and_delete_last_tab(service, tmp_path):
    app = TermStation(tmp_path)
    async with app.run_test(size=(120, 44)) as pilot:
        await ready(app, pilot)
        panel = app.current_panel()
        height = panel.component.h
        target = (panel.region.x+10, panel.region.bottom-1-round(app.query_one(Dashboard).grid_step[1]*2))
        await mouse_input(pilot, "down", (panel.region.x+10, panel.region.bottom-1))
        await mouse_input(pilot, "move", target)
        await mouse_input(pilot, "up", target)
        assert panel.component.h == height - 2
        assert panel.region.height < app.query_one(Dashboard).content_size.height
        original_pid = (await service.call("list"))["sessions"][0]["pid"]
        await pilot.press("ctrl+b", "r")
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click("#confirm")
        await ready(app, pilot)
        assert (await service.call("list"))["sessions"][0]["pid"] != original_pid
        await pilot.press("ctrl+b", "w")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert len(app.workspace.tabs) == 1
        assert app.workspace.current.components == []
        assert not list(app.query(Panel))
        assert (await service.call("list"))["sessions"] == []
        await pilot.press("ctrl+b", "?")
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape", "ctrl+b", "a")
        await ready(app, pilot)
        assert len(app.screen_stack) == 1
        assert isinstance(app.focused, TerminalView)
        assert len(app.workspace.current.components) == 1
        assert len((await service.call("list"))["sessions"]) == 1


async def test_small_window_keeps_entire_terminal_visible(service, tmp_path):
    app = TermStation(tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        await ready(app, pilot)
        panel = app.current_panel()
        board = app.query_one(Dashboard)
        assert panel.region.height <= board.content_size.height
        assert panel.region.bottom <= board.region.bottom
        assert app.workspace.current.components[0].w == 8


async def test_direct_add_shell_uses_terminal_defaults_and_accepts_input(service, tmp_path):
    directory = tmp_path / "project"
    directory.mkdir()
    workspace = Workspace.default()
    workspace.current.components = workspace.current.components[:1]
    original = workspace.current.components[0]
    original.cwd = str(directory)
    original.shell = "/bin/sh"
    app = TermStation(tmp_path, workspace)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app, pilot)
        for mode in ("z", "e"):
            await pilot.press("ctrl+b", mode, "ctrl+b", "a")
            await ready(app, pilot)
            assert len(app.screen_stack) == 1
            assert not app.zoomed and not app.layout_mode and not app.prefix_active
            terminal = app.focused
            assert isinstance(terminal, TerminalView)
            item = terminal.component
            assert (item.cwd, item.shell) == (str(directory), "/bin/sh")
            assert item.id == app.workspace.current.components[-1].id
            await pilot.press(*"pwd", "enter")
            await wait_frame(service, item.id, lambda frame: str(directory.resolve()) in "".join(screen_lines(frame)))
        saved = WorkspaceStore(tmp_path).load()
        assert len(saved.current.components) == 3
        assert {s["id"] for s in (await service.call("list"))["sessions"]} == {c.id for c in saved.current.components}


async def test_panels_fill_viewport_with_separate_borders_after_resize(service, tmp_path):
    workspace = Workspace.default()
    workspace.current.components[1].h = 6
    workspace.current.components.append(Component(kind="clock", x=8, y=6, w=4, h=5))
    app = TermStation(tmp_path, workspace)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app, pilot)
        for width, height in [(120, 40), (150, 46), (80, 24)]:
            await pilot.resize_terminal(width, height)
            await pilot.pause()
            terminal, notes, clock = list(app.query(Panel))
            assert terminal.region.x == 0
            assert terminal.region.y == 0
            assert terminal.region.bottom == height - 1
            assert notes.region.right == clock.region.right == width
            assert notes.region.y == 0
            assert clock.region.bottom == height - 1
            assert terminal.region.right == notes.region.x == clock.region.x
            assert notes.region.bottom == clock.region.y
            assert app.query_one(TerminalView).size.height == height-3


async def test_function_keys_reach_terminal_without_triggering_workbench_actions(service, tmp_path, monkeypatch):
    app = TermStation(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app, pilot)
        terminal = app.query_one(TerminalView)
        sent = []

        async def capture(data):
            sent.append(data)

        monkeypatch.setattr(terminal, "send", capture)
        await pilot.press(*(f"f{i}" for i in range(1, 13)))
        assert sent == ["\x1bOP", "\x1bOQ", "\x1bOR", "\x1bOS", "\x1b[15~", "\x1b[17~", "\x1b[18~",
                        "\x1b[19~", "\x1b[20~", "\x1b[21~", "\x1b[23~", "\x1b[24~"]
        assert len(app.screen_stack) == len(app.workspace.tabs) == 1
        assert not app.layout_mode and not app.zoomed
        assert app.is_running
