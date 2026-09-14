import asyncio
import os
import re

import psutil
import pytest

from term_station.daemon import Client, socket_path
from term_station.model import new_id
from conftest import screen_lines, wait_frame


async def new_session(client, tmp_path):
    identifier = new_id()
    await client.call("ensure", id=identifier, cwd=str(tmp_path), shell="/bin/sh")
    await client.call("input", id=identifier, data="stty -echo\r")
    return identifier


async def test_real_shell_survives_disconnect_with_environment_cwd_and_jobs(service, tmp_path):
    identifier = await new_session(service, tmp_path)
    await service.call("input", id=identifier, data="export STATION_TEST=preserved; cd /tmp; sleep 30 & printf '\\nCHILD:%s\\n' $!\r")
    frame = await wait_frame(service, identifier, lambda f: any(re.fullmatch(r"CHILD:\d+", line) for line in screen_lines(f)))
    child_pid = int(next(line[6:] for line in screen_lines(frame) if re.fullmatch(r"CHILD:\d+", line)))
    original_pid = (await service.call("list"))["sessions"][0]["pid"]
    await service.close()
    await asyncio.sleep(0.15)
    reconnect = Client(tmp_path)
    try:
        await reconnect.connect(start=False)
        await reconnect.call("ensure", id=identifier, cwd=str(tmp_path), shell="/bin/sh")
        assert (await reconnect.call("list"))["sessions"][0]["pid"] == original_pid
        assert psutil.pid_exists(child_pid)
        await reconnect.call("input", id=identifier, data="printf '\\nVALUE:%s DIR:%s\\n' \"$STATION_TEST\" \"$PWD\"\r")
        await wait_frame(reconnect, identifier, lambda f: "VALUE:preserved DIR:/tmp" in screen_lines(f))
    finally:
        await reconnect.close()
    await service.connect(start=False)
    await service.call("kill", id=identifier)
    assert not psutil.pid_exists(original_pid)
    assert not psutil.pid_exists(child_pid)


async def test_resize_interrupt_history_and_exit(service, tmp_path):
    identifier = await new_session(service, tmp_path)
    await service.call("resize", id=identifier, columns=96, rows=18)
    await service.call("input", id=identifier, data="printf '\\n'; stty size\r")
    await wait_frame(service, identifier, lambda f: "18 96" in screen_lines(f))
    await service.call("input", id=identifier, data="sleep 30\r")
    await asyncio.sleep(0.15)
    await service.call("input", id=identifier, data="\x03")
    await service.call("input", id=identifier, data="printf '\\nAFTER_INTERRUPT\\n'; i=0; while [ $i -lt 40 ]; do echo row-$i; i=$((i+1)); done\r")
    frame = await wait_frame(service, identifier, lambda f: "row-39" in screen_lines(f))
    assert frame["history"] > 0
    older = (await service.call("snapshot", sessions=[{"id":identifier,"offset":25}]))["frames"][0]
    assert "row-39" not in screen_lines(older)
    assert older["cursor_hidden"]
    await service.call("input", id=identifier, data="exit 7\r")
    frame = await wait_frame(service, identifier, lambda f: f["exit_code"] == 7)
    assert not frame["alive"]
    await service.call("ensure", id=identifier, cwd=str(tmp_path), shell="/bin/sh")
    assert not (await service.call("list"))["sessions"][0]["alive"]


async def test_input_validation_and_private_socket(service, tmp_path):
    assert socket_path(tmp_path).stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="无效会话"):
        await service.call("ensure", id="../../bad", cwd=str(tmp_path), shell="/bin/sh")
    with pytest.raises(ValueError, match="启动目录"):
        await service.call("ensure", id=new_id(), cwd=str(tmp_path/"missing"), shell="/bin/sh")
    with pytest.raises(ValueError, match="绝对路径"):
        await service.call("ensure", id=new_id(), cwd=str(tmp_path), shell="sh")
    assert (await service.call("list"))["sessions"] == []


async def test_vim_edits_file_and_restores_shell(service, tmp_path):
    import shutil
    if not shutil.which("vim"):
        pytest.skip("vim is not installed")
    identifier = await new_session(service, tmp_path)
    await service.call("input", id=identifier, data="vim -Nu NONE -i NONE -n editor-check.txt\r")
    await asyncio.sleep(0.3)
    await service.call("input", id=identifier, data="i终端编辑验证\rsecond line")
    await wait_frame(service, identifier, lambda f: any("终端编辑验证" in line for line in screen_lines(f)))
    await service.call("resize", id=identifier, columns=100, rows=30)
    await service.call("input", id=identifier, data="\x1b:wq\r")
    for _ in range(40):
        if (tmp_path/"editor-check.txt").exists():
            break
        await asyncio.sleep(0.05)
    assert (tmp_path/"editor-check.txt").read_text() == "终端编辑验证\nsecond line\n"
    await wait_frame(service, identifier, lambda f: not f["alternate"])
    await service.call("input", id=identifier, data="printf '\\nEDITOR_%s\\n' done\r")
    await wait_frame(service, identifier, lambda f: "EDITOR_done" in screen_lines(f))
