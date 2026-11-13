"""T55: how long each player takes from launch to window and to the first video frame.

Run via bench.bat (it makes the venv). Settings: config.json next to this script.

For every player × file: one warm-up run (dropped: cold disk cache), then `runs`
measured runs, interleaved (run 1 of every pair, then run 2 …) so slow drift of the PC
spreads evenly. Clock starts at process launch (not the Explorer double-click).

  window — the player's main window is on screen (ChikChik: after it uncloaks);
  frame  — the middle of the window shows a picture (see has_picture).

ChikChik also writes its own stage log (QTC_STARTUP_LOG) — matched by process id.

Results: results\\startup_<date>\\  summary.txt, runs.csv, chikchik_stages.csv,
first-frame screenshots of the first measured run in frames\\, DONE.txt.

Safety: mouse in the top-left corner pauses, Ctrl+Alt+Q stops (summary still written).
"""
from __future__ import annotations

import csv
import json
import os
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil

import winutil as w

HERE = Path(__file__).resolve().parent
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".dpx", ".bmp", ".tga"}


# ------------------------------------------------------------------ picture test
def has_picture(pixels, first) -> tuple[bool, str]:
    """Structure (not one flat colour) — or clearly different from a flat first look.

    `first` = samples of the window as it first appeared (usually black / a logo)."""
    lum = w.luma(pixels)
    dom = w.dominant_fraction(pixels)
    spread = statistics.pstdev(lum)
    if dom < 0.6 and spread > 6:
        return True, "structure"
    if first is not None and w.dominant_fraction(first[0]) >= 0.75 \
            and w.mean_diff(lum, first[1]) > 12:
        return True, "changed"
    return False, ""


def target_path(path: Path) -> Path:
    """A sequence folder → its first image (players pick up the rest)."""
    if path.is_dir():
        images = sorted(p for p in path.iterdir() if p.suffix.lower() in IMAGE_EXT)
        if not images:
            raise FileNotFoundError(f"No images in {path}")
        return images[0]
    return path


def is_sequence(path: Path) -> bool:
    return path.is_dir() or path.suffix.lower() in IMAGE_EXT


# ------------------------------------------------------------------ one launch
def measure(ctl: w.Control, screen: w.Screen, exe: Path, args: list[str], file: Path,
            timeout: float, env: dict, shot_path: Path | None) -> dict:
    result = {"window_ms": None, "frame_ms": None, "rule": "", "window_class": "",
              "grab_ms": None, "pid": None, "closed": "", "note": ""}
    grabs = []
    t0 = time.perf_counter()
    proc = subprocess.Popen([str(exe), *args, str(file)], cwd=str(exe.parent), env=env)
    result["pid"] = proc.pid
    first = None
    try:
        while True:
            now = time.perf_counter()
            if now - t0 > timeout:
                result["note"] = "timeout"
                break
            if ctl.check():
                result["note"] = "paused — run void"
                break
            if proc.poll() is not None and not w.process_tree(proc.pid):
                result["note"] = f"exited with code {proc.returncode}"
                break
            windows = w.windows_of(w.process_tree(proc.pid))
            if not windows:
                time.sleep(0.002)
                continue
            window = windows[0]
            if result["window_ms"] is None:
                result["window_ms"] = (now - t0) * 1000
                result["window_class"] = window.cls
            region = w.center(w.client_rect(window.hwnd), 0.6)
            taken = time.perf_counter()
            shot = screen.grab(region)
            grabs.append((time.perf_counter() - taken) * 1000)
            pixels = w.samples(shot)
            if first is None:
                first = (pixels, w.luma(pixels))
            ok, rule = has_picture(pixels, first)
            if ok:
                result["frame_ms"] = (taken - t0) * 1000
                result["rule"] = rule
                if shot_path is not None:
                    time.sleep(0.3)              # the settled picture, for checking by eye
                    w.save_png(screen.grab(w.client_rect(window.hwnd)), shot_path)
                break
    finally:
        time.sleep(0.3)
        result["closed"] = w.close_process(psutil.Process(proc.pid)) \
            if psutil.pid_exists(proc.pid) else "gone"
    if grabs:
        result["grab_ms"] = statistics.median(grabs)
    return result


# ------------------------------------------------------------------ ChikChik stages
def read_stages(log: Path) -> dict[int, list[tuple[float, str]]]:
    blocks: dict[int, list[tuple[float, str]]] = {}
    if not log.exists():
        return blocks
    pid = None
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        head = re.match(r"=== .*\bpid (\d+)\b", line)
        if head:
            pid = int(head.group(1))
            blocks[pid] = []
            continue
        row = re.match(r"\s*([\d.]+)\s+\+\s*[\d.]+\s+(.*)", line)
        if row and pid is not None:
            blocks[pid].append((float(row.group(1)), row.group(2).strip()))
    return blocks


def stage_key(text: str) -> str:
    """Stable names for the table (drop numbers like '75 frames' and error text)."""
    if text.startswith("open failed"):
        return "open failed"
    return re.sub(r"\d+ frames", "N frames", text)


# ------------------------------------------------------------------ report
def fmt(values: list[float]) -> str:
    if not values:
        return "—"
    med = statistics.median(values)
    return f"{med:6.0f}  ({min(values):.0f}–{max(values):.0f})"


