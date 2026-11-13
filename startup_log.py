"""Hidden startup timing log (T55a): where the time goes between the click and the first
video frame.

Enable: set QTC_STARTUP_LOG=1 before starting ChikChik!. The log goes to
%TEMP%\\ChikChik-startup.log (or the path given instead of 1). Each start appends one
block headed by the process id; times are milliseconds since the process was created
(on Windows — including the PyInstaller bootloader), so they line up with an outside
stopwatch such as tools/bench/bench_startup.py.

No Qt imports here: app.py imports this module first, before PySide6.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

_value = os.environ.get("QTC_STARTUP_LOG", "").strip()
ENABLED = bool(_value) and _value != "0"
PATH = (Path(_value) if ENABLED and _value not in ("1", "yes", "true")
        else Path(tempfile.gettempdir()) / "ChikChik-startup.log")

_lock = threading.Lock()
_lines: list[str] = []
_seen: set[str] = set()
_finished = False
_last = 0.0


def _process_age_ms() -> float | None:
    """Milliseconds since this process was created (Windows), else None."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt

        kernel32 = ctypes.windll.kernel32
        created, exited, kernel, user = (wt.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(kernel32.GetCurrentProcess(), ctypes.byref(created),
                                        ctypes.byref(exited), ctypes.byref(kernel),
                                        ctypes.byref(user)):
            return None
        now = wt.FILETIME()
        kernel32.GetSystemTimePreciseAsFileTime(ctypes.byref(now))

        def ticks(ft) -> int:
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

        return (ticks(now) - ticks(created)) / 10_000       # 100 ns → ms
    except Exception:
        return None


# perf_counter value that corresponds to the process creation time.
_age = _process_age_ms() if ENABLED else None
_origin = time.perf_counter() - (_age or 0.0) / 1000


def mark(text: str, once: bool = False) -> None:
    """Record a stage. `once`: only the first call with this text counts (paint events)."""
    global _last
    if not ENABLED or _finished:
        return
    now = (time.perf_counter() - _origin) * 1000
    with _lock:
        if once:
            if text in _seen:
                return
            _seen.add(text)
        _lines.append(f"{now:9.1f}  +{now - _last:7.1f}  {text}")
        _last = now


def finish(text: str = "done") -> None:
    """Write the block and stop recording (called once the first video frame is up)."""
    global _finished
    if not ENABLED or _finished:
        return
    mark(text)
    with _lock:
        _finished = True
        header = (f"=== {time.strftime('%Y-%m-%d %H:%M:%S')}  pid {os.getpid()}  "
                  f"origin {'process creation' if _age is not None else 'python start'}  "
                  f"args {' '.join(sys.argv[1:])} ===")
        try:
            with open(PATH, "a", encoding="utf-8") as file:
                file.write("\n".join([header, *_lines]) + "\n\n")
        except OSError:
            pass


mark("python: startup_log imported")
