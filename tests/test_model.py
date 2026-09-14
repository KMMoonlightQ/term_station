import json
from dataclasses import asdict

import pytest

from term_station.model import Component, Workspace, WorkspaceStore, WorkspaceTab


def test_roundtrip_preserves_every_setting(tmp_path):
    workspace = Workspace.default()
    item = workspace.current.add("terminal", "API 服务", str(tmp_path), "/bin/sh")
    workspace.current.place(item, 2, 15, 7, 12)
    workspace.current.components[1].text = "中文便笺\n[x] 已完成"
    tab = WorkspaceTab(name="监控")
    tab.add("clock")
    workspace.tabs.append(tab)
    workspace.active_tab = tab.id
    store = WorkspaceStore(tmp_path)
    store.save(workspace)
    assert asdict(store.load()) == asdict(workspace)
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.tmp"))


def test_drag_and_resize_push_collisions_without_overlap():
    tab = WorkspaceTab(components=[Component(x=0,y=0,w=6,h=5), Component(x=6,y=0,w=6,h=5), Component(x=6,y=5,w=6,h=5)])
    moved = tab.components[0]
    tab.place(moved, 6, 0)
    assert (moved.x, moved.y) == (6, 0)
    assert sorted(c.y for c in tab.components) == [0, 5, 10]
    tab.place(moved, -9, -9, 90, 0)
    assert (moved.x, moved.y, moved.w, moved.h) == (0, 0, 12, 3)
    for first in tab.components:
        assert all(not first.intersects(second) for second in tab.components if first != second)


@pytest.mark.parametrize("raw", ['{broken', '{"version":99}', '{"version":1,"tabs":[]}'])
def test_invalid_configuration_is_never_silently_replaced(tmp_path, raw):
    store = WorkspaceStore(tmp_path)
    store.path.write_text(raw)
    with pytest.raises(ValueError, match="原文件未修改"):
        store.load()
    assert store.path.read_text() == raw


def test_many_components_fill_grid_without_overlap():
    tab = WorkspaceTab()
    for i in range(45):
        tab.add(["terminal", "notes", "system", "clock"][i % 4])
    for item in tab.components:
        assert item.x + item.w <= 12
        assert all(not item.intersects(other) for other in tab.components if item != other)


def test_duplicate_ids_rejected(tmp_path):
    workspace = Workspace.default()
    workspace.current.components[1].id = workspace.current.components[0].id
    store = WorkspaceStore(tmp_path)
    store.path.write_text(json.dumps(asdict(workspace)))
    with pytest.raises(ValueError, match="重复"):
        store.load()


def test_remove_old_sample_notes_without_changing_edited_notes(tmp_path):
    from term_station.model import LEGACY_INTRO_NOTES

    workspace = Workspace.default()
    sample = next(iter(LEGACY_INTRO_NOTES))
    workspace.current.components[1].text = sample
    edited = workspace.current.add("notes")
    edited.text = sample + "\n我的任务"
    store = WorkspaceStore(tmp_path)
    store.save(workspace)
    restored = store.load()
    assert restored.current.components[1].text == ""
    assert restored.current.components[-1].text == edited.text


def test_remove_starter_system_once_and_preserve_terminal_and_notes(tmp_path):
    workspace = Workspace.default()
    workspace.version = 1
    terminal, notes = workspace.current.components
    notes.h = 6
    notes.text = "保留我的便笺"
    system = Component(kind="system", title="系统状态", x=8, y=6, w=4, h=5)
    workspace.current.components.append(system)
    store = WorkspaceStore(tmp_path)
    store.save(workspace)
    restored = store.load()
    assert restored.version == 2
    assert [c.kind for c in restored.current.components] == ["terminal", "notes"]
    assert asdict(restored.current.components[0]) == asdict(terminal)
    assert restored.current.components[1].text == notes.text
    assert restored.current.components[1].h == 11
    # Re-adding a monitoring card after migration remains an explicit user choice.
    restored.current.components[1].h = 6
    restored.current.components.append(system)
    store.save(restored)
    assert asdict(store.load()) == asdict(restored)