def main() -> int:
    config = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    runs, timeout = int(config.get("runs", 5)), float(config.get("timeout_s", 12))
    only = [a.lower() for a in sys.argv[1:]]          # optional: player names to run

    players = {}
    for name, item in config["players"].items():
        if only and name.lower() not in only:
            continue
        exe = Path(item["exe"])
        if not exe.exists():
            print(f"skip {name}: not found {exe}")
            continue
        players[name] = (exe, item.get("args", []), item.get("sequence", False))
    files = []
    for raw in config["files"]:
        path = Path(raw)
        if not path.exists():
            print(f"skip file: not found {path}")
            continue
        files.append((path, target_path(path)))

    busy = {name: w.running_instances(exe) for name, (exe, _, _) in players.items()}
    busy = {k: v for k, v in busy.items() if v}
    if busy:
        text = "Close these players first:\n" + "\n".join(busy)
        print(text)
        w.message("Startup bench", text, warning=True)
        return 1

    pairs = [(name, src, target) for name, (_, _, seq) in players.items()
             for src, target in files if seq or not is_sequence(src)]
    out = HERE / "results" / f"startup_{datetime.now():%Y%m%d_%H%M}"
    (out / "frames").mkdir(parents=True, exist_ok=True)
    stage_log = out / "chikchik_startup.log"
    total = len(pairs) * (runs + 1)
    print(f"{len(pairs)} player×file pairs × {runs + 1} runs = {total} launches "
          f"(~{total * 4 / 60:.0f} min). Results: {out}")
    print("Mouse to the top-left corner = pause, Ctrl+Alt+Q = stop.\n")

    ctl = w.Control("Startup bench")
    screen = w.Screen()
    rows: list[dict] = []
    stopped = ""
    started = time.perf_counter()
    try:
        n = 0
        for run in range(runs + 1):
            for name, src, target in pairs:
                n += 1
                exe, args, _ = players[name]
                env = dict(os.environ)
                if name == "ChikChik":
                    env["QTC_STARTUP_LOG"] = str(stage_log)
                shot = (out / "frames" / f"{name}_{src.stem}.png") if run == 1 else None
                r = measure(ctl, screen, exe, args, target, timeout, env, shot)
                r.update(player=name, file=src.name, run=run, warmup=run == 0)
                rows.append(r)
                win = f"{r['window_ms']:.0f}" if r["window_ms"] is not None else "—"
                frm = f"{r['frame_ms']:.0f}" if r["frame_ms"] is not None else "—"
                print(f"[{n:3}/{total}] run {run}{' (warm-up)' if run == 0 else ''}  "
                      f"{name:13} {src.name:28} window {win:>5}  frame {frm:>5} ms  "
                      f"{r['rule']:9} {r['note']}")
                ctl.sleep(1.0)            # let disk / GPU settle between launches
    except w.Stop as reason:
        stopped = str(reason)
        print(f"\nStopped: {stopped}")
    finally:
        ctl.close()

    # ---- CSV
    fields = ["player", "file", "run", "warmup", "window_ms", "frame_ms", "rule", "note",
              "window_class", "grab_ms", "closed", "pid"]
    with open(out / "runs.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    measured = [r for r in rows if not r["warmup"] and not r["note"].startswith("paused")]
    lines = [f"Startup bench {datetime.now():%Y-%m-%d %H:%M}   runs per pair: {runs} "
             f"(+1 warm-up dropped)   clock: from process launch",
             f"median (min–max), ms. frame = picture in the middle of the window."]
    if stopped:
        lines.append(f"STOPPED EARLY: {stopped}")
    for src, _ in files:
        lines += ["", f"{src.name}", f"  {'player':14} {'window':>20}  {'first frame':>20}  fails"]
        for name in players:
            sel = [r for r in measured if r["player"] == name and r["file"] == src.name]
            if not sel:
                continue
            windows = [r["window_ms"] for r in sel if r["window_ms"] is not None]
            frames = [r["frame_ms"] for r in sel if r["frame_ms"] is not None]
            fails = len(sel) - len(frames)
            lines.append(f"  {name:14} {fmt(windows):>20}  {fmt(frames):>20}  "
                         f"{fails if fails else ''}")

    # ---- ChikChik internal stages, matched by pid
    blocks = read_stages(stage_log)
    stage_rows = []
    for r in rows:
        if r["player"] == "ChikChik" and r["pid"] in blocks:
            for ms, text in blocks[r["pid"]]:
                stage_rows.append({"file": r["file"], "run": r["run"], "warmup": r["warmup"],
                                   "stage": stage_key(text), "ms": ms})
    if stage_rows:
        with open(out / "chikchik_stages.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["file", "run", "warmup", "stage", "ms"])
            writer.writeheader()
            writer.writerows(stage_rows)
        lines += ["", "ChikChik stages — median ms since process creation (measured runs)"]
        for src, _ in files:
            sel = [s for s in stage_rows if s["file"] == src.name and not s["warmup"]]
            if not sel:
                continue
            lines.append(f"  {src.name}")
            order: list[str] = []
            for s in sel:
                if s["stage"] not in order:
                    order.append(s["stage"])
            for stage in order:
                values = [s["ms"] for s in sel if s["stage"] == stage]
                lines.append(f"    {statistics.median(values):7.0f}  {stage}")

    elapsed = (time.perf_counter() - started) / 60
    lines += ["", f"took {elapsed:.0f} min; first-frame screenshots (run 1): frames\\",
              "rule: structure = picture with detail; changed = differs from a flat empty "
              "window (check the screenshot if a number looks odd)"]
    summary = "\n".join(lines)
    (out / "summary.txt").write_text(summary + "\n", encoding="utf-8")
    (out / "DONE.txt").write_text(f"finished {datetime.now():%Y-%m-%d %H:%M}"
                                  f"{' — ' + stopped if stopped else ''}\n", encoding="utf-8")
    print("\n" + summary)
    w.message("Startup bench — done",
              f"{'Stopped: ' + stopped if stopped else 'Finished'} after {elapsed:.0f} min.\n\n"
              f"Results:\n{out}\n\nSend summary.txt, runs.csv and chikchik_stages.csv.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
