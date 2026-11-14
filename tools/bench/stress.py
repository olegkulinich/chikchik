"""T64: long review-session stress test (the MPC-BE hang scenario from client calls).

Run via stress.bat:  stress.bat ChikChik            (90 min, config.json → "stress")
                     stress.bat MPC-BE 60 --note obs
                     arguments: player [minutes] [--file path] [--seed N] [--note text]

One process for the whole session (like a real call). Cycle, repeated:
  1. rewind (jump back a few times), play 8–25 s;
  2. pause, 10–30 frame steps (mostly forward, some back);
  3. sometimes (40%) scrub the timeline with the mouse (players with scrub_from_bottom_px);
  4. idle on pause: usually 60 s, sometimes 2–5 min;
  5. play again — must start moving within a few seconds.

Watched all the time:
  HANG  — the window does not answer for 5 s ("Not Responding"); wait up to 30 s more,
          then kill and restart. Recovered hangs are logged with their length.
  CRASH — the process is gone; restart.
  STALL — playing, but the picture has not changed for 8 s (screenshot; the test goes on).
Every minute: memory, handles, threads, CPU → metrics.csv.

Results: results\\stress_<player>_<date>\\  stress.log, metrics.csv, events\\*.png,
summary.txt, DONE.txt. Before the first cycle: scrub_target.png shows where the mouse
will scrub (red cross) — check it once.

Safety: mouse in the top-left corner pauses, Ctrl+Alt+Q stops (summary still written).
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil

import winutil as w

HERE = Path(__file__).resolve().parent


class Session:
    def __init__(self, args, config: dict):
        stress = config["stress"]
        self.name = args.player
        self.keys = stress["players"][self.name]
        self.exe = Path(config["players"][self.name]["exe"])
        self.exe_args = config["players"][self.name].get("args", [])
        self.file = Path(args.file or stress["file"])
        self.seconds = 60 * float(args.minutes or stress.get("minutes", 90))
        self.seed = args.seed if args.seed is not None else int(time.time())
        self.rng = random.Random(self.seed)
        self.out = HERE / "results" / f"stress_{self.name.replace(' ', '')}_{datetime.now():%Y%m%d_%H%M}"
        (self.out / "events").mkdir(parents=True, exist_ok=True)
        self.log_file = open(self.out / "stress.log", "w", encoding="utf-8", buffering=1)
        self.metrics = open(self.out / "metrics.csv", "w", newline="", encoding="utf-8")
        self.metrics_writer = csv.writer(self.metrics)
        self.metrics_writer.writerow(["elapsed_min", "rss_mb", "private_mb", "handles",
                                      "threads", "cpu_percent", "restarts"])
        self.ctl = w.Control(f"Stress test ({self.name})")
        self.screen = w.Screen()
        self.proc: psutil.Process | None = None
        self.hwnd = 0
        self.t0 = time.perf_counter()
        self.next_metrics = 0.0
        self.counts = {"cycles": 0, "HANG": 0, "HANG recovered": 0, "CRASH": 0, "STALL": 0,
                       "restarts": 0, "focus lost": 0, "frame steps": 0, "scrubs": 0}
        self.note = args.note or ""
        self.quiet = args.quiet

    # ---------------------------------------------------------------- log
    def elapsed(self) -> float:
        return time.perf_counter() - self.t0

    def log(self, text: str) -> None:
        stamp = f"{datetime.now():%H:%M:%S}  {self.elapsed() / 60:6.1f} min"
        line = f"{stamp}  {text}"
        print(line, flush=True)
        self.log_file.write(line + "\n")

    def snapshot(self, tag: str) -> None:
        try:
            if self.hwnd:
                path = self.out / "events" / f"{self.elapsed() / 60:06.1f}min_{tag}.png"
                w.save_png(self.screen.grab(w.client_rect(self.hwnd)), path)
                self.log(f"    screenshot {path.name}")
        except Exception as exc:
            self.log(f"    screenshot failed: {exc}")

    # ---------------------------------------------------------------- process
    def launch(self) -> None:
        self.log(f"launch {self.exe.name} {self.file.name}")
        popen = subprocess.Popen([str(self.exe), *self.exe_args, str(self.file)],
                                 cwd=str(self.exe.parent))
        self.proc = psutil.Process(popen.pid)
        self.proc.cpu_percent(None)
        deadline = time.perf_counter() + 20
        while time.perf_counter() < deadline:
            self.ctl.check()
            windows = w.windows_of(w.process_tree(self.proc.pid))
            if windows:
                self.hwnd = windows[0].hwnd
                self.ctl.sleep(2.0)             # let the file load
                self.ensure_focus()
                return
            time.sleep(0.05)
        raise RuntimeError("no window within 20 s")

    def restart(self, reason: str) -> None:
        self.counts["restarts"] += 1
        self.log(f"RESTART after {reason}")
        if self.proc is not None and self.proc.is_running():
            w.close_process(self.proc, grace=2.0)
        self.ctl.sleep(2.0)
        self.launch()

    def alive(self) -> bool:
        return self.proc is not None and self.proc.is_running() \
            and self.proc.status() != psutil.STATUS_ZOMBIE

    # ---------------------------------------------------------------- watch
    def watch(self) -> bool:
        """Crash and hang check. False → the process was restarted (abandon the cycle)."""
        self.ctl.check()
        if self.elapsed() >= self.next_metrics:
            self.write_metrics()
        if not self.alive():
            self.counts["CRASH"] += 1
            self.log("CRASH: the process is gone")
            self.restart("crash")
            return False
        if w.responds(self.hwnd, 5000):
            return True
        started = time.perf_counter()
        self.counts["HANG"] += 1
        self.log("HANG: no answer for 5 s")
        self.snapshot("hang")
        while time.perf_counter() - started < 30:
            self.ctl.check()
            if not self.alive():
                break
            if w.responds(self.hwnd, 1000):
                spent = time.perf_counter() - started + 5
                self.counts["HANG recovered"] += 1
                self.log(f"    recovered after ~{spent:.0f} s")
                return True
        self.log("    still hung after 35 s")
        self.snapshot("hang_final")
        self.restart("hang")
        return False

    def write_metrics(self) -> None:
        self.next_metrics = self.elapsed() + 60
        if not self.alive():
            return
        try:
            procs = [self.proc] + self.proc.children(recursive=True)
            rss = sum(p.memory_info().rss for p in procs) / 2**20
            private = sum(getattr(p.memory_info(), "private", 0) for p in procs) / 2**20
            handles = sum(p.num_handles() for p in procs)
            threads = sum(p.num_threads() for p in procs)
            cpu = self.proc.cpu_percent(None)
        except psutil.Error:
            return
        self.metrics_writer.writerow([f"{self.elapsed() / 60:.1f}", f"{rss:.0f}",
                                      f"{private:.0f}", handles, threads, f"{cpu:.0f}",
                                      self.counts["restarts"]])
        self.metrics.flush()

    def ensure_focus(self) -> None:
        if not w.focus(self.hwnd):
            self.counts["focus lost"] += 1
            self.log("    could not bring the window to front (another window on top?)")

    def key(self, name: str) -> None:
        self.ensure_focus()
        w.press(self.keys[name])

    def thumb(self) -> list[float]:
        return w.luma(w.samples(self.screen.grab(w.center(w.client_rect(self.hwnd), 0.5))))

    def moving(self, seconds: float) -> bool:
        """The picture changes within `seconds` (checked every 0.25 s)."""
        start = self.thumb()
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            self.ctl.sleep(0.25)
            if w.mean_diff(self.thumb(), start) > 1.5:
                return True
        return False

    # ---------------------------------------------------------------- steps
    def rewind(self) -> None:
        for _ in range(int(self.keys.get("jump_back_times", 8))):
            self.key("jump_back")
            self.ctl.sleep(0.12)

    def play_for(self, seconds: float) -> bool:
        """Start playback and watch it. Returns False if the cycle must be abandoned."""
        self.key("play")
        if not self.moving(3.0):
            self.key("play")                     # it was already playing → now paused
            if not self.moving(3.0):
                self.counts["STALL"] += 1
                self.log("STALL: play pressed twice, the picture does not move")
                self.snapshot("stall_start")
                return self.watch()
        end = time.perf_counter() + seconds
        last_change, previous = time.perf_counter(), self.thumb()
        while time.perf_counter() < end:
            if not self.watch():
                return False
            self.ctl.sleep(0.5)
            current = self.thumb()
            if w.mean_diff(current, previous) > 1.5:
                last_change, previous = time.perf_counter(), current
            elif time.perf_counter() - last_change > 8:
                self.counts["STALL"] += 1
                self.log("STALL: playing, but no picture change for 8 s")
                self.snapshot("stall")
                last_change = time.perf_counter()
        return True

    def pause(self) -> None:
        self.key("play")
        self.ctl.sleep(0.6)
        if self.moving(1.0):
            self.log("    pause did not take — pressing again")
            self.key("play")

    def frame_steps(self) -> bool:
        steps = self.rng.randint(10, 30)
        for i in range(steps):
            self.key("frame_next" if self.rng.random() < 0.7 else "frame_prev")
            self.counts["frame steps"] += 1
            self.ctl.sleep(self.rng.uniform(0.15, 0.6))
            if i % 5 == 4 and not self.watch():
                return False
        return True

    def scrub_point(self, fraction: float) -> tuple[int, int]:
        left, top, width, height = w.client_rect(self.hwnd)
        y = top + height - int(self.keys["scrub_from_bottom_px"] * w.dpi_scale(self.hwnd))
        return left + int(width * fraction), y

    def scrub(self) -> bool:
        if not self.keys.get("scrub_from_bottom_px"):
            return True
        lo, hi = self.keys.get("scrub_x", [0.3, 0.65])
        self.ensure_focus()
        x, y = self.scrub_point(self.rng.uniform(lo, hi))
        w.move_mouse(x, y)
        time.sleep(0.05)
        w.mouse_button(True)
        try:
            end = time.perf_counter() + self.rng.uniform(1.0, 3.0)
            while time.perf_counter() < end:
                self.ctl.check()
                x, _ = self.scrub_point(self.rng.uniform(lo, hi))
                w.move_mouse(x, y)
                time.sleep(self.rng.uniform(0.016, 0.05))
        finally:
            w.mouse_button(False)
        self.counts["scrubs"] += 1
        # park the cursor over the video (not over a button, not in the pause corner)
        left, top, width, height = w.client_rect(self.hwnd)
        w.move_mouse(left + width // 2, top + height // 3)
        return self.watch()

    def idle(self) -> bool:
        seconds = 60 if self.rng.random() < 0.8 else self.rng.uniform(120, 300)
        self.log(f"  idle on pause {seconds:.0f} s")
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            if not self.watch():
                return False
            self.ctl.sleep(2.0)
        return True

    def resume_after_idle(self) -> bool:
        """The moment MPC-BE froze on calls: play after a long pause."""
        self.key("play")
        started = time.perf_counter()
        if self.moving(4.0):
            self.log(f"  resumed OK ({(time.perf_counter() - started) * 1000:.0f} ms to move)")
            return True
        if not self.watch():
            return False
        # maybe the playhead sits at the very end of the file: rewind and try once more
        self.rewind()
        self.key("play")
        if self.moving(4.0):
            self.log("  resumed after a rewind (was at the end)")
            return True
        self.key("play")                          # in case the first press paused it
        if self.moving(4.0):
            self.log("  resumed on a second press")
            return True
        self.counts["STALL"] += 1
        self.log("STALL: does not resume after the pause")
        self.snapshot("stall_resume")
        if not self.watch():
            return False
        self.restart("no resume")
        return False

    # ---------------------------------------------------------------- run
    def calibrate(self) -> None:
        if not self.keys.get("scrub_from_bottom_px"):
            self.log("scrub: off for this player (keyboard only)")
            return
        rect = w.client_rect(self.hwnd)
        marks = [(self.scrub_point(f)[0] - rect[0], self.scrub_point(f)[1] - rect[1])
                 for f in self.keys.get("scrub_x", [0.3, 0.65])]
        w.save_png(self.screen.grab(rect), self.out / "scrub_target.png", marks)
        self.log(f"scrub: y = {marks[0][1]} px from the client top "
                 f"(see scrub_target.png — the red crosses must sit on the timeline)")

    def run(self) -> None:
        self.log(f"Stress test: {self.name}, {self.file.name}, {self.seconds / 60:.0f} min, "
                 f"seed {self.seed}{', note: ' + self.note if self.note else ''}")
        self.log("Mouse to the top-left corner = pause, Ctrl+Alt+Q = stop.")
        if w.running_instances(self.exe):
            raise RuntimeError(f"close {self.name} first")
        self.launch()
        self.calibrate()
        while self.elapsed() < self.seconds:
            self.counts["cycles"] += 1
            self.log(f"cycle {self.counts['cycles']}")
            self.rewind()
            if not self.play_for(self.rng.uniform(8, 25)):
                continue
            self.pause()
            if not self.frame_steps():
                continue
            if self.rng.random() < 0.4 and not self.scrub():
                continue
            if self.rng.random() < 0.5 and not self.frame_steps():
                continue
            if not self.idle():
                continue
            if not self.resume_after_idle():
                continue
            self.ctl.sleep(self.rng.uniform(3, 8))
            self.pause()

    def finish(self, stopped: str) -> None:
        self.write_metrics()
        if self.alive():
            w.close_process(self.proc)
        self.ctl.close()
        c = self.counts
        verdict = ("CLEAN — no hangs, crashes or stalls" if not (c["HANG"] or c["CRASH"]
                                                                 or c["STALL"])
                   else "PROBLEMS — see stress.log and events\\")
        lines = [f"Stress test {self.name} — {verdict}",
                 f"{datetime.now():%Y-%m-%d %H:%M}, {self.elapsed() / 60:.0f} min, "
                 f"file {self.file.name}, seed {self.seed}"
                 f"{', note: ' + self.note if self.note else ''}",
                 *(f"STOPPED EARLY: {stopped}",) * bool(stopped),
                 "", *(f"  {k:15} {v}" for k, v in c.items()),
                 "", "memory/handles over time: metrics.csv"]
        try:
            rows = list(csv.reader(open(self.out / "metrics.csv", encoding="utf-8")))[1:]
            if len(rows) >= 2:
                lines.append(f"  RSS {rows[0][1]} → {rows[-1][1]} MB, handles {rows[0][3]} → "
                             f"{rows[-1][3]} (first → last minute)")
        except Exception:
            pass
        summary = "\n".join(lines)
        (self.out / "summary.txt").write_text(summary + "\n", encoding="utf-8")
        (self.out / "DONE.txt").write_text(f"finished {datetime.now():%Y-%m-%d %H:%M}\n",
                                           encoding="utf-8")
        self.log("\n" + summary)
        self.log_file.close()
        self.metrics.close()
        if self.quiet:                       # stress_all.bat: no dialog between tests
            w.user32.MessageBeep(w.MB_ICONINFORMATION)
        else:
            w.message(f"Stress test ({self.name}) — done",
                      f"{summary}\n\nResults:\n{self.out}", warning=verdict.startswith("PROB"))


def main() -> int:
    config = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser()
    parser.add_argument("player", choices=list(config["stress"]["players"]))
    parser.add_argument("minutes", nargs="?", type=float)
    parser.add_argument("--file")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--note", help="e.g. 'obs capture' — goes into the log")
    parser.add_argument("--quiet", action="store_true", help="no dialog at the end")
    session = Session(parser.parse_args(), config)
    stopped = ""
    try:
        session.run()
    except w.Stop as reason:
        stopped = str(reason)
    except Exception as exc:                      # still write what we have
        stopped = f"error: {exc!r}"
        session.log(stopped)
    finally:
        session.finish(stopped)
    return 2 if stopped.startswith(("Ctrl+Alt+Q", "stopped from")) else 0   # 2: user stop


if __name__ == "__main__":
    sys.exit(main())
