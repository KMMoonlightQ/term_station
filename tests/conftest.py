import asyncio

import psutil
import pytest
from textual._xterm_parser import XTermParser

from term_station.daemon import Client


@pytest.fixture
async def service(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.setenv("TERM", "xterm-256color")
    client = Client(tmp_path)
    await client.connect()
    pid = (await client.call("ping"))["pid"]
    try:
        yield client
    finally:
        try:
            if client.writer is None:
                await client.connect(start=False)
            await client.call("shutdown")
        except (ConnectionError, OSError):
            pass
        await client.close()
        for _ in range(80):
            if not psutil.pid_exists(pid):
                break
            try:
                if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                    break
            except psutil.NoSuchProcess:
                break
            await asyncio.sleep(0.05)
        else:
            psutil.Process(pid).kill()
            pytest.fail("测试后台未能按时退出")


async def wait_frame(client, identifier, predicate, timeout=5):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    frame = {}
    while loop.time() < end:
        response = await client.call("snapshot", sessions=[{"id": identifier}])
        if response["frames"]:
            frame = response["frames"][0]
            if predicate(frame):
                return frame
        await asyncio.sleep(0.05)
    raise AssertionError(f"终端未达到预期状态：{frame}")


def screen_lines(frame):
    return ["".join(run[0] for run in line).rstrip() for line in frame["lines"]]


async def mouse_input(pilot, action, position):
    # Pilot's mouse helpers bypass App.on_event. Use the terminal's SGR mouse
    # protocol so border interception follows the same path as real input.
    x, y = position
    button, suffix = {"move": (35, "M"), "down": (0, "M"), "up": (0, "m")}[action]
    for event in XTermParser().feed(f"\x1b[<{button};{x+1};{y+1}{suffix}"):
        pilot.app.post_message(event)
    await pilot.pause()
