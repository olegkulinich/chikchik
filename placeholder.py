"""E3 (B): a plain dark window shown before Qt is even imported.

Startup to the real window is ~600 ms (Python + PySide6 + OpenGL). This shows an empty
window in the saved place at ~150 ms, made with bare WinAPI (no Python callbacks: the
window procedure is DefWindowProcW, the background a solid brush), in its own thread
with its own message loop. The real window opens cloaked on top of it; once it is
uncloaked, the placeholder is destroyed.

Hidden switch QTC_PLACEHOLDER (Windows only):
  0 / unset — off (default until the user compares)
  1 — placeholder without the system open animation (DWMWA_TRANSITIONS_FORCEDISABLED)
  2 — placeholder with the system open animation (fade + grow, like other apps)

Where: `window/placeholder` in the settings (registry), written on close by MainWindow:
"show_cmd,left,top,right,bottom,mode" — WINDOWPLACEMENT normal rect (workspace
coordinates, physical px) + normal/minimal/hidden. Nothing saved yet → no placeholder.

No Qt imports: app.py starts this before gl_warm and PySide6.
"""
from __future__ import annotations

import os
import sys
import threading

import startup_log

_value = os.environ.get("QTC_PLACEHOLDER", "").strip()
LEVEL = int(_value) if _value.isdigit() else 0

SETTINGS_KEY = r"Software\QuickTrimCrop\QuickTrimCrop\window"   # QSettings group "window"
VALUE_NAME = "placeholder"
WINDOW_RGB = (0x1E, 0x1E, 0x1E)       # theme.WINDOW
CAPTION_TEXT_RGB = (0x75, 0x75, 0x75)  # theme.DIM

_hwnd = None
_thread_id = None
_lock = threading.Lock()


def parse(text: str):
    """"show_cmd,l,t,r,b,mode" → (show_cmd, (l, t, r, b), mode) or None."""
    try:
        parts = text.split(",")
        show_cmd = int(parts[0])
        rect = tuple(int(p) for p in parts[1:5])
        mode = parts[5] if len(parts) > 5 else "normal"
    except (ValueError, IndexError):
        return None
    if rect[2] - rect[0] < 100 or rect[3] - rect[1] < 100:
        return None
    return show_cmd, rect, mode


def _read_saved():
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SETTINGS_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
    except OSError:
        return None
    return parse(str(value))


def _title(argv: list[str]) -> str:
    paths = [a for a in argv[1:] if a.strip() and a != "--gif"]
    name = os.path.basename(paths[0]) if paths else ""
    return f"ChikChik! — {name}" if name else "ChikChik!"


def _set_dpi_awareness() -> None:
    """Before any window of the process (gl_warm makes one too): the same per-monitor v2
    awareness Qt 6 sets, so the saved physical-pixel rect means the same here."""
    import ctypes

    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass


def _colorref(rgb: tuple[int, int, int]) -> int:
    return rgb[0] | rgb[1] << 8 | rgb[2] << 16


