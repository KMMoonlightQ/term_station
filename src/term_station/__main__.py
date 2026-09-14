from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import sys
from pathlib import Path

from .daemon import Client, Daemon
from .model import WorkspaceStore, state_directory


async def service_command(directory: Path, command: str) -> None:
    client = Client(directory)
    try:
        await client.connect(start=False)
        result = await client.call("shutdown" if command == "stop" else "list")
        if command == "stop":
            print("后台服务已收到停止请求，所有终端会话将结束。布局和便笺已保留。")
        else:
            print(json.dumps(result["sessions"], ensure_ascii=False, indent=2))
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Term Station · 可持久化的纯终端工作台（macOS / Linux）")
    parser.add_argument("command", nargs="?", choices=["start", "sessions", "stop"], default="start")
    parser.add_argument("--state-dir", type=Path, default=state_directory(), help="配置与后台会话目录")
    parser.add_argument("--yes", action="store_true", help="确认 stop 会结束所有终端进程")
    parser.add_argument("--version", action="version", version="term-station 0.1.1")
    parser.add_argument("--daemon", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    directory = args.state_dir.expanduser().resolve()
    if os.name != "posix":
        parser.error("目前支持 macOS / Linux；Windows 请在 WSL 内使用")
    try:
        if args.daemon:
            asyncio.run(Daemon(directory).run())
            return
        if args.command in ("sessions", "stop"):
            if args.command == "stop" and not args.yes:
                parser.error("stop 会结束所有终端进程；确认后请加 --yes")
            asyncio.run(service_command(directory, args.command))
            return
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            parser.error("请在交互式终端运行 term-station")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (directory / "ui.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                parser.error("这个工作空间已在另一个界面打开，请先退出该界面，或使用另一个 --state-dir")
            from .app import TermStation
            workspace = WorkspaceStore(directory).load()
            TermStation(directory, workspace).run()
    except (ValueError, ConnectionError, OSError) as error:
        print(f"term-station: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
