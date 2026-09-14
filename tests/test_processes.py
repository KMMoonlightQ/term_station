import asyncio
import shutil

import pytest

from term_station.app import TermStation
from term_station.model import Component, Workspace, new_id
from term_station.processes import foreground_commands
from term_station.terminal import TerminalView
from term_station.widgets import Panel


async def wait_title(service, identifier, expected):
    for _ in range(80):
        titles = foreground_commands((await service.call("list"))["sessions"])
        if titles.get(identifier) == expected:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"期待标题 {expected}，实际 {titles}")


async def test_foreground_command_restores_shell_and_ignores_background_jobs(service, tmp_path):
    identifier = new_id()
    await service.call("ensure", id=identifier, cwd=str(tmp_path), shell="/bin/sh")
    await wait_title(service, identifier, "sh")
    await service.call("input", id=identifier, data="sleep 30\r")
    await wait_title(service, identifier, "sleep")
    await service.call("input", id=identifier, data="\x03")
    await wait_title(service, identifier, "sh")
    await service.call("input", id=identifier, data="sleep 30 &\r")
    await asyncio.sleep(0.15)
    await wait_title(service, identifier, "sh")
    await service.call("input", id=identifier, data="exec sleep 30\r")
    await wait_title(service, identifier, "sleep")


async def test_zsh_top_and_back(service, tmp_path):
    if not shutil.which("zsh") or not shutil.which("top"):
        pytest.skip("需要 zsh 和 top")
    identifier = new_id()
    await service.call("ensure", id=identifier, cwd=str(tmp_path), shell="/bin/sh")
    await service.call("input", id=identifier, data="exec zsh -f\r")
    await wait_title(service, identifier, "zsh")
    await service.call("input", id=identifier, data="top\r")
    await wait_title(service, identifier, "top")
    await service.call("input", id=identifier, data="\x03")
    await wait_title(service, identifier, "zsh")


async def test_panel_title_updates_during_silent_command_without_restarting_daemon(service, tmp_path):
    workspace = Workspace.default()
    component = Component(shell="/bin/sh", cwd=str(tmp_path), title="原有名称")
    workspace.current.components = [component]
    daemon_pid = (await service.call("ping"))["pid"]
    app = TermStation(tmp_path, workspace)
    async with app.run_test(size=(100, 32)) as pilot:
        async def title_is(expected):
            for _ in range(100):
                await pilot.pause(0.05)
                if app.query_one(Panel).border_title == expected:
                    return
            raise AssertionError(f"窗口标题未切换为 {expected}")

        await title_is("sh")
        for _ in range(80):
            if app.query_one(TerminalView).ready:
                break
            await pilot.pause(0.05)
        await pilot.press(*"sleep 30", "enter")
        await title_is("sleep")
        await pilot.press("ctrl+c")
        await title_is("sh")
        assert component.title == "原有名称"
        assert (await service.call("ping"))["pid"] == daemon_pid
        await pilot.press("ctrl+b", "d")