def _run(level: int, saved, title: str) -> None:
    global _hwnd, _thread_id
    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    dwm = ctypes.WinDLL("dwmapi")

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint32), ("style", ctypes.c_uint32),
                    ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int32),
                    ("cbWndExtra", ctypes.c_int32), ("hInstance", ctypes.c_void_p),
                    ("hIcon", ctypes.c_void_p), ("hCursor", ctypes.c_void_p),
                    ("hbrBackground", ctypes.c_void_p), ("lpszMenuName", ctypes.c_wchar_p),
                    ("lpszClassName", ctypes.c_wchar_p), ("hIconSm", ctypes.c_void_p)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_int32), ("top", ctypes.c_int32),
                    ("right", ctypes.c_int32), ("bottom", ctypes.c_int32)]

    class WINDOWPLACEMENT(ctypes.Structure):
        _fields_ = [("length", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                    ("showCmd", ctypes.c_uint32), ("ptMinPosition", POINT),
                    ("ptMaxPosition", POINT), ("rcNormalPosition", RECT)]

    class MARGINS(ctypes.Structure):
        _fields_ = [("left", ctypes.c_int32), ("right", ctypes.c_int32),
                    ("top", ctypes.c_int32), ("bottom", ctypes.c_int32)]

    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint32),
                    ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                    ("time", ctypes.c_uint32), ("pt", POINT), ("lPrivate", ctypes.c_uint32)]

    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetCurrentThreadId.restype = ctypes.c_uint32
    user32.RegisterClassExW.restype = ctypes.c_uint16
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.CreateWindowExW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                       ctypes.c_uint32, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    user32.LoadCursorW.restype = ctypes.c_void_p
    user32.LoadCursorW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.SetWindowPlacement.argtypes = [ctypes.c_void_p, ctypes.POINTER(WINDOWPLACEMENT)]
    user32.UpdateWindow.argtypes = [ctypes.c_void_p]
    user32.GetMessageW.restype = ctypes.c_int
    user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), ctypes.c_void_p, ctypes.c_uint32,
                                   ctypes.c_uint32]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    gdi32.CreateSolidBrush.restype = ctypes.c_void_p
    gdi32.CreateSolidBrush.argtypes = [ctypes.c_uint32]
    shell32.ExtractIconW.restype = ctypes.c_void_p
    shell32.ExtractIconW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
    dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                          ctypes.c_uint32]
    dwm.DwmExtendFrameIntoClientArea.argtypes = [ctypes.c_void_p, ctypes.POINTER(MARGINS)]

    show_cmd, rect, mode = saved
    hinst = kernel32.GetModuleHandleW(None)
    icon = shell32.ExtractIconW(hinst, sys.executable, 0)
    icon = icon if icon and icon != 1 else None        # 1 = the file has no icon
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.style = 0
    wc.lpfnWndProc = ctypes.cast(user32.DefWindowProcW, ctypes.c_void_p).value  # no Python
    wc.hInstance = hinst
    wc.hIcon = wc.hIconSm = icon
    wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32512))               # IDC_ARROW
    wc.hbrBackground = gdi32.CreateSolidBrush(_colorref(WINDOW_RGB))
    wc.lpszClassName = "ChikChikPlaceholder"
    if not user32.RegisterClassExW(ctypes.byref(wc)):
        startup_log.mark("placeholder: no window class")
        return

    ws_overlappedwindow, ws_popup = 0x00CF0000, 0x80000000
    style = ws_popup if mode == "hidden" else ws_overlappedwindow   # Hide UI: no frame drawn
    hwnd = user32.CreateWindowExW(0, wc.lpszClassName, title, style,
                                  rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1],
                                  None, None, hinst, None)
    if not hwnd:
        startup_log.mark("placeholder: no window")
        return

    def attr(index: int, value: int) -> None:
        v = ctypes.c_int(value)
        dwm.DwmSetWindowAttribute(hwnd, index, ctypes.byref(v), ctypes.sizeof(v))

    attr(20, 1)                                     # dark mode title bar
    attr(35, _colorref(WINDOW_RGB))                 # caption colour (theme.dark_title_bar)
    attr(36, _colorref(CAPTION_TEXT_RGB))           # caption text
    if level == 1:
        attr(3, 1)                                  # DWMWA_TRANSITIONS_FORCEDISABLED
    if mode == "hidden":
        margins = MARGINS(0, 0, 1, 0)               # shadow, as win_frame does
        dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))

    placement = WINDOWPLACEMENT()
    placement.length = ctypes.sizeof(WINDOWPLACEMENT)
    placement.showCmd = show_cmd if show_cmd in (1, 3) else 1   # SW_SHOWNORMAL / MAXIMIZED
    placement.rcNormalPosition = RECT(*rect)
    with _lock:
        _hwnd = hwnd
        _thread_id = kernel32.GetCurrentThreadId()
    user32.SetWindowPlacement(hwnd, ctypes.byref(placement))   # shows it, in workspace coords
    user32.UpdateWindow(hwnd)
    startup_log.mark(f"placeholder: shown (level {level}, {mode}, show {placement.showCmd})")

    msg = MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:   # releases the GIL
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    with _lock:
        _hwnd = None
    startup_log.mark("placeholder: thread done")


def _guarded(level: int, saved, title: str) -> None:
    try:
        _run(level, saved, title)
    except Exception as exc:                      # never let it hurt the start
        startup_log.mark(f"placeholder: failed · {exc}")


def start(argv: list[str]) -> None:
    if sys.platform != "win32" or LEVEL <= 0:
        return
    saved = _read_saved()
    if saved is None:
        startup_log.mark("placeholder: nothing saved yet")
        return
    _set_dpi_awareness()
    threading.Thread(target=_guarded, args=(LEVEL, saved, _title(argv)),
                     name="placeholder", daemon=True).start()


def close() -> None:
    """From the GUI thread once the real window is on screen (it is above: shown later)."""
    if sys.platform != "win32":
        return
    import ctypes

    with _lock:
        hwnd, thread_id = _hwnd, _thread_id
    if not hwnd:
        return
    user32 = ctypes.windll.user32
    user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t,
                                    ctypes.c_ssize_t]
    user32.PostThreadMessageW.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t,
                                          ctypes.c_ssize_t]
    user32.PostMessageW(hwnd, 0x0010, 0, 0)          # WM_CLOSE → DefWindowProc destroys it
    user32.PostThreadMessageW(thread_id, 0x0012, 0, 0)   # WM_QUIT after the queue empties
    startup_log.mark("placeholder: closed")


def save_value(hwnd) -> str | None:
    """MainWindow.closeEvent (Windows): the string for `window/placeholder`."""
    if sys.platform != "win32" or not hwnd:
        return None
    import ctypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_int32), ("top", ctypes.c_int32),
                    ("right", ctypes.c_int32), ("bottom", ctypes.c_int32)]

    class WINDOWPLACEMENT(ctypes.Structure):
        _fields_ = [("length", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                    ("showCmd", ctypes.c_uint32), ("ptMinPosition", POINT),
                    ("ptMaxPosition", POINT), ("rcNormalPosition", RECT)]

    placement = WINDOWPLACEMENT()
    placement.length = ctypes.sizeof(WINDOWPLACEMENT)
    get = ctypes.windll.user32.GetWindowPlacement
    get.argtypes = [ctypes.c_void_p, ctypes.POINTER(WINDOWPLACEMENT)]
    if not get(ctypes.c_void_p(int(hwnd)), ctypes.byref(placement)):
        return None
    r = placement.rcNormalPosition
    show = 3 if placement.showCmd == 3 else 1
    return f"{show},{r.left},{r.top},{r.right},{r.bottom}"
