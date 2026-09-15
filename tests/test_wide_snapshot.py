from types import SimpleNamespace

import pyte
import pytest
from rich.cells import cell_len

from term_station.daemon import Session, TerminalScreen


def snapshot(screen):
    session = SimpleNamespace(screen=screen, id="test", revision=1,
                              columns=screen.columns, rows=screen.lines,
                              closed=False, exit_code=None)
    return Session.snapshot(session)


@pytest.mark.parametrize("overwrite,expected_prefix", [
    ("", "中文"),
    ("\x1b[1;1H ", "  文"),
    ("\x1b[1;2H ", "  文"),
    ("\x1b[1;2HX", " X文"),
    ("\x1b[1;2H新", " 新 "),
    ("\x1b[1;2H\x1b[0K", ""),
])
def test_overwritten_wide_cells_preserve_screen_columns(overwrite, expected_prefix):
    screen = TerminalScreen(30, 3, lambda _: None)
    pyte.Stream(screen).feed("中文" + overwrite + "\x1b[1;11H设置")
    line = "".join(run[0] for run in snapshot(screen)["lines"][0])
    assert line.startswith(expected_prefix)
    assert cell_len(line[:line.index("设置")]) == 10
    assert cell_len(line) == 30


def test_menu_rows_stay_aligned_after_old_chinese_text_is_cleared():
    screen = TerminalScreen(50, 8, lambda _: None)
    stream = pyte.Stream(screen)
    labels = ["播放列表", "订阅列表", "推荐列表", "设置", "Enter 进入 · q 退出"]
    for row, (label, count) in enumerate(zip(labels, [2, 2, 2, 3, 4]), start=1):
        stream.feed(f"\x1b[{row};1H" + "旧" * count)
        # Incremental TUI output clears a wide glyph's leading cell only.
        for column in range(1, count * 2, 2):
            stream.feed(f"\x1b[{row};{column}H ")
        stream.feed(f"\x1b[{row};19H{label}")
    for runs, label in zip(snapshot(screen)["lines"], labels):
        line = "".join(run[0] for run in runs)
        assert cell_len(line[:line.index(label)]) == 18
        assert cell_len(line) == 50


def test_resize_clipping_a_wide_character_keeps_snapshot_width():
    screen = TerminalScreen(8, 2, lambda _: None)
    pyte.Stream(screen).feed("abcdef中")
    screen.resize(columns=7, lines=2)
    line = "".join(run[0] for run in snapshot(screen)["lines"][0])
    assert line == "abcdef "
