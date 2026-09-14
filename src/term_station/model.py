"""Versioned workspace data and deterministic, non-overlapping grid placement."""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

KINDS = {"terminal": "终端", "notes": "便笺", "system": "系统状态", "clock": "时钟"}
COLUMNS = 12
LEGACY_INTRO_NOTES = {
    "今天，从这里开始。\n\n• 把任务拆成小步\n• 留下关键命令和想法\n\n内容自动保存。",
    "今天，从这里开始。\n\n• 把任务拆成小步\n• 留下关键命令和想法\n\n这里的内容会自动保存。",
}


def new_id() -> str:
    return uuid4().hex


def state_directory() -> Path:
    return Path(os.environ.get("TERM_STATION_STATE_DIR", str(Path.home() / ".local/state/term-station"))).expanduser()


@dataclass
class Component:
    kind: str = "terminal"
    title: str = "终端"
    id: str = field(default_factory=new_id)
    x: int = 0
    y: int = 0
    w: int = 6
    h: int = 8
    cwd: str = field(default_factory=os.getcwd)
    shell: str = field(default_factory=lambda: os.environ.get("SHELL", "/bin/sh"))
    text: str = ""

    def intersects(self, other: Component) -> bool:
        return (self.x < other.x + other.w and self.x + self.w > other.x
                and self.y < other.y + other.h and self.y + self.h > other.y)


@dataclass
class WorkspaceTab:
    name: str = "工作空间"
    id: str = field(default_factory=new_id)
    components: list[Component] = field(default_factory=list)

    def place(self, item: Component, x: int, y: int, w: int | None = None, h: int | None = None) -> None:
        item.w = max(3, min(COLUMNS, item.w if w is None else w))
        item.h = max(3, min(40, item.h if h is None else h))
        item.x = max(0, min(COLUMNS - item.w, x))
        item.y = max(0, min(1000, y))
        # The moved card owns its requested cells; displaced cards flow down.
        placed = [item]
        for other in sorted((c for c in self.components if c.id != item.id), key=lambda c: (c.y, c.x)):
            while any(other.intersects(c) for c in placed):
                other.y = max(c.y + c.h for c in placed if other.intersects(c))
            placed.append(other)

    def add(self, kind: str, title: str = "", cwd: str = "", shell: str = "") -> Component:
        if kind not in KINDS:
            raise ValueError("未知组件类型")
        item = Component(kind=kind, title=title or KINDS[kind], w=6 if kind == "terminal" else 4,
                         h=8 if kind == "terminal" else 5)
        if cwd:
            item.cwd = str(Path(cwd).expanduser().resolve())
        if shell:
            item.shell = shell
        # Fill the first available rectangle, without disturbing existing cards.
        for y in range(max((c.y + c.h for c in self.components), default=0) + 1):
            for x in range(COLUMNS - item.w + 1):
                item.x, item.y = x, y
                if not any(item.intersects(c) for c in self.components):
                    self.components.append(item)
                    return item
        raise RuntimeError("无法放置组件")


@dataclass
class Workspace:
    version: int = 2
    active_tab: str = ""
    tabs: list[WorkspaceTab] = field(default_factory=list)

    @classmethod
    def default(cls) -> Workspace:
        tab = WorkspaceTab(name="开发")
        tab.components = [
            Component(title="主终端", x=0, y=0, w=8, h=11),
            Component(kind="notes", title="便笺", x=8, y=0, w=4, h=11),
        ]
        return cls(active_tab=tab.id, tabs=[tab])

    @property
    def current(self) -> WorkspaceTab:
        return next(t for t in self.tabs if t.id == self.active_tab)


class WorkspaceStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "workspace.json"

    def load(self) -> Workspace:
        if not self.path.exists():
            return Workspace.default()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") not in (1, 2):
                raise ValueError("不支持的配置版本")
            tabs = []
            ids = set()
            for raw in data["tabs"]:
                self._check_id(raw["id"], ids)
                if not isinstance(raw["name"], str) or not raw["name"].strip():
                    raise ValueError("Tab 名称无效")
                tab = WorkspaceTab(name=raw["name"], id=raw["id"])
                for card in raw["components"]:
                    self._check_id(card["id"], ids)
                    item = Component(**card)
                    if item.kind not in KINDS or not all(isinstance(getattr(item, k), str) for k in ("title", "cwd", "shell", "text")):
                        raise ValueError("组件数据无效")
                    if item.kind == "notes" and item.text in LEGACY_INTRO_NOTES:
                        item.text = ""
                    if not all(type(getattr(item, k)) is int for k in ("x", "y", "w", "h")):
                        raise ValueError("组件坐标无效")
                    if not (3 <= item.w <= 12 and 3 <= item.h <= 40 and 0 <= item.x <= 12-item.w and 0 <= item.y <= 100000):
                        raise ValueError("组件坐标超出范围")
                    if any(item.intersects(c) for c in tab.components):
                        raise ValueError("组件布局重叠")
                    tab.components.append(item)
                tabs.append(tab)
            if not tabs or data["active_tab"] not in {t.id for t in tabs}:
                raise ValueError("缺少活动 Tab")
            if data["version"] == 1:
                self._remove_starter_system(tabs[0])
            return Workspace(active_tab=data["active_tab"], tabs=tabs)
        except (TypeError, KeyError, AttributeError, ValueError) as error:
            # Refuse to overwrite invalid user configuration with an empty workspace.
            raise ValueError(f"无法读取 {self.path}：{error}。原文件未修改。") from error

    @staticmethod
    def _remove_starter_system(tab: WorkspaceTab) -> None:
        """Migrate the original starter card once; keep custom monitoring cards."""
        system = next((c for c in tab.components if c.kind == "system" and c.title == "系统状态"
                       and (c.x, c.y, c.w, c.h) == (8, 6, 4, 5)), None)
        notes = next((c for c in tab.components if c.kind == "notes"
                      and (c.x, c.y, c.w, c.h) == (8, 0, 4, 6)), None)
        if system is not None and notes is not None:
            tab.components.remove(system)
            notes.h += system.h

    @staticmethod
    def _check_id(value: str, seen: set) -> None:
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value) or value in seen:
            raise ValueError("重复或无效的 ID")
        seen.add(value)

    def save(self, workspace: Workspace) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix="workspace-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(asdict(workspace), file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
