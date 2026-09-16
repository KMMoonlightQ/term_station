from unittest.mock import AsyncMock

import pytest

from term_station.app import TermStation
from term_station.dividers import Divider
from term_station.model import Component, Workspace, WorkspaceStore
from term_station.widgets import Dashboard, NotesView, Panel
from conftest import mouse_input


def test_connected_divider_resizes_all_three_panels_and_clamps():
    left = Component(x=0, y=0, w=6, h=12)
    top = Component(x=6, y=0, w=6, h=6)
    bottom = Component(x=6, y=6, w=6, h=6)
    divider = Divider("x", left, top, [left, top, bottom])
    divider.resize(2)
    assert (left.x, left.w, top.x, top.w, bottom.x, bottom.w) == (0, 8, 8, 4, 8, 4)
    divider.resize(100)
    assert (left.w, top.x, top.w, bottom.x, bottom.w) == (9, 9, 3, 9, 3)
    divider.resize(-100)
    assert (left.w, top.x, top.w, bottom.x, bottom.w) == (3, 3, 9, 3, 9)
    assert (left.y, left.h, top.y, top.h, bottom.y, bottom.h) == (0, 12, 0, 6, 6, 6)


def test_disconnected_dividers_do_not_move_other_row():
    top_left = Component(x=0, y=0, w=6, h=6)
    top_right = Component(x=6, y=0, w=6, h=6)
    bottom_left = Component(x=0, y=6, w=6, h=6)
    bottom_right = Component(x=6, y=6, w=6, h=6)
    panels = [top_left, top_right, bottom_left, bottom_right]
    Divider("x", top_left, top_right, panels).resize(2)
    assert (top_left.w, top_right.x, top_right.w) == (8, 8, 4)
    assert (bottom_left.x, bottom_left.w, bottom_right.x, bottom_right.w) == (0, 6, 6, 6)


def test_partial_divider_stops_before_unconnected_panel():
    left = Component(x=0, y=0, w=6, h=6)
    right = Component(x=6, y=3, w=6, h=6)
    obstacle = Component(x=3, y=6, w=3, h=3)
    Divider("x", left, right, [left, right, obstacle]).resize(-2)
    assert (left.w, right.x, right.w) == (6, 6, 6)


def offline_app(tmp_path, monkeypatch, components):
    workspace = Workspace.default()
    workspace.current.components = components
    app = TermStation(tmp_path, workspace)
    monkeypatch.setattr(app, "poll_loop", AsyncMock())
    return app


@pytest.mark.parametrize("inner_column", [-2, -1, 0, 1])
async def test_shared_vertical_drag_from_either_border_or_inner_column(tmp_path, monkeypatch, inner_column):
    left = Component(kind="notes", x=0, y=0, w=6, h=12)
    right = Component(kind="notes", x=6, y=0, w=6, h=12)
    app = offline_app(tmp_path, monkeypatch, [left, right])
    async with app.run_test(size=(120, 49)) as pilot:
        await pilot.pause()
        board = app.query_one(Dashboard)
        panels = list(app.query(Panel))
        start = (panels[1].region.x+inner_column, 10)
        await mouse_input(pilot, "move", start)
        assert not any(p.has_class("edge-hover") for p in panels)
        assert app.screen._pointer_shape == "ew-resize"
        await mouse_input(pilot, "down", start)
        assert all(p.has_class("edge-hover") for p in panels)
        assert board.drag_target.divider is not None
        await mouse_input(pilot, "move", (start[0]+20, start[1]))
        # The resize is visible before releasing the mouse.
        assert (left.x, left.w, right.x, right.w) == (0, 8, 8, 4)
        assert panels[0].region.right == panels[1].region.x == 80
        await mouse_input(pilot, "up", (start[0]+20, start[1]))
        assert app.mouse_captured is None
        assert not any(p.dragging for p in panels)
        assert (left.y, left.h, right.y, right.h) == (0, 12, 0, 12)
        saved = WorkspaceStore(tmp_path).load().current.components
        assert [(c.id, c.x, c.w) for c in saved] == [(left.id, 0, 8), (right.id, 8, 4)]
        await mouse_input(pilot, "move", (30, 10))
        assert not any(p.has_class("edge-hover") for p in panels)
        assert board.hover_pointer == "default"
        assert app.screen._pointer_shape == "text"
        assert all(p.query_one(NotesView).text == "" for p in panels)


