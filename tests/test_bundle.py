"""Opt-in tests for the actual executable: TERM_STATION_BUNDLE=/path/to/bundle-directory."""
import asyncio
import fcntl
import json
import os
from pathlib import Path
import pty
import shutil
import struct
import subprocess
import termios

import psutil
import pyte
import pytest

from term_station.daemon import Client
from term_station.model import Component, Workspace, WorkspaceStore
from conftest import screen_lines, wait_frame


@pytest.mark.skipif(not os.environ.get("TERM_STATION_BUNDLE"), reason="需要指定打包产物")
async def test_standalone_binary_preserves_sessions_after_ui_exit_and_reopen(tmp_path):
    # Relocate the complete distribution, including internal library symlinks.
    # Neither the source tree nor its virtualenv is on the child's working path.
    artifact = Path(os.environ["TERM_STATION_BUNDLE"]).resolve()
    if artifact.is_file():
        artifact = artifact.parent
    bundle = tmp_path / "relocated bundle"
    shutil.copytree(artifact, bundle, symlinks=True)
    binary = bundle / "term-station"
    assert (bundle / "_internal").is_dir()
    environment = {k: v for k, v in os.environ.items()
                   if k not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "NO_COLOR", "TEXTUAL_DEVTOOLS"}}
    environment.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", SHELL="/bin/sh",
                       TERM="xterm-256color", COLORTERM="truecolor")
    directory = tmp_path / "saved state"
    workspace = Workspace.default()
    component = Component(shell="/bin/sh", cwd=str(tmp_path), w=12)
    workspace.current.components = [component]
    WorkspaceStore(directory).save(workspace)
    loop = asyncio.get_running_loop()
    uis = []
    client = Client(directory)
    daemon_pid = None

    def launch():
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        process = subprocess.Popen([str(binary), "--state-dir", str(directory)],
                                   stdin=slave, stdout=slave, stderr=slave,
                                   cwd=tmp_path, env=environment, start_new_session=True)
        os.close(slave)
        os.set_blocking(master, False)
        screen = pyte.Screen(120, 40)
        stream = pyte.ByteStream(screen)
        uis.append((process, master))

        def drain():
            try:
                data = os.read(master, 65536)
            except BlockingIOError:
                return
            except OSError:
                data = b""
            if data:
                stream.feed(data)
            else:
                loop.remove_reader(master)

        loop.add_reader(master, drain)
        return process, master, screen

    async def visible(screen, text):
        for _ in range(600):
            if text in "\n".join(screen.display):
                return
            await asyncio.sleep(0.05)
        raise AssertionError(f"打包界面缺少 {text}：\n" + "\n".join(screen.display))

    try:
        process, master, screen = launch()
        # Allow for macOS validation of the newly copied distribution.
        for _ in range(1200):
            try:
                await client.connect(start=False)
                break
            except ConnectionError:
                assert process.poll() is None, "\n".join(screen.display)
                await asyncio.sleep(0.05)
        else:
            log = directory / "daemon.log"
            raise AssertionError("后台未连接：\n" + "\n".join(screen.display)
                                 + "\n" + (log.read_text() if log.exists() else "缺少后台日志"))
        daemon_pid = (await client.call("ping"))["pid"]
        assert Path(psutil.Process(daemon_pid).exe()).resolve() == binary.resolve()
        await wait_frame(client, component.id, lambda f: f["alive"])
        await visible(screen, "sh")
        os.write(master, b"export BUNDLE_CHECK=preserved; printf '\\nBUNDLE_READY\\n'\r")
        await wait_frame(client, component.id, lambda f: "BUNDLE_READY" in screen_lines(f))
        shell_pid = (await client.call("list"))["sessions"][0]["pid"]
        os.write(master, b"\x02?")
        await visible(screen, "快捷键")
        os.write(master, b"\x1b")
        await asyncio.sleep(0.15)
        os.write(master, b"\x02d")
        assert await asyncio.to_thread(process.wait, 15) == 0
        assert psutil.pid_exists(daemon_pid)
        assert (await client.call("list"))["sessions"][0]["pid"] == shell_pid
        runtime = psutil.Process(daemon_pid).environ().get("_PYI_APPLICATION_HOME_DIR")
        # Onedir bootloaders may omit the one-file extraction environment key.
        if runtime:
            assert Path(runtime).resolve() == (bundle / "_internal").resolve()
        assert (bundle / "_internal").is_dir(), "后台运行目录必须在界面退出后继续存在"

        process, master, screen = launch()
        await visible(screen, "BUNDLE_READY")
        os.write(master, b"printf '\\nBUNDLE_%s\\n' \"$BUNDLE_CHECK\"\r")
        await wait_frame(client, component.id, lambda f: "BUNDLE_preserved" in screen_lines(f))
        assert (await client.call("ping"))["pid"] == daemon_pid
        # The surviving daemon can create shells after the first UI exits.
        os.write(master, b"\x02a")
        for _ in range(100):
            if len((await client.call("list"))["sessions"]) == 2:
                break
            await asyncio.sleep(0.05)
        assert len((await client.call("list"))["sessions"]) == 2
        os.write(master, b"\x02d")
        assert await asyncio.to_thread(process.wait, 15) == 0
        result = await asyncio.to_thread(subprocess.run, [str(binary), "sessions", "--state-dir", str(directory)],
                                         capture_output=True, text=True, env=environment, cwd=tmp_path, timeout=15)
        assert result.returncode == 0, result.stderr
        assert len(json.loads(result.stdout)) == 2
        result = await asyncio.to_thread(subprocess.run, [str(binary), "stop", "--yes", "--state-dir", str(directory)],
                                         capture_output=True, text=True, env=environment, cwd=tmp_path, timeout=15)
        assert result.returncode == 0, result.stderr
        assert len(WorkspaceStore(directory).load().current.components) == 2
    finally:
        for process, master in uis:
            if process.poll() is None:
                process.terminate()
                await asyncio.to_thread(process.wait, 10)
            loop.remove_reader(master)
            os.close(master)
        if daemon_pid is None:
            # A timed-out UI may have spawned a daemon that is still starting.
            for _ in range(300):
                try:
                    await client.connect(start=False)
                    daemon_pid = (await client.call("ping"))["pid"]
                    break
                except (ConnectionError, OSError):
                    await asyncio.sleep(0.05)
        try:
            if client.writer:
                await client.call("shutdown")
        except (ConnectionError, OSError):
            pass
        await client.close()
        if daemon_pid:
            for _ in range(100):
                if not psutil.pid_exists(daemon_pid):
                    break
                await asyncio.sleep(0.05)
            else:
                psutil.Process(daemon_pid).kill()
                pytest.fail("打包后台没有正常退出")
