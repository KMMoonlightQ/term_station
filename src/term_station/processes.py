"""Read foreground jobs without restarting the persistent PTY service."""
from __future__ import annotations

import os
import subprocess

import psutil


def command_name(process: psutil.Process) -> str:
    try:
        argv = process.cmdline()
    except psutil.AccessDenied:
        # macOS top is setuid: its name is readable but its arguments are not.
        argv = []
    name = os.path.basename(argv[0]).lstrip("-") if argv else process.name()
    return "".join(c for c in name if c.isprintable())[:80]


def foreground_commands(sessions: list[dict]) -> dict[str, str]:
    titles = {s["id"]: os.path.basename(s["shell"]) for s in sessions}
    running = {s["pid"]: s for s in sessions if s.get("alive") and isinstance(s.get("pid"), int) and s["pid"] > 0}
    if not running:
        return titles
    try:
        # tpgid is the controlling terminal's foreground job, not its most
        # recently spawned child (which might be a background task).
        result = subprocess.run(
            ["ps", "-p", ",".join(str(pid) for pid in running), "-o", "pid=,tpgid="],
            capture_output=True, text=True, timeout=1,
        )
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) != 2:
                continue
            pid, foreground = map(int, fields)
            if pid not in running or foreground <= 0:
                continue
            try:
                process = psutil.Process(foreground)
                title = command_name(process)
                # Shell wrappers can run the actual program in the same job.
                if foreground != pid and title in {"sh", "bash", "zsh", "fish", "sudo", "env", "time"}:
                    for child in reversed(process.children(recursive=True)):
                        if os.getpgid(child.pid) == foreground:
                            title = command_name(child)
                            break
                if title:
                    titles[running[pid]["id"]] = title
            except (psutil.Error, OSError):
                continue
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return titles
