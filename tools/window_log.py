"""Log every window ChikChik creates/shows/hides/destroys during startup.

Usage (Windows):
    python tools\\window_log.py                      -> installed ChikChik.exe
    python tools\\window_log.py path\\to\\ChikChik.exe
    python tools\\window_log.py app.py               -> run from source
    add a number of seconds at the end to change the duration (default 6)

Result: window_log.txt next to this script.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

EVENT_OBJECT_CREATE = 0x8000
EVENT_OBJECT_DESTROY = 0x8001
EVENT_OBJECT_SHOW = 0x8002
EVENT_OBJECT_HIDE = 0x8003
EVENT_NAMES = {EVENT_OBJECT_CREATE: "CREATE", EVENT_OBJECT_DESTROY: "DESTROY",
               EVENT_OBJECT_SHOW: "SHOW", EVENT_OBJECT_HIDE: "HIDE"}
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
OBJID_WINDOW = 0
CHILDID_SELF = 0
GA_ROOT = 2
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CAPTION = 0x00C00000
WS_POPUP = 0x80000000
WS_CHILD = 0x40000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PM_REMOVE = 0x0001

WinEventProc = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG,
                                  wt.LONG, wt.DWORD, wt.DWORD)
user32.SetWinEventHook.restype = wt.HANDLE
user32.SetWinEventHook.argtypes = [wt.DWORD, wt.DWORD, wt.HMODULE, WinEventProc,
                                   wt.DWORD, wt.DWORD, wt.DWORD]
user32.UnhookWinEvent.argtypes = [wt.HANDLE]
user32.GetAncestor.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                                ctypes.POINTER(wt.DWORD)]

WATCH = {"quicktrimcrop.exe", "python.exe", "pythonw.exe", "ffprobe.exe", "ffmpeg.exe"}

_proc_names: dict[int, str] = {}
_known: dict[int, str] = {}         # hwnd -> description (DESTROY has no info left)
_lines: list[str] = []
_t0 = time.perf_counter()


def process_name(pid: int) -> str:
    if pid in _proc_names:
        return _proc_names[pid]
    name = "?"
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(buffer))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            name = Path(buffer.value).name
        kernel32.CloseHandle(handle)
    _proc_names[pid] = name
    return name


def describe(hwnd: int) -> str:
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 256)
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    style = user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    flags = [name for bit, name in ((WS_CAPTION, "caption"), (WS_POPUP, "popup"),
                                    (WS_CHILD, "child")) if style & bit == bit]
    flags += [name for bit, name in ((WS_EX_TOOLWINDOW, "tool"), (WS_EX_LAYERED, "layered"),
                                     (WS_EX_NOACTIVATE, "noactivate")) if ex & bit]
    return (f'class "{cls.value}"  title "{title.value}"  '
            f"rect {rect.left},{rect.top} {rect.right - rect.left}x{rect.bottom - rect.top}  "
            f"[{' '.join(flags)}]")


def on_event(_hook, event, hwnd, id_object, id_child, _thread, _time):
    if id_object != OBJID_WINDOW or id_child != CHILDID_SELF or not hwnd:
        return
    try:
        if event == EVENT_OBJECT_DESTROY:
            if hwnd not in _known:
                return
            info = _known.pop(hwnd)
        else:
            if user32.GetAncestor(hwnd, GA_ROOT) != hwnd:      # top-level windows only
                return
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            proc = process_name(pid.value)
            if proc.lower() not in WATCH:
                return
            info = f"{proc} pid {pid.value}  {describe(hwnd)}"
            _known[hwnd] = info
        ms = (time.perf_counter() - _t0) * 1000
        line = f"{ms:8.0f} ms  {EVENT_NAMES[event]:<7}  hwnd {hwnd:#010x}  {info}"
        _lines.append(line)
        print(line, flush=True)
    except Exception as exc:                                     # never kill the hook
        _lines.append(f"error: {exc}")


def main() -> int:
    global _t0
    args = sys.argv[1:]
    seconds = 6.0
    if args and args[-1].replace(".", "", 1).isdigit():
        seconds = float(args.pop())
    if args:
        target = Path(args[0]).resolve()
    else:
        target = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "ChikChik" / "ChikChik.exe"
    if not target.exists():
        print(f"Not found: {target}")
        return 1
    command = [sys.executable, str(target)] if target.suffix.lower() == ".py" else [str(target)]

    callback = WinEventProc(on_event)                            # keep a reference
    hook = user32.SetWinEventHook(EVENT_OBJECT_CREATE, EVENT_OBJECT_HIDE, None, callback,
                                  0, 0, WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
    if not hook:
        print("SetWinEventHook failed")
        return 1

    print(f"Launching {target} for {seconds:g} s ...\n")
    _t0 = time.perf_counter()
    process = subprocess.Popen(command, cwd=str(target.parent))

    msg = wt.MSG()
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.002)
    user32.UnhookWinEvent(hook)

    out = Path(__file__).with_name("window_log.txt")
    header = [f"target: {target}", f"pid: {process.pid}", f"duration: {seconds:g} s", ""]
    out.write_text("\n".join(header + _lines) + "\n", encoding="utf-8")
    print(f"\nSaved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
