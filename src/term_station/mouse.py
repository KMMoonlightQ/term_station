"""Encode application mouse reports in PTY cell coordinates."""

TRACKING_MODES = (9, 1000, 1002, 1003)


def encode_mouse(action: str, button: int, x: int, y: int, tracking: int, sgr: bool,
                 shift: bool = False, alt: bool = False, ctrl: bool = False) -> bytes:
    if action not in ("down", "up", "move", "scroll"):
        raise ValueError("无效鼠标事件")
    if type(button) is not int or button not in (-1, 0, 1, 2, 64, 65):
        raise ValueError("无效鼠标按键")
    if any(type(value) is not int or value < 0 for value in (x, y)):
        raise ValueError("无效鼠标坐标")
    if tracking not in TRACKING_MODES:
        return b""
    if tracking == 9 and action != "down":
        return b""
    if action == "move" and (tracking not in (1002, 1003) or tracking == 1002 and button == -1):
        return b""
    if action in ("down", "up") and button not in (0, 1, 2):
        return b""
    if action == "scroll" and button not in (64, 65):
        return b""
    code = 3 if button == -1 else button
    if action == "move":
        code |= 32
    if action == "up" and not sgr:
        code = 3
    if tracking != 9:
        code |= (4 if shift else 0) | (8 if alt else 0) | (16 if ctrl else 0)
    if sgr:
        return f"\x1b[<{code};{x+1};{y+1}{'m' if action == 'up' else 'M'}".encode("ascii")
    # Legacy reports contain raw bytes, not UTF-8 text, and cannot exceed 223 cells.
    if x >= 223 or y >= 223:
        return b""
    return b"\x1b[M" + bytes((code + 32, x + 33, y + 33))
