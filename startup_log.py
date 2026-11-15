"""Hidden startup timing log (T55a): where the time goes between the click and the first
video frame.

Enable: set QTC_STARTUP_LOG=1 before starting ChikChik!. The log goes to
%TEMP%\\ChikChik-startup.log (or the path given instead of 1). Each start appends one
block headed by the process id; times are milliseconds since the process was created
(on Windows — including the PyInstaller bootloader), so they line up with an outside
stopwatch such as tools/bench/bench_startup.py.

E4 (T95): QTC_GL_TRACE=1 (together with QTC_STARTUP_LOG) also writes Qt's OpenGL/window
debug messages (qt.qpa.gl, qt.qpa.window) and mpv's verbose log lines, each with its time —
to see what happens inside winId / initializeGL / the mpv chain. The extra logging costs a
little time itself: compare it as a separate bench variant. `[cpu]` lines carry CPU time
(ms) of the process instead of a timestamp.

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

TRACE = ENABLED and os.environ.get("QTC_GL_TRACE", "") not in ("", "0")
TRACE_LIMIT = 400          # lines from Qt/mpv per start, then they are dropped

_lock = threading.Lock()
_traced = 0
_counts: dict[str, int] = {}
_on_finish: list = []
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


def _process_cpu_ms() -> tuple[float, float] | None:
    """(kernel, user) CPU milliseconds of this process so far (Windows), else None."""
    if os.name == "nt":
        try:
            import ctypes
            import ctypes.wintypes as wt

            kernel32 = ctypes.windll.kernel32
            created, exited, kernel, user = (wt.FILETIME() for _ in range(4))
            if kernel32.GetProcessTimes(kernel32.GetCurrentProcess(), ctypes.byref(created),
                                        ctypes.byref(exited), ctypes.byref(kernel),
                                        ctypes.byref(user)):
                def ms(ft) -> float:
                    return ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / 10_000
                return ms(kernel), ms(user)
        except Exception:
            return None
        return None
    times = os.times()
    return times.system * 1000, times.user * 1000


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


def value(text: str, number: float) -> None:
    """A measured number (not a time) in the time column, e.g. `[cpu] …` lines."""
    if not ENABLED or _finished:
        return
    with _lock:
        _lines.append(f"{number:9.1f}  +{0:7.1f}  {text}")


def cpu(where: str) -> None:
    """E4 (T95): CPU time of the process so far — tells busy (CPU) from waiting (disk,
    driver, antivirus) when compared with the wall time of the same mark."""
    if not ENABLED:
        return
    times = _process_cpu_ms()
    if times is not None:
        value(f"[cpu] total at {where}", times[0] + times[1])
        value(f"[cpu] kernel at {where}", times[0])


def trace(source: str, text: str) -> None:
    """One Qt/mpv debug line (QTC_GL_TRACE). Pointers dropped, repeats numbered, so the
    bench stage table lines up the same messages across runs."""
    global _traced
    if not TRACE or _finished:
        return
    import re

    text = re.sub(r"0x[0-9a-fA-F]+", "0x", " ".join(text.split()))[:110]
    key = f"{source}: {text}"
    with _lock:
        if _traced >= TRACE_LIMIT:
            return
        _traced += 1
        count = _counts[key] = _counts.get(key, 0) + 1
    mark(key if count == 1 else f"{key} #{count}")


def mpv_message(level: str, prefix: str, text: str) -> None:     # mpv log_handler
    trace(f"mpv[{prefix}]", text)


def on_finish(callback) -> None:
    """Called once the log is written (app.py turns Qt's debug categories off there)."""
    _on_finish.append(callback)


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
    for callback in _on_finish:
        try:
            callback()
        except Exception:
            pass


mark("python: startup_log imported")
cpu("python start")      # E4 (T95): pre-Python time — busy or waiting?
