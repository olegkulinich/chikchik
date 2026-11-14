"""Windows helpers shared by bench_startup.py and stress.py (ctypes only + mss + psutil).

Also the safety controls both scripts honour:
  * mouse in the top-left screen corner → pause (a dialog: continue or stop);
  * Ctrl+Alt+Q → stop now (the summary is still written).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
from dataclasses import dataclass
from pathlib import Path

import mss
import mss.tools
import psutil

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

# Physical pixels everywhere (captures, cursor, window rects).
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # per-monitor v2
except Exception:
    pass

WM_NULL, WM_CLOSE, WM_HOTKEY = 0x0000, 0x0010, 0x0312
SMTO_ABORTIFHUNG = 0x0002
GW_OWNER = 4
DWMWA_CLOAKED = 14
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000
PM_REMOVE = 0x1
MB_OK, MB_YESNO, MB_ICONINFORMATION, MB_ICONWARNING = 0x0, 0x4, 0x40, 0x30
MB_TOPMOST, MB_SETFOREGROUND, IDYES = 0x40000, 0x10000, 6
ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x1, 0x2
SW_RESTORE = 9

EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wt.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindow.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
user32.GetWindow.restype = wt.HWND
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT,
                                       wt.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageTimeoutW.restype = ctypes.c_size_t
user32.GetForegroundWindow.restype = wt.HWND
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.GetDpiForWindow.argtypes = [wt.HWND]
user32.GetDpiForWindow.restype = wt.UINT
user32.MessageBoxW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT]
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
kernel32.SetThreadExecutionState.argtypes = [wt.DWORD]


# ------------------------------------------------------------------ windows
@dataclass
class Window:
    hwnd: int
    pid: int
    cls: str
    title: str
    rect: tuple[int, int, int, int]      # client area on screen: left, top, width, height

    @property
    def area(self) -> int:
        return self.rect[2] * self.rect[3]


def _text(func, hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    func(hwnd, buffer, 256)
    return buffer.value


def cloaked(hwnd: int) -> bool:
    value = wt.DWORD()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value),
                                    ctypes.sizeof(value)) != 0:
        return False
    return value.value != 0


def client_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    point = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(point))
    return point.x, point.y, rect.right - rect.left, rect.bottom - rect.top


def process_tree(pid: int) -> set[int]:
    try:
        root = psutil.Process(pid)
        return {pid} | {child.pid for child in root.children(recursive=True)}
    except psutil.Error:
        return set()


def windows_of(pids: set[int], min_w: int = 200, min_h: int = 120) -> list[Window]:
    """Visible, not cloaked, not minimized, unowned top-level windows of these processes,
    largest first. A cloaked window (ChikChik before its first frame) does not count."""
    found: list[Window] = []

    def callback(hwnd, _):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        if user32.GetWindow(hwnd, GW_OWNER) or cloaked(hwnd):
            return True
        rect = client_rect(hwnd)
        if rect[2] >= min_w and rect[3] >= min_h:
            found.append(Window(hwnd, pid.value, _text(user32.GetClassNameW, hwnd),
                                _text(user32.GetWindowTextW, hwnd), rect))
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    found.sort(key=lambda w: w.area, reverse=True)
    return found


def responds(hwnd: int, timeout_ms: int = 5000) -> bool:
    """False if the window's thread does not answer WM_NULL in time (Windows "Not
    Responding" uses the same idea)."""
    result = ctypes.c_size_t()
    return bool(user32.SendMessageTimeoutW(hwnd, WM_NULL, 0, 0, SMTO_ABORTIFHUNG,
                                           timeout_ms, ctypes.byref(result)))


def dpi_scale(hwnd: int) -> float:
    try:
        return (user32.GetDpiForWindow(hwnd) or 96) / 96
    except Exception:
        return 1.0


def close_process(proc: psutil.Popen | psutil.Process, grace: float = 4.0) -> str:
    """WM_CLOSE to its windows, then kill the whole tree if it is still there."""
    pids = process_tree(proc.pid)
    for window in windows_of(pids, 1, 1):
        user32.PostMessageW(window.hwnd, WM_CLOSE, 0, 0)
    deadline = time.perf_counter() + grace
    while time.perf_counter() < deadline:
        if not any(psutil.pid_exists(p) for p in pids):
            return "closed"
        time.sleep(0.05)
    for pid in pids:
        try:
            psutil.Process(pid).kill()
        except psutil.Error:
            pass
    return "killed"


def running_instances(exe: Path) -> list[int]:
    target = str(exe).lower()
    pids = []
    for proc in psutil.process_iter(["exe"]):
        try:
            if (proc.info["exe"] or "").lower() == target:
                pids.append(proc.pid)
        except psutil.Error:
            pass
    return pids


def focus(hwnd: int) -> bool:
    if user32.GetForegroundWindow() == hwnd:
        return True
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    # A lone Alt tap lifts the foreground lock for this process.
    press("Alt")
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    time.sleep(0.05)
    return user32.GetForegroundWindow() == hwnd


# ------------------------------------------------------------------ input
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("padding", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x1, 0x2
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x2, 0x4

VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "space": 0x20, "left": 0x25, "up": 0x26,
      "right": 0x27, "down": 0x28, "home": 0x24, "end": 0x23, "pageup": 0x21,
      "pagedown": 0x22, "esc": 0x1B, "enter": 0x0D, "tab": 0x09}