async def test_border_highlight_only_appears_after_hover_delay(tmp_path, monkeypatch):
    left = Component(kind="notes", x=0, y=0, w=6, h=12)
    right = Component(kind="notes", x=6, y=0, w=6, h=12)
    app = offline_app(tmp_path, monkeypatch, [left, right])
    async with app.run_test(size=(120, 49)) as pilot:
        await pilot.pause()
        board = app.query_one(Dashboard)
        panels = list(app.query(Panel))
        border = (panels[1].region.x, 10)

        await mouse_input(pilot, "move", border)
        assert app.screen._pointer_shape == "ew-resize"
        assert not any(panel.has_class("edge-hover") for panel in panels)

        # Passing over the border before the delay expires must not flash it.
        await mouse_input(pilot, "move", (30, 10))
        await pilot.pause(board.HOVER_HIGHLIGHT_DELAY + 0.03)
        assert not any(panel.has_class("edge-hover") for panel in panels)

        await mouse_input(pilot, "move", border)
        await pilot.pause(board.HOVER_HIGHLIGHT_DELAY + 0.03)
        assert all(panel.has_class("edge-hover") for panel in panels)


async def test_lower_panel_top_border_resizes_both_heights(tmp_path, monkeypatch):
    top = Component(kind="notes", x=0, y=0, w=12, h=6)
    bottom = Component(kind="notes", x=0, y=6, w=12, h=6)
    app = offline_app(tmp_path, monkeypatch, [top, bottom])
    async with app.run_test(size=(120, 49)) as pilot:
        await pilot.pause()
        panels = list(app.query(Panel))
        start = (30, panels[1].region.y)
        await mouse_input(pilot, "move", start)
        assert app.screen._pointer_shape == "ns-resize"
        await mouse_input(pilot, "down", start)
        await mouse_input(pilot, "move", (start[0], start[1]+8))
        await mouse_input(pilot, "up", (start[0], start[1]+8))
        assert (top.y, top.h, bottom.y, bottom.h) == (0, 8, 8, 4)
        assert panels[0].region.bottom == panels[1].region.y == 32
        assert panels[1].region.bottom == 48
        assert (top.x, top.w, bottom.x, bottom.w) == (0, 12, 0, 12)
        saved = WorkspaceStore(tmp_path).load().current.components
        assert [(c.id, c.y, c.h) for c in saved] == [(top.id, 0, 8), (bottom.id, 8, 4)]


async def test_t_junction_drag_resizes_all_connected_panels(tmp_path, monkeypatch):
    left = Component(kind="notes", x=0, y=0, w=6, h=12)
    top = Component(kind="notes", x=6, y=0, w=6, h=6)
    bottom = Component(kind="notes", x=6, y=6, w=6, h=6)
    app = offline_app(tmp_path, monkeypatch, [left, top, bottom])
    async with app.run_test(size=(120, 49)) as pilot:
        await pilot.pause()
        await mouse_input(pilot, "down", (60, 10))
        assert app.screen._pointer_shape == "ew-resize"
        await mouse_input(pilot, "move", (80, 10))
        await mouse_input(pilot, "up", (80, 10))
        assert (left.w, top.x, top.w, bottom.x, bottom.w) == (8, 8, 4, 8, 4)
        panels = list(app.query(Panel))
        assert panels[0].region.right == panels[1].region.x == panels[2].region.x == 80
        assert panels[1].region.bottom == panels[2].region.y
