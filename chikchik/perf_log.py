"""Hidden timing log for timeline scrubbing (decides whether mpv needs its own render thread).

Enable: set QTC_SCRUB_LOG=1 before starting ChikChik!. The log goes to
%TEMP%\\ChikChik-scrub.log (or the path given instead of 1).
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

_value = os.environ.get("QTC_SCRUB_LOG", "").strip()
ENABLED = bool(_value) and _value != "0"
PATH = (Path(_value) if ENABLED and _value not in ("1", "yes", "true")
        else Path(tempfile.gettempdir()) / "ChikChik-scrub.log")
_file = None


def log(text: str) -> None:
    global _file
    if not ENABLED:
        return
    try:
        if _file is None:
            _file = open(PATH, "a", encoding="utf-8", buffering=1)
            _file.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        _file.write(f"{time.perf_counter() * 1000:12.1f}  {text}\n")
    except OSError:
        pass


class _Log:
    """Same format as the scrub log, for other hidden diagnostics."""

    def __init__(self, env: str, filename: str):
        value = os.environ.get(env, "").strip()
        self.enabled = bool(value) and value != "0"
        self.path = (Path(value) if self.enabled and value not in ("1", "yes", "true")
                     else Path(tempfile.gettempdir()) / filename)
        self._file = None

    def log(self, text: str) -> None:
        if not self.enabled:
            return
        try:
            if self._file is None:
                self._file = open(self.path, "a", encoding="utf-8", buffering=1)
                self._file.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            self._file.write(f"{time.perf_counter() * 1000:12.1f}  {text}\n")
        except OSError:
            pass


# E2 (55g): QTC_RESUME_LOG=1 → %TEMP%\ChikChik-resume.log — Space → mpv unpaused →
# first new frame from mpv → painted → on screen, one line per resume.
resume = _Log("QTC_RESUME_LOG", "ChikChik-resume.log")
