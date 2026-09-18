"""Terminal defaults must reach the host, including its light/dark palette."""
from term_station.app import TermStation
from term_station.terminal import cell_style


def test_default_cells_use_host_foreground_and_background():
    style = cell_style("default", "default", False, False, False, False, False)
    assert style.color.is_default
    assert style.bgcolor.is_default


def test_app_preserves_host_ansi_palette(tmp_path):
    assert TermStation(tmp_path).native_ansi_color


async def test_rendered_workspace_and_dialog_keep_terminal_defaults(service, tmp_path):
    from textual.widgets import Input
    from term_station.dialogs import NameScreen
    from term_station.terminal import TerminalView
    from term_station.widgets import NotesView, Panel
    from test_app import ready

    app = TermStation(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await ready(app, pilot)
        for widget in [app.screen, *app.query(Panel), app.query_one(NotesView),
                       app.query_one(TerminalView), app.query_one("#command-bar")]:
            assert widget.rich_style.bgcolor.is_default
            assert widget.rich_style.color.is_default
        terminal = app.query_one(TerminalView)
        terminal.apply_frame({"lines": [[["text", "default", "default", False, False, False, False, False]]],
                              "offset": 0, "cursor_hidden": True})
        # Include both content and right/bottom padding after the real app filters.
        for y in (0, terminal.size.height - 1):
            strip = terminal.render_line(y)
            for filter_ in app._filters:
                if filter_.enabled:
                    strip = strip.apply_filter(filter_, terminal.styles.background)
            assert all(segment.style.color.is_default and segment.style.bgcolor.is_default
                       for segment in strip)
        await pilot.press("ctrl+b", "c")
        assert isinstance(app.screen, NameScreen)
        for widget in [app.screen, app.screen.query_one(".dialog"), app.screen.query_one(Input)]:
            assert widget.rich_style.bgcolor.is_default
            assert widget.rich_style.color.is_default
