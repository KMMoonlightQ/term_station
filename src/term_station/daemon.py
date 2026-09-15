"""A local PTY multiplexer. UI disconnects never terminate a shell."""
from __future__ import annotations

import asyncio
import copy
import errno
import fcntl
import hashlib
import json
import os
import pty
import re
import signal
import struct
import subprocess
import sys
import termios
from itertools import groupby
from pathlib import Path
from typing import Any

import psutil
import pyte
from wcwidth import wcswidth

from .mouse import TRACKING_MODES, encode_mouse

PROTOCOL_LIMIT = 4 * 1024 * 1024


def socket_path(directory: Path) -> Path:
    path = directory / "server.sock"
    if len(os.fsencode(path)) >= 100:
        digest = hashlib.sha256(str(directory.resolve()).encode()).hexdigest()[:20]
        return Path(f"/tmp/term-station-{os.getuid()}-{digest}.sock")
    return path


class TerminalScreen(pyte.HistoryScreen):
    """pyte plus the alternate screen used by vim, less, and curses apps."""
    SAVED = ("buffer", "cursor", "savepoints", "margins", "history", "mode", "tabstops")

    def __init__(self, columns: int, lines: int, reply):
        self.alternate = False
        self.main_screen = None
        self.main_size = (columns, lines)
        self.reply = reply
        super().__init__(columns, lines, history=2000)

    def set_mode(self, *modes: int, **kwargs: Any) -> None:
        if kwargs.get("private") and any(m in (47, 1047, 1049) for m in modes) and not self.alternate:
            self.main_screen = {name: copy.deepcopy(getattr(self, name)) for name in self.SAVED}
            self.main_size = (self.columns, self.lines)
            super().reset()
            self.alternate = True
        super().set_mode(*modes, **kwargs)
        if kwargs.get("private"):
            tracking = [mode for mode in modes if mode in TRACKING_MODES]
            if tracking:
                self.mode.difference_update(mode << 5 for mode in TRACKING_MODES)
                self.mode.add(tracking[-1] << 5)

    def reset_mode(self, *modes: int, **kwargs: Any) -> None:
        if kwargs.get("private") and any(m in (47, 1047, 1049) for m in modes) and self.alternate:
            if self.main_screen is not None:
                for name, value in self.main_screen.items():
                    setattr(self, name, value)
            self.main_screen = None
            self.alternate = False
            columns, lines = self.columns, self.lines
            self.columns, self.lines = self.main_size
            super().resize(columns=columns, lines=lines)
            self.cursor.x = min(self.cursor.x, self.columns - 1)
            self.cursor.y = min(self.cursor.y, self.lines - 1)
            self.dirty.update(range(self.lines))
        super().reset_mode(*modes, **kwargs)

    def write_process_input(self, data: str) -> None:
        self.reply(data.encode())

    @property
    def mouse_tracking(self) -> int:
        return next((mode for mode in reversed(TRACKING_MODES) if mode << 5 in self.mode), 0)


