"""T27 Hide UI on Windows: keep the native window (snap, Win+arrows, shadow, resize),
just don't draw its frame — WM_NCCALCSIZE gives the whole window to the client area,
WM_NCHITTEST hands the edges and our hover title strip back to Windows.

Everything here is a no-op on other platforms."""
from __future__ import annotations

import sys

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084

HTCAPTION = 2
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17

ENABLED = sys.platform == "win32"

if ENABLED:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _dwm = ctypes.windll.dwmapi

    class _WINDOWPOS(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("hwndInsertAfter", wintypes.HWND),
                    ("x", ctypes.c_int), ("y", ctypes.c_int),
                    ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", wintypes.UINT)]

    class _NCCALCSIZE_PARAMS(ctypes.Structure):
        _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.POINTER(_WINDOWPOS))]

    class _MARGINS(ctypes.Structure):
        _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int),
                    ("top", ctypes.c_int), ("bottom", ctypes.c_int)]


VK_OEM_5 = 0xDC          # the physical \\ | key on Windows, whatever the layout


def is_backslash_key(event) -> bool:
    """T27: Ctrl+\\ / Ctrl+Shift+\\ by the physical key — with Shift the same key types
    | (EN) or / (RU), and Qt matches shortcuts by the character, so Ctrl+Shift+\\
    never fired. Figma works by the key, so do we."""
    return ENABLED and event.nativeVirtualKey() == VK_OEM_5


def message(event_type, message_ptr):
    """(msg id, hwnd, wParam, lParam) of a native Windows message, or None."""
    if not ENABLED or bytes(event_type) != b"windows_generic_MSG":
        return None
    msg = wintypes.MSG.from_address(int(message_ptr))
    return msg.message, msg.hWnd, msg.wParam, msg.lParam


def calc_size(hwnd, wparam, lparam) -> bool:
    """WM_NCCALCSIZE: client area = the whole window. A maximized window hangs its
    frame off-screen, so pull the client rect back in by the frame thickness."""
    if not wparam:
        return False
    if _user32.IsZoomed(hwnd):
        params = _NCCALCSIZE_PARAMS.from_address(lparam)
        try:
            dpi = _user32.GetDpiForWindow(hwnd)
            padded = _user32.GetSystemMetricsForDpi(92, dpi)          # SM_CXPADDEDBORDER
            fx = _user32.GetSystemMetricsForDpi(32, dpi) + padded     # SM_CXSIZEFRAME
            fy = _user32.GetSystemMetricsForDpi(33, dpi) + padded     # SM_CYSIZEFRAME
        except Exception:
            padded = _user32.GetSystemMetrics(92)
            fx = _user32.GetSystemMetrics(32) + padded
            fy = _user32.GetSystemMetrics(33) + padded
        rect = params.rgrc[0]
        rect.left += fx
        rect.top += fy
        rect.right -= fx
        rect.bottom -= fy
    return True


def refresh(widget, hidden: bool) -> None:
    """Re-run WM_NCCALCSIZE after switching; a 1 px DWM margin keeps the shadow."""
    if not ENABLED:
        return
    try:
        hwnd = int(widget.winId())
        margins = _MARGINS(0, 0, 1 if hidden else 0, 0)
        _dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        # SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
        _user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0004 | 0x0010 | 0x0020)
    except Exception:
        pass


def edge_hit(x: int, y: int, width: int, height: int, band: int) -> int:
    """Resize edges / corners for a point in window coordinates, 0 inside."""
    left, right = x < band, x >= width - band
    top, bottom = y < band, y >= height - band
    if top and left:
        return HTTOPLEFT
    if top and right:
        return HTTOPRIGHT
    if bottom and left:
        return HTBOTTOMLEFT
    if bottom and right:
        return HTBOTTOMRIGHT
    if left:
        return HTLEFT
    if right:
        return HTRIGHT
    if top:
        return HTTOP
    if bottom:
        return HTBOTTOM
    return 0
