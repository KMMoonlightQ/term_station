import asyncio
from unittest.mock import AsyncMock

import pytest
from textual import events
from textual._xterm_parser import XTermParser

from term_station.app import TermStation
from term_station.dialogs import HelpScreen
from term_station.model import Component, Workspace, WorkspaceTab
from term_station.terminal import TerminalView
from term_station.widgets import Dashboard, NotesView


@pytest.fixture
def key_app(tmp_path, monkeypatch):
    app = TermStation(tmp_path)
    # Exercise the real input dispatcher without needing a PTY connection.
    monkeypatch.setattr(app, "poll_loop", AsyncMock())
    return app


async def test_queued_shell_input_cannot_clear_a_new_prefix(key_app, monkeypatch):
    async with key_app.run_test() as pilot:
        terminal = key_app.query_one(TerminalView)
        started, unblock, drained = asyncio.Event(), asyncio.Event(), asyncio.Event()
        sent = []

        async def slow_send(data):
            sent.append(data)
            if data == "a":
                started.set()
                await unblock.wait()
            elif data == "s":
                drained.set()

        monkeypatch.setattr(terminal, "send", slow_send)
        key_app.post_message(events.Key("a", "a"))
        await asyncio.wait_for(started.wait(), 1)
        try:
            key_app.post_message(events.Key("s", "s"))
            key_app.post_message(events.Key("ctrl+b", "\x02"))
            for _ in range(100):
                if key_app.prefix_active:
                    break
                await asyncio.sleep(0.01)
            assert key_app.prefix_active
        finally:
            unblock.set()
        await asyncio.wait_for(drained.wait(), 1)
        assert key_app.prefix_active
        await pilot.press("?")
        assert isinstance(key_app.screen, HelpScreen)
        assert sent == ["a", "s"]


async def test_prefix_waits_for_next_key_without_expiring(key_app, monkeypatch):
    async with key_app.run_test() as pilot:
        send = AsyncMock()
        monkeypatch.setattr(key_app.query_one(TerminalView), "send", send)
        await pilot.press("ctrl+b")
        await pilot.pause(4.2)
        assert key_app.prefix_active
        assert "等待命令" in key_app.query_one("#command-bar").render_line(0).text
        await pilot.press("?")
        assert isinstance(key_app.screen, HelpScreen)
        send.assert_not_called()


@pytest.mark.parametrize("focus", ["terminal", "notes"])
async def test_help_accepts_question_marks_from_terminal_input_protocols(key_app, monkeypatch, focus):
    async with key_app.run_test() as pilot:
        send = AsyncMock()
        monkeypatch.setattr(key_app.query_one(TerminalView), "send", send)
        if focus == "notes":
            await pilot.press("ctrl+b", "tab")
        original_focus = key_app.focused
        for sequence in (
            "\x02?", "\x02？", "\x02\x1b[63;2u", "\x02\x1b[47;2u",
            "\x02\x1b[63;5u", "\x02\x1b[47;6u", "\x02\x1f",
        ):
            for event in XTermParser().feed(sequence):
                key_app.post_message(event)
            await pilot.pause()
            assert isinstance(key_app.screen, HelpScreen), repr(sequence)
            assert not key_app.prefix_active
            await pilot.press("escape")
            assert key_app.focused == original_focus
        assert key_app.query_one(NotesView).text == ""
        send.assert_not_called()


async def test_cancelling_prefix_and_typing_ordinary_question_marks(key_app, monkeypatch):
    async with key_app.run_test() as pilot:
        send = AsyncMock()
        monkeypatch.setattr(key_app.query_one(TerminalView), "send", send)
        await pilot.press("ctrl+b")
        assert "等待命令" in key_app.query_one("#command-bar").render_line(0).text
        await pilot.press("escape", "?")
        assert key_app.query_one("#command-bar").render_line(0).text.strip() == ""
        assert not key_app.prefix_active
        assert len(key_app.screen_stack) == 1
        send.assert_awaited_once_with("?")
        await pilot.press("ctrl+b", "tab")
        for event in XTermParser().feed("?？"):
            key_app.post_message(event)
        await pilot.pause()
        assert key_app.query_one(NotesView).text == "?？"
        await pilot.press("ctrl+b", "g", "?")
        assert not key_app.prefix_active
        assert len(key_app.screen_stack) == 1
        assert key_app.query_one(NotesView).text == "?？?"


async def test_literal_control_b_still_reaches_the_shell(key_app, monkeypatch):
    async with key_app.run_test() as pilot:
        send = AsyncMock()
        monkeypatch.setattr(key_app.query_one(TerminalView), "send", send)
        for second_key in ("ctrl+b", "b"):
            await pilot.press("ctrl+b", second_key)
            assert not key_app.prefix_active
        assert [call.args[0] for call in send.await_args_list] == ["\x02", "\x02"]


@pytest.mark.parametrize("shift_code", [57441, 57447])
async def test_saved_single_terminal_layout_keeps_prefix_when_shift_is_pressed(tmp_path, monkeypatch, shift_code):
    # Match the saved one-tab, one-zsh workspace where the user hit this bug.
    tab = WorkspaceTab(name="开发", components=[Component(title="主终端", shell="/bin/zsh", x=0, y=0, w=8, h=11)])
    app = TermStation(tmp_path, Workspace(active_tab=tab.id, tabs=[tab]))
    monkeypatch.setattr(app, "poll_loop", AsyncMock())
    async with app.run_test(size=(172, 48)) as pilot:
        send = AsyncMock()
        monkeypatch.setattr(app.query_one(TerminalView), "send", send)
        bar = app.query_one("#command-bar")
        board_region = app.query_one(Dashboard).region
        assert bar.region.y == 47 and bar.region.height == 1 and bar.region.width == 172
        assert board_region.bottom == bar.region.y
        assert bar.render_line(0).text.strip() == ""
        # Textual requests Kitty report-all-keys: Control and Shift each arrive
        # separately, before their chord's character event.
        for event in XTermParser().feed("\x1b[57442;5u\x1b[98;5u"):
            app.post_message(event)
        await pilot.pause()
        assert app.prefix_active
        assert "Ctrl+B › 等待命令" in bar.render_line(0).text
        assert app.query_one(Dashboard).region == board_region
        for event in XTermParser().feed(f"\x1b[{shift_code};2u"):
            app.post_message(event)
        await pilot.pause()
        assert app.prefix_active
        assert "等待命令" in bar.render_line(0).text
        for event in XTermParser().feed("\x1b[47;2;63u"):
            app.post_message(event)
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        assert not app.prefix_active
        send.assert_not_called()
        await pilot.press("escape")
        assert bar.render_line(0).text.strip() == ""
        assert app.query_one(Dashboard).region == board_region