class Session:
    def __init__(self, identifier: str, cwd: str, shell: str, columns: int = 80, rows: int = 24):
        self.id = identifier
        self.cwd = cwd
        self.shell = shell
        self.columns, self.rows = columns, rows
        self.loop = asyncio.get_running_loop()
        self.revision = 0
        self.exit_code = None
        self.closed = False
        self.pending = bytearray()
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor", "TERM_STATION_SESSION": identifier}
        env.pop("TMUX", None)
        env.pop("STY", None)
        env.pop("COLUMNS", None)
        env.pop("LINES", None)
        if getattr(sys, "frozen", False):
            # Shells launch system programs, including independent copies of us.
            for key in list(env):
                if key.startswith("_PYI_") or key == "PYINSTALLER_RESET_ENVIRONMENT":
                    env.pop(key)
            if sys.platform.startswith("linux"):
                original_library_path = env.pop("LD_LIBRARY_PATH_ORIG", None)
                if original_library_path is None:
                    env.pop("LD_LIBRARY_PATH", None)
                else:
                    env["LD_LIBRARY_PATH"] = original_library_path

        def controlling_terminal():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        try:
            self.process = subprocess.Popen([shell, "-l"], stdin=slave, stdout=slave, stderr=slave,
                                            cwd=cwd, env=env, preexec_fn=controlling_terminal, close_fds=True)
        except BaseException:
            os.close(self.master)
            raise
        finally:
            os.close(slave)
        os.set_blocking(self.master, False)
        self.screen = TerminalScreen(columns, rows, self.write)
        self.stream = pyte.ByteStream(self.screen)
        self.loop.add_reader(self.master, self.read)

    def read(self) -> None:
        try:
            data = os.read(self.master, 65536)
        except BlockingIOError:
            return
        except OSError as error:
            if error.errno != errno.EIO:
                raise
            data = b""
        if data:
            self.stream.feed(data)
            self.revision += 1
        else:
            self.close_fd()
            self.exit_code = self.process.poll()
            self.loop.call_later(0.05, self.reap)
            self.revision += 1

    def reap(self) -> None:
        self.exit_code = self.process.poll()
        if self.exit_code is None:
            self.loop.call_later(0.1, self.reap)
        else:
            self.revision += 1

    def write(self, data: bytes) -> None:
        if self.closed:
            return
        if len(self.pending) + len(data) > 1024 * 1024:
            raise ValueError("终端输入过长，请分段粘贴")
        self.pending.extend(data)
        self.flush()

    def flush(self) -> None:
        try:
            while self.pending:
                count = os.write(self.master, self.pending)
                del self.pending[:count]
            self.loop.remove_writer(self.master)
        except BlockingIOError:
            self.loop.add_writer(self.master, self.flush)
        except OSError:
            self.pending.clear()
            self.loop.remove_writer(self.master)

    def resize(self, columns: int, rows: int) -> None:
        columns, rows = max(2, min(400, columns)), max(2, min(150, rows))
        if (columns, rows) == (self.columns, self.rows):
            return
        self.columns, self.rows = columns, rows
        self.screen.resize(lines=rows, columns=columns)
        if not self.closed:
            fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        self.revision += 1

    def snapshot(self, offset: int = 0) -> dict:
        screen = self.screen
        history = list(screen.history.top) if not screen.alternate else []
        offset = max(0, min(len(history), offset))
        lines = history + [screen.buffer[y] for y in range(screen.lines)]
        end = len(lines) - offset
        visible = lines[max(0, end-screen.lines):end]
        encoded = []
        for line in visible:
            chars = []
            for x in range(screen.columns):
                char = line[x]
                # pyte may leave either half of an overwritten wide glyph behind.
                # Preserve its screen columns when converting the grid to text.
                if char.data == "":
                    if x == 0 or wcswidth(line[x - 1].data) != 2:
                        char = char._replace(data=" ")
                elif wcswidth(char.data) == 2:
                    if x + 1 == screen.columns or line[x + 1].data != "":
                        char = char._replace(data=" ")
                chars.append(char)
            runs = []
            for attributes, group in groupby(chars, key=lambda c: (c.fg, c.bg, c.bold, c.italics, c.underscore, c.reverse, c.strikethrough)):
                runs.append(["".join(c.data for c in group), *attributes])
            encoded.append(runs)
        return {"id": self.id, "revision": self.revision, "lines": encoded,
                "cursor": [screen.cursor.x, screen.cursor.y], "cursor_hidden": screen.cursor.hidden or offset > 0 or self.closed,
                "history": len(history), "offset": offset, "columns": self.columns, "rows": self.rows,
                "alternate": screen.alternate,
                "application_cursor": (1 << 5) in screen.mode, "bracketed_paste": (2004 << 5) in screen.mode,
                "mouse_tracking": screen.mouse_tracking,
                "alive": not self.closed, "exit_code": self.exit_code}

    def close_fd(self) -> None:
        if not self.closed:
            self.loop.remove_reader(self.master)
            self.loop.remove_writer(self.master)
            os.close(self.master)
            self.closed = True

    async def terminate(self) -> None:
        # Kill only descendants of this component, including foreground job groups.
        descendants = []
        if self.process.poll() is None:
            try:
                descendants = psutil.Process(self.process.pid).children(recursive=True)
            except psutil.Error:
                pass
            for child in descendants:
                try:
                    child.terminate()
                except psutil.Error:
                    pass
            try:
                os.killpg(self.process.pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
        self.close_fd()
        for _ in range(10):
            if self.process.poll() is not None and not any(child.is_running() for child in descendants):
                break
            await asyncio.sleep(0.05)
        for child in descendants:
            try:
                child.kill()
            except psutil.Error:
                pass
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()


class Daemon:
    def __init__(self, directory: Path):
        self.directory = directory
        self.sessions: dict[str, Session] = {}
        self.stopping = None

    async def dispatch(self, request: dict) -> dict:
        operation = request.get("op")
        if operation == "ping":
            return {"pid": os.getpid(), "version": 1}
        if operation == "list":
            return {"sessions": [{"id": s.id, "pid": s.process.pid, "cwd": s.cwd, "shell": s.shell,
                                  "alive": not s.closed} for s in self.sessions.values()]}
        if operation == "snapshot":
            frames = []
            for spec in request.get("sessions", []):
                session = self.sessions.get(spec["id"])
                if session and (spec.get("revision") != session.revision or spec.get("offset", 0) != spec.get("last_offset", 0)):
                    frames.append(session.snapshot(spec.get("offset", 0)))
            return {"frames": frames}
        if operation == "shutdown":
            self.stopping.set()
            return {"stopping": True}
        identifier = request.get("id", "")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("无效会话 ID")
        if operation == "ensure":
            if identifier not in self.sessions:
                cwd = str(Path(request["cwd"]).expanduser().resolve())
                shell = request["shell"]
                if not Path(cwd).is_dir():
                    raise ValueError(f"启动目录不存在：{cwd}")
                if not isinstance(shell, str) or not os.path.isabs(shell) or not os.access(shell, os.X_OK) or not Path(shell).is_file():
                    raise ValueError(f"Shell 必须是可执行文件的绝对路径：{shell}")
                self.sessions[identifier] = Session(identifier, cwd, shell)
            return {"id": identifier, "alive": not self.sessions[identifier].closed}
        if operation == "kill":
            session = self.sessions.pop(identifier, None)
            if session:
                await session.terminate()
            return {"killed": identifier}
        session = self.sessions.get(identifier)
        if not session:
            raise ValueError("会话不存在")
        if operation == "input":
            session.write(request["data"].encode("utf-8"))
        elif operation == "mouse":
            x, y = request["x"], request["y"]
            if type(x) is not int or type(y) is not int or not (0 <= x < session.columns and 0 <= y < session.rows):
                raise ValueError("无效鼠标坐标")
            data = encode_mouse(request["action"], request["button"], x, y,
                                session.screen.mouse_tracking, (1006 << 5) in session.screen.mode,
                                request.get("shift", False), request.get("alt", False), request.get("ctrl", False))
            if data:
                session.write(data)
        elif operation == "resize":
            session.resize(int(request["columns"]), int(request["rows"]))
        else:
            raise ValueError("未知操作")
        return {}

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while line := await reader.readline():
                try:
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        raise ValueError("请求必须是 JSON 对象")
                    result = {"ok": True, **await self.dispatch(request)}
                except (ValueError, KeyError, TypeError, OSError) as error:
                    result = {"ok": False, "error": str(error)}
                writer.write(json.dumps(result, ensure_ascii=False).encode() + b"\n")
                await writer.drain()
        except (ConnectionError, ValueError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, asyncio.CancelledError):
                pass

    async def run(self) -> None:
        self.stopping = asyncio.Event()
        os.umask(0o077)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (self.directory / "daemon.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            address = socket_path(self.directory)
            if address.exists():
                if not address.is_socket():
                    raise RuntimeError(f"{address} 不是 socket，拒绝覆盖")
                address.unlink()
            server = await asyncio.start_unix_server(self.handle, path=str(address), limit=PROTOCOL_LIMIT)
            os.chmod(address, 0o600)
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, self.stopping.set)
            try:
                async with server:
                    await self.stopping.wait()
            finally:
                await asyncio.gather(*(session.terminate() for session in self.sessions.values()))
                address.unlink(missing_ok=True)


class Client:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.reader = None
        self.writer = None
        self.lock = None

    async def connect(self, start: bool = True) -> None:
        try:
            self.reader, self.writer = await asyncio.open_unix_connection(str(socket_path(self.directory)), limit=PROTOCOL_LIMIT)
            return
        except (FileNotFoundError, ConnectionRefusedError):
            if not start:
                raise ConnectionError("后台服务未运行")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        log_fd = os.open(self.directory / "daemon.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        frozen = getattr(sys, "frozen", False)
        command = ([sys.executable, "--daemon", "--state-dir", str(self.directory)] if frozen
                   else [sys.executable, "-m", "term_station.daemon", str(self.directory)])
        # A one-file UI deletes its extracted files when it exits. The daemon
        # must unpack its own runtime so it can keep serving later UI instances.
        daemon_env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"} if frozen else None
        with os.fdopen(log_fd, "ab") as log:
            child = subprocess.Popen(command, env=daemon_env, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=log, start_new_session=True)
        for _ in range(300 if frozen else 100):
            await asyncio.sleep(0.05)
            try:
                self.reader, self.writer = await asyncio.open_unix_connection(str(socket_path(self.directory)), limit=PROTOCOL_LIMIT)
                return
            except (FileNotFoundError, ConnectionRefusedError):
                if child.poll() not in (None, 0):
                    break
        raise ConnectionError(f"后台服务启动失败，请查看 {self.directory / 'daemon.log'}")

    async def call(self, operation: str, **parameters: Any) -> dict:
        if self.lock is None:
            self.lock = asyncio.Lock()
        async with self.lock:
            if self.writer is None or self.reader is None:
                raise ConnectionError("后台服务尚未连接")
            try:
                self.writer.write(json.dumps({"op": operation, **parameters}, ensure_ascii=False).encode() + b"\n")
                await asyncio.wait_for(self.writer.drain(), timeout=5)
                line = await asyncio.wait_for(self.reader.readline(), timeout=5)
                if not line:
                    raise ConnectionError("后台连接已断开")
                response = json.loads(line)
            except (OSError, asyncio.TimeoutError, ValueError) as error:
                raise ConnectionError(f"后台通信失败：{error}") from error
            if not response.get("ok"):
                raise ValueError(response.get("error", "后台请求失败"))
            return response

    async def close(self) -> None:
        if self.writer:
            writer = self.writer
            self.writer = None
            self.reader = None
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, RuntimeError):
                pass


if __name__ == "__main__":
    asyncio.run(Daemon(Path(sys.argv[1]).resolve()).run())