EXTENDED = {0x25, 0x26, 0x27, 0x28, 0x24, 0x23, 0x21, 0x22}


def _key_input(vk: int, up: bool) -> INPUT:
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0)
    item = INPUT(type=1)
    item.u.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
    return item


def press(combo: str) -> None:
    """'Space', 'Right', 'Ctrl+Right', 'Shift+Left', a letter 'L'."""
    parts = [p.strip().lower() for p in combo.split("+")]
    codes = []
    for part in parts:
        if part in VK:
            codes.append(VK[part])
        elif len(part) == 1:
            codes.append(ord(part.upper()))
        else:
            raise ValueError(f"Unknown key: {part}")
    events = [(c, False) for c in codes] + [(c, True) for c in reversed(codes)]
    for vk, up in events:
        user32.SendInput(1, ctypes.byref(_key_input(vk, up)), ctypes.sizeof(INPUT))
        time.sleep(0.005)


def mouse_button(down: bool) -> None:
    item = INPUT(type=0)
    item.u.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP, 0, 0)
    user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))


def move_mouse(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))


def cursor() -> tuple[int, int]:
    point = wt.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


# ------------------------------------------------------------------ screen
class Screen:
    def __init__(self):
        self._sct = mss.mss()

    def grab(self, rect: tuple[int, int, int, int]):
        left, top, width, height = rect
        return self._sct.grab({"left": left, "top": top, "width": width, "height": height})


def center(rect: tuple[int, int, int, int], fraction: float) -> tuple[int, int, int, int]:
    left, top, width, height = rect
    w, h = max(1, int(width * fraction)), max(1, int(height * fraction))
    return left + (width - w) // 2, top + (height - h) // 2, w, h


def samples(shot, nx: int = 48, ny: int = 27) -> list[tuple[int, int, int]]:
    """A grid of RGB pixels — enough to tell "picture" from "empty" without numpy."""
    raw, width, height = shot.raw, shot.width, shot.height
    out = []
    for j in range(ny):
        y = int((j + 0.5) * height / ny)
        row = y * width * 4
        for i in range(nx):
            o = row + int((i + 0.5) * width / nx) * 4
            out.append((raw[o + 2], raw[o + 1], raw[o]))
    return out


def luma(pixels) -> list[float]:
    return [0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in pixels]


def dominant_fraction(pixels) -> float:
    """Share of the most common colour (16 levels per channel)."""
    counts: dict[tuple[int, int, int], int] = {}
    for r, g, b in pixels:
        key = (r >> 4, g >> 4, b >> 4)
        counts[key] = counts.get(key, 0) + 1
    return max(counts.values()) / len(pixels)


def mean_diff(a: list[float], b: list[float]) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / max(1, len(a))


def save_png(shot, path: Path, marks: list[tuple[int, int]] = ()) -> None:
    """Save a capture; `marks` (x, y inside the shot) get a red cross."""
    raw, width, height = shot.raw, shot.width, shot.height
    rgb = bytearray(width * height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = raw[2::4], raw[1::4], raw[0::4]
    for mx, my in marks:
        for d in range(-12, 13):
            for x, y in ((mx + d, my), (mx, my + d)):
                if 0 <= x < width and 0 <= y < height:
                    o = (y * width + x) * 3
                    rgb[o:o + 3] = b"\xff\x00\x00"
    path.parent.mkdir(parents=True, exist_ok=True)
    mss.tools.to_png(bytes(rgb), (width, height), output=str(path))


# ------------------------------------------------------------------ control
class Stop(Exception):
    """Ctrl+Alt+Q or "stop" in the pause dialog."""


def message(title: str, text: str, warning: bool = False) -> None:
    user32.MessageBeep(MB_ICONWARNING if warning else MB_ICONINFORMATION)
    user32.MessageBoxW(None, text, title, MB_OK | MB_TOPMOST | MB_SETFOREGROUND
                       | (MB_ICONWARNING if warning else MB_ICONINFORMATION))


class Control:
    """Polled between steps: Ctrl+Alt+Q stops, the top-left corner pauses. Keeps the PC
    awake (no sleep, no screen-off) while it lives."""

    HOTKEY_ID = 0xC41C

    def __init__(self, name: str):
        self.name = name
        self.paused_s = 0.0
        user32.RegisterHotKey(None, self.HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT,
                              ord("Q"))
        kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)

    def close(self) -> None:
        user32.UnregisterHotKey(None, self.HOTKEY_ID)
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)

    def check(self) -> bool:
        """Raises Stop; returns True if the run was paused (timings around it are void)."""
        msg = wt.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            if msg.message == WM_HOTKEY and msg.wParam == self.HOTKEY_ID:
                raise Stop("Ctrl+Alt+Q")
        x, y = cursor()
        if x <= 1 and y <= 1:
            started = time.perf_counter()
            answer = user32.MessageBoxW(
                None, f"{self.name} is paused.\n\nYes — continue (move the mouse out of the "
                "corner first)\nNo — stop and write the summary", f"{self.name} — paused",
                MB_YESNO | MB_TOPMOST | MB_SETFOREGROUND | MB_ICONWARNING)
            self.paused_s += time.perf_counter() - started
            if answer != IDYES:
                raise Stop("stopped from the pause dialog")
            return True
        return False

    def sleep(self, seconds: float, step: float = 0.05) -> None:
        end = time.perf_counter() + seconds
        while True:
            self.check()
            left = end - time.perf_counter()
            if left <= 0:
                return
            time.sleep(min(step, left))
