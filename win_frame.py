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


SCAN_SLASH = 0x35        # the physical / ? key (US position), whatever the layout


def is_slash_key(event) -> bool:
    """T50b: Ctrl+/ by the physical key. On a Russian layout that key types "." — its
    virtual-key code follows the layout, the scan code doesn't (numpad / is 0x135)."""
    return ENABLED and event.nativeScanCode() == SCAN_SLASH


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


# ------------------------------------------------ T44 / T45 (group 6: window as PiP)
WM_SIZING = 0x0214
WMSZ_LEFT, WMSZ_RIGHT, WMSZ_TOP, WMSZ_TOPLEFT = 1, 2, 3, 4
WMSZ_TOPRIGHT, WMSZ_BOTTOM, WMSZ_BOTTOMLEFT, WMSZ_BOTTOMRIGHT = 5, 6, 7, 8
_VK_SHIFT = 0x10


def shift_down() -> bool:
    """Shift held right now (WM_SIZING carries no key state)."""
    return ENABLED and bool(_user32.GetAsyncKeyState(_VK_SHIFT) & 0x8000)


def sizing_rect(lparam):
    """The RECT that WM_SIZING lets us change in place (window rect, physical px)."""
    return wintypes.RECT.from_address(int(lparam))


def keep_aspect(rect, edge: int, extra_w: int, extra_h: int, aspect: float,
                min_w: int, min_h: int, max_w: int, max_h: int) -> None:
    """T44: change the dragged RECT so the video area (window minus `extra`) keeps
    `aspect` (w/h). The dragged edge leads: left/right → width, top/bottom → height,
    a corner → whichever moved more. The opposite edge/corner stays put. At the
    window's minimum it stops at the smallest size that keeps the aspect, at the
    screen's size it stops growing; when the two disagree (a tall video with the full
    panels) the aspect can't be kept and it's a plain resize."""
    w, h = rect.right - rect.left, rect.bottom - rect.top
    free_w, free_h = w, h                # the plain drag, if the aspect can't be kept
    vw, vh = max(1, w - extra_w), max(1, h - extra_h)
    if edge in (WMSZ_LEFT, WMSZ_RIGHT):
        by_width = True
    elif edge in (WMSZ_TOP, WMSZ_BOTTOM):
        by_width = False
    else:   # corner: the axis that moved further from the aspect leads
        by_width = vw / aspect >= vh
    if by_width:
        h = round(max(1, w - extra_w) / aspect) + extra_h
    else:
        w = round(max(1, h - extra_h) * aspect) + extra_w
    # not past the screen: the window stops growing there (keeps the aspect)
    if h > max_h:
        h = max_h
        w = round((h - extra_h) * aspect) + extra_w
    if w > max_w:
        w = max_w
        h = round((w - extra_w) / aspect) + extra_h
    # at the minimum size: stop at the smallest window that still keeps the aspect
    if w < min_w:
        w = min_w
        h = round((w - extra_w) / aspect) + extra_h
    if h < min_h:
        h = min_h
        w = round((h - extra_h) * aspect) + extra_w
    if w > max_w or h > max_h:           # min and screen disagree: a plain resize
        w, h = min(free_w, max_w), min(free_h, max_h)
    w, h = max(w, min_w), max(h, min_h)
    # anchor the opposite side
    if edge in (WMSZ_LEFT, WMSZ_TOPLEFT, WMSZ_BOTTOMLEFT):
        rect.left = rect.right - w
    else:
        rect.right = rect.left + w
    if edge in (WMSZ_TOP, WMSZ_TOPLEFT, WMSZ_TOPRIGHT):
        rect.top = rect.bottom - h
    else:
        rect.bottom = rect.top + h


def set_topmost(hwnd, on: bool) -> None:
    """T45: Pin on Top through WinAPI — Qt's WindowStaysOnTopHint re-creates the
    native window, which breaks the OpenGL view and the Hide UI frame."""
    if not ENABLED or not hwnd:
        return
    swp_nosize, swp_nomove, swp_noactivate = 0x0001, 0x0002, 0x0010
    after = wintypes.HWND(-1 if on else -2)       # HWND_TOPMOST / HWND_NOTOPMOST
    _user32.SetWindowPos(wintypes.HWND(int(hwnd)), after, 0, 0, 0, 0,
                         swp_nosize | swp_nomove | swp_noactivate)


def window_size(hwnd) -> tuple[int, int, int, int] | None:
    """(width, height, invisible_w, invisible_h) of the native window in physical px.
    The size is the same box WM_SIZING hands us — with Windows 11's invisible resize
    borders (Qt's frameGeometry doesn't match it); invisible_* is how much of it
    isn't drawn (DWM's extended frame bounds), so the visible window can fill the screen."""
    if not ENABLED or not hwnd:
        return None
    handle = wintypes.HWND(int(hwnd))
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(handle, ctypes.byref(rect)):
        return None
    w, h = rect.right - rect.left, rect.bottom - rect.top
    shown = wintypes.RECT()
    dwmwa_extended_frame_bounds = 9
    if _dwm.DwmGetWindowAttribute(handle, dwmwa_extended_frame_bounds,
                                  ctypes.byref(shown), ctypes.sizeof(shown)) != 0:
        return w, h, 0, 0
    return w, h, w - (shown.right - shown.left), h - (shown.bottom - shown.top)

