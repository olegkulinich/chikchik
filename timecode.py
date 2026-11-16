"""T102: SMPTE timecode (HH:MM:SS:FF) — no Qt, pure functions.

Frames count from 0. The "nominal" count is what a timecode label says
(((h * 60 + m) * 60 + s) * base + f); for non-drop-frame it equals the frame
number. Drop-frame (29.97 / 59.94) skips labels ;00 ;01 (;00–;03 at 59.94) at
the start of every minute except each 10th, so the label follows the clock.
23.976 has no drop-frame: it counts 24 frames per timecode second.
"""
from __future__ import annotations

import re


def base_drop(fps: float) -> tuple[int, int]:
    """(frames per timecode second, labels dropped per minute)."""
    if not fps or fps <= 0:
        return 24, 0
    base = max(1, round(fps))
    if base in (30, 60) and abs(fps - base * 1000 / 1001) < 0.005:
        return base, base // 15          # 29.97 → 2, 59.94 → 4
    return base, 0


def frame_to_nominal(frame: int, base: int, drop: int) -> int:
    if not drop or frame < 0:
        return frame
    per_min = base * 60 - drop
    per_10min = base * 600 - drop * 9
    tens, rest = divmod(frame, per_10min)
    nominal = frame + drop * 9 * tens
    if rest > drop:
        nominal += drop * ((rest - drop) // per_min)
    return nominal


def nominal_to_frame(nominal: int, base: int, drop: int) -> int:
    if not drop:
        return nominal
    minutes = nominal // (base * 60)
    return nominal - drop * (minutes - minutes // 10)


def split(nominal: int, base: int) -> tuple[int, int, int, int]:
    seconds, f = divmod(max(0, nominal), base)
    minutes, s = divmod(seconds, 60)
    h, m = divmod(minutes, 60)
    return h, m, s, f


def format_frame(frame: int, base: int, drop: int) -> str:
    """Frame number → "00:00:12:05" ("00:01:00;02" at drop-frame)."""
    h, m, s, f = split(frame_to_nominal(max(0, frame), base, drop), base)
    digits = 3 if base > 99 else 2
    return f"{h:02d}:{m:02d}:{s:02d}{';' if drop else ':'}{f:0{digits}d}"


def parse(text: str, base: int, drop: int, offset: bool = False) -> int | None:
    """Timecode text → frame number, or None if it isn't a timecode.

    "1:3::16" (empty / missing parts are zeros; the right-most part is frames) and
    plain digits read in pairs from the right, like AE / Resolve: "120" = 1 s 20 f,
    "10000" = 1 min. Seconds and minutes must be < 60 and frames < base, so a
    YouTube-style "1:30" at 25 fps is an error rather than a silent 2 s 05 f.
    offset=True (a "+10" / "-10" step): parts carry over ("+30" at 25 fps = 30 frames)
    and the result is a plain frame count, no drop-frame labels.
    """
    text = text.strip()
    if not text:
        return None
    if re.fullmatch(r"\d+", text):
        if len(text) > 8:
            return None
        text = text.rjust((len(text) + 1) // 2 * 2, "0")
        parts = [text[i:i + 2] for i in range(0, len(text), 2)]
    elif re.fullmatch(r"[\d:;]+", text):
        parts = re.split(r"[:;]", text)
        if len(parts) > 4:
            return None
    else:
        return None
    values = [int(p) if p else 0 for p in parts]
    values = [0] * (4 - len(values)) + values
    h, m, s, f = values
    if offset:
        return ((h * 60 + m) * 60 + s) * base + f
    if m >= 60 or s >= 60 or f >= base:
        return None
    if drop and s == 0 and m % 10 and f < drop:   # a skipped label → the next real one
        f = drop
    return nominal_to_frame(((h * 60 + m) * 60 + s) * base + f, base, drop)
