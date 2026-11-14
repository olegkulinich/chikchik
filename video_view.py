"""mpv rendered into a QOpenGLWidget, so the crop frame and clicks live in Qt."""
from __future__ import annotations

import math
import os
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QOpenGLContext, QPainter, QPainterPath, QPen
from PySide6.QtOpenGLWidgets import QOpenGLWidget

import perf_log
import startup_log
import theme


class VideoView(QOpenGLWidget):
    crop_drawn = Signal(QRect)         # new crop rect in frame pixels (drag draws, click doesn't)
    menu_requested = Signal(QPoint)    # global position
    _frame_ready = Signal()
    _seek_done = Signal()              # from mpv's event thread → GUI thread
    _display_changed = Signal()        # dwidth/dheight arrived → re-apply zoom

    # Read from a cache filled by mpv's event thread: a synchronous property read
    # waits for the mpv core, which is busy for the whole seek (~200 ms on long
    # files) — that froze the window while dragging the timeline (4.1.0).
    OBSERVED = ("time-pos", "pause", "eof-reached", "dwidth", "dheight", "hwdec-current")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.frame_duration_s: float | None = None   # for frame-accurate seeks
        import mpv  # imported late so app.py can prepare the DLL path first

        self.setMinimumSize(320, 180)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        options = dict(
            vo="libmpv",
            idle="yes",
            keep_open="yes",
            hr_seek="yes",
            hwdec="auto-copy",
            osc=False,
            input_default_bindings=False,
            input_vo_keyboard=False,
            osd_level=0,
            sid="no",                     # subtitles off unless picked in the menu
        )
        if os.environ.get("QTC_SAFE_GL"):     # software GL in tests
            options.update(gpu_dumb_mode="yes", hwdec="no")
        # E2 (55g): extra mpv options for experiments without a new build, e.g.
        # QTC_MPV_OPTS="audio-stream-silence=yes audio-buffer=0.1"
        for item in os.environ.get("QTC_MPV_OPTS", "").split():
            name, _, value = item.partition("=")
            if name:
                options[name.strip().replace("-", "_")] = value.strip() or "yes"
        perf_log.resume.log(f"mpv options: {options}")
        startup_log.mark("mpv module imported")
        # E2 build 2 (E): the options of this run, so a bench with QTC_MPV_OPTS is provable
        startup_log.mark("mpv options: " + " ".join(f"{k}={v}" for k, v in options.items()))
        self.player = mpv.MPV(**options)
        startup_log.mark("mpv core created")
        self._mpv = mpv
        self._render = None
        self._get_proc = None
        self._frame_ready.connect(self.update, Qt.ConnectionType.QueuedConnection)
        self._display_changed.connect(self._apply_zoom, Qt.ConnectionType.QueuedConnection)
        self._seek_done.connect(self._dispatch_seek, Qt.ConnectionType.QueuedConnection)
        self._state: dict = {"time-pos": None, "pause": True, "eof-reached": False,
                             "dwidth": 0, "dheight": 0, "hwdec-current": "no"}
        for name in self.OBSERVED:
            self.player.observe_property(name, self._on_property)
        self.player.register_event_callback(self._on_event)
        # Seek queue, latest wins: one seek in flight, the next goes when mpv reports
        # playback-restart — adapts to how fast the file seeks.
        self._in_flight: tuple[float, bool] | None = None
        self._sent_at = 0.0
        self._pending: tuple[float, bool] | None = None

        self.has_video = False
        self.frame_size = (0, 0)          # crop space (after rotation)
        self.empty_text = "Drop video or Ctrl+O"
        self.crop_enabled = False
        self.crop = QRect()
        self._draft: QRect | None = None
        self._press: QPointF | None = None
        self._press_frame: QPointF | None = None
        self._scrub_mode = False
        self._light = False
        self._stepping_until = 0.0
        # T20 canvas zoom: None = fit; otherwise device pixels per display pixel
        # (1.0 = 100%, 1:1 with the screen) and the display point at the view center.
        self._scale: float | None = None
        self._center = QPointF()
        self._actual = False             # set by Ctrl+1: window resizes keep 100%
        self._pan: QPointF | None = None
        self._sent_zoom: tuple | None = None
        try:
            self._upscale = str(self.player["scale"])   # restored at ≤ 100%
        except Exception:
            self._upscale = "bilinear"

    DRAG_THRESHOLD = 5      # px before a press becomes a crop drag
    ZOOM_STEP = 1.25        # per wheel notch, as on the timeline
    MAX_SCALE = 32.0        # device px per video px
    # ------------------------------------------------------------ GL
    def initializeGL(self) -> None:
        def get_proc_address(_ctx, name):
            context = QOpenGLContext.currentContext()
            address = context.getProcAddress(name.decode()) if context else 0
            return int(address) if address else 0

        self._get_proc = self._mpv.MpvGlGetProcAddressFn(get_proc_address)
        self._render = self._mpv.MpvRenderContext(
            self.player, "opengl",
            opengl_init_params={"get_proc_address": self._get_proc},
        )
        self._render.update_cb = self._on_render_update
        startup_log.mark("GL: mpv render context ready")
        self._send_preload()                               # E2 (55b')
        if startup_log.ENABLED:
            self.frameSwapped.connect(self._startup_swapped)
        if perf_log.resume.enabled:
            self.frameSwapped.connect(self._resume_swapped)

    def _on_render_update(self) -> None:                 # mpv thread: a new frame is ready
        if self._resume_t0 is not None and self._resume_stage == "unpaused":
            self._resume_step("mpv frame ready", "frame")
        self._frame_ready.emit()

    def _resume_step(self, text: str, stage: str) -> None:
        t0 = self._resume_t0
        if t0 is None:
            return
        self._resume_stage = stage
        perf_log.resume.log(f"  +{(time.perf_counter() - t0) * 1000:7.1f} ms  {text}")

    def _resume_swapped(self) -> None:
        if self._resume_t0 is not None and self._resume_stage == "painted":
            self._resume_step("swapped (on screen)", "done")
            perf_log.resume.log(f"  audio: {self._safe_get('current-ao')} · "
                                f"{self._safe_get('audio-device')} · "
                                f"hwdec {self._state.get('hwdec-current')} · "
                                f"vsync {self._safe_get('video-sync')} · "
                                f"display {self._safe_get('display-fps')} Hz")
            self._resume_t0 = None

    def _safe_get(self, name: str):
        try:
            return self.player[name]
        except Exception:
            return "?"

    def _startup_swapped(self) -> None:
        """T55a: the first swap after mpv drew a real video frame ends the startup log."""
        startup_log.mark("first swap", once=True)
        if self._startup_video_painted:
            startup_log.finish("first video frame on screen (swapped)")
            self.frameSwapped.disconnect(self._startup_swapped)

    _startup_restart = False        # T55a: mpv reported playback-restart (a frame is ready)
    _startup_video_painted = False

    def paintGL(self) -> None:
        if startup_log.ENABLED:
            startup_log.mark("first paintGL", once=True)
            if self._startup_restart and not self._startup_video_painted:
                self._startup_video_painted = True
                startup_log.mark("first paintGL with a decoded frame")
        ratio = self.devicePixelRatioF()
        painter = QPainter(self)
        if self._render is not None:
            # mpv changes GL state; native painting lets QPainter restore it after.
            painter.beginNativePainting()
            self._render.update()
            started = time.perf_counter()
            self._render.render(
                flip_y=True,
                block_for_target_time=False,   # don't wait for the frame's display time
                opengl_fbo={
                    "w": int(self.width() * ratio),
                    "h": int(self.height() * ratio),
                    "fbo": self.defaultFramebufferObject(),
                },
            )
            painter.endNativePainting()
            if self._resume_t0 is not None and self._resume_stage == "frame":
                self._resume_step("painted", "painted")
            if perf_log.ENABLED and self._scrub_mode:
                perf_log.log(f"render {(time.perf_counter() - started) * 1000:6.2f} ms")
        if not self.has_video:
            painter.fillRect(self.rect(), QColor(theme.VIDEO_BG))
            painter.setPen(QColor(theme.DIM))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)
        elif self.crop_enabled or self._draft is not None:
            self._paint_crop(painter)
        painter.end()

    def _paint_crop(self, painter: QPainter) -> None:
        rect = self._draft or self.crop
        if rect.isNull():
            return
        shown = self.frame_to_view(rect)
        shade = QPainterPath()
        shade.setFillRule(Qt.FillRule.OddEvenFill)
        shade.addRect(QRectF(self.video_rect()))
        shade.addRect(shown)
        painter.fillPath(shade, QColor(0, 0, 0, 140))
        pen = QPen(QColor(theme.CROP), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(shown.adjusted(0.5, 0.5, -0.5, -0.5))

    # ------------------------------------------------------ geometry
    def display_size(self) -> tuple[int, int]:
        return self._state.get("dwidth") or 0, self._state.get("dheight") or 0

    def _display_dims(self) -> tuple[float, float]:
        dw, dh = self.display_size()
        if not dw or not dh:
            dw, dh = self.frame_size
        return float(dw or 0), float(dh or 0)

    def fit_scale(self) -> float:
        """Device px per display px at fit."""
        dw, dh = self._display_dims()
        if not dw or not dh:
            return 1.0
        return min(self.width() / dw, self.height() / dh) * self.devicePixelRatioF()

    def zoom_scale(self) -> float:
        return self.fit_scale() if self._scale is None else self._scale

    def is_fit(self) -> bool:
        return self._scale is None

    def video_rect(self) -> QRectF:
        """Where mpv draws the picture: fit (keep-aspect, centered) or T20 zoom/pan."""
        dw, dh = self._display_dims()
        if not dw or not dh:
            return QRectF(self.rect())
        if self._scale is None:
            scale = min(self.width() / dw, self.height() / dh)
            w, h = dw * scale, dh * scale
            return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        k = self._scale / self.devicePixelRatioF()            # logical px per display px
        w, h = dw * k, dh * k
        x = self.width() / 2 - self._center.x() * k
        y = self.height() / 2 - self._center.y() * k
        return QRectF(x, y, w, h)

    def _clamp_center(self) -> None:
        """20b: pan stops at the picture's edges; an axis smaller than the view is centered."""
        if self._scale is None:
            return
        dw, dh = self._display_dims()
        k = self._scale / self.devicePixelRatioF()
        half_w, half_h = self.width() / 2 / k, self.height() / 2 / k
        cx, cy = self._center.x(), self._center.y()
        cx = dw / 2 if dw * k <= self.width() else min(max(cx, half_w), dw - half_w)
        cy = dh / 2 if dh * k <= self.height() else min(max(cy, half_h), dh - half_h)
        self._center = QPointF(cx, cy)

    def _apply_zoom(self) -> None:
        """Mirror the zoom state into mpv (video-zoom is log2 of the scale over fit,
        video-pan is in fractions of the scaled picture)."""
        if self._scale is not None:
            self._clamp_center()
        dw, dh = self._display_dims()
        if self._scale is None or not dw or not dh:
            values = (0.0, 0.0, 0.0, self._upscale)
        else:
            rect = self.video_rect()
            zoom = math.log2(self._scale / self.fit_scale())
            pan_x = (rect.center().x() - self.width() / 2) / rect.width()
            pan_y = (rect.center().y() - self.height() / 2) / rect.height()
            # above 100% show pixels as pixels
            values = (zoom, pan_x, pan_y, "nearest" if self._scale > 1.0001 else self._upscale)
        if values != self._sent_zoom:
            self._sent_zoom = values
            zoom, pan_x, pan_y, scaler = values
            self._command("set", "video-zoom", f"{zoom:.6f}")
            self._command("set", "video-pan-x", f"{pan_x:.6f}")
            self._command("set", "video-pan-y", f"{pan_y:.6f}")
            self._command("set", "scale", scaler)
        self.update()

    def _set_zoom(self, scale: float | None, anchor: QPointF | None = None) -> None:
        """New scale keeping the display point under `anchor` (view px) in place."""
        if not self.has_video:
            return
        dw, dh = self._display_dims()
        if scale is None or not dw or not dh:
            self._scale = None
            self._actual = False
            self._apply_zoom()
            return
        anchor = QPointF(self.width() / 2, self.height() / 2) if anchor is None else anchor
        rect = self.video_rect()
        old_k = rect.width() / dw
        point = QPointF((anchor.x() - rect.x()) / old_k, (anchor.y() - rect.y()) / old_k)
        new_k = scale / self.devicePixelRatioF()
        self._scale = scale
        self._center = QPointF(point.x() - (anchor.x() - self.width() / 2) / new_k,
                               point.y() - (anchor.y() - self.height() / 2) / new_k)
        self._apply_zoom()

    def zoom_fit(self) -> None:
        """Ctrl+0."""
        self._set_zoom(None)

    def zoom_actual(self) -> None:
        """Ctrl+1: 100% — may be below fit for a small video (20c)."""
        self._set_zoom(1.0)
        self._actual = self.has_video

    def zoom_by(self, steps: float, anchor: QPointF) -> None:
        """Wheel: 20a — zooming out stops at fit; below fit (after Ctrl+1) it jumps to fit."""
        fit = self.fit_scale()
        current = self.zoom_scale()
        if steps < 0 and current <= fit * 1.0001:
            self._set_zoom(None)
            return
        target = min(self.MAX_SCALE, current * self.ZOOM_STEP ** steps)
        self._actual = False
        if steps < 0 and target <= fit * 1.0001:
            self._set_zoom(None)
        else:
            self._set_zoom(target, anchor)

    def visible_frame_rect(self) -> QRect | None:
        """20d/T31: the visible part of the frame in source pixels (no evenness)."""
        if not self.has_video or self._scale is None:
            return None
        fw, fh = self.frame_size
        if not fw or not fh:
            return None
        rect = self.video_rect()
        shown = rect.intersected(QRectF(self.rect()))
        if shown.isEmpty():
            return None
        sx, sy = fw / rect.width(), fh / rect.height()
        left = max(0, math.floor((shown.left() - rect.left()) * sx + 1e-6))
        top = max(0, math.floor((shown.top() - rect.top()) * sy + 1e-6))
        right = min(fw, math.ceil((shown.right() - rect.left()) * sx - 1e-6))
        bottom = min(fh, math.ceil((shown.bottom() - rect.top()) * sy - 1e-6))
        return QRect(left, top, max(1, right - left), max(1, bottom - top))

    def reset_zoom(self) -> None:
        self._scale = None
        self._actual = False
        self._pan = None
        self._apply_zoom()

    def resizeEvent(self, event) -> None:
        """Keep the real scale (100% stays 100%); a wheel zoom that ends up below fit → fit."""
        super().resizeEvent(event)
        if self._scale is not None and not self._actual \
                and self._scale <= self.fit_scale() * 1.0001:
            self._scale = None
        self._apply_zoom()

    def view_to_frame(self, point: QPointF) -> QPointF:
        vr = self.video_rect()
        fw, fh = self.frame_size
        x = (point.x() - vr.x()) / vr.width() * fw
        y = (point.y() - vr.y()) / vr.height() * fh
        return QPointF(max(0.0, min(fw, x)), max(0.0, min(fh, y)))

    def frame_to_view(self, rect: QRect) -> QRectF:
        vr = self.video_rect()
        fw, fh = self.frame_size
        sx, sy = vr.width() / fw, vr.height() / fh
        return QRectF(vr.x() + rect.x() * sx, vr.y() + rect.y() * sy,
                      rect.width() * sx, rect.height() * sy)

    # ---------------------------------------------- mpv thread → cache
    def _on_property(self, name: str, value) -> None:      # mpv event thread
        self._state[name] = value
        if name == "time-pos" and perf_log.ENABLED and value is not None and self.frame_duration_s:
            perf_log.log(f"shown  frame {round(value / self.frame_duration_s)}")
        if name == "pause" and value:
            self._stepping_until = 0.0                     # the step is over
        if name == "pause" and not value and self._resume_t0 is not None:
            self._resume_step("mpv unpaused", "unpaused")
        if name in ("dwidth", "dheight"):
            self._frame_ready.emit()
            self._display_changed.emit()

    def _on_event(self, event) -> None:                    # mpv event thread
        try:
            event_id = event.event_id.value
            if startup_log.ENABLED:
                names = {self._mpv.MpvEventID.START_FILE: "mpv: start-file",
                         self._mpv.MpvEventID.FILE_LOADED: "mpv: file-loaded",
                         self._mpv.MpvEventID.VIDEO_RECONFIG: "mpv: video-reconfig",
                         self._mpv.MpvEventID.PLAYBACK_RESTART: "mpv: playback-restart"}
                if event_id in names:
                    startup_log.mark(names[event_id], once=True)
                if event_id == self._mpv.MpvEventID.PLAYBACK_RESTART:
                    self._startup_restart = True
                    self._frame_ready.emit()
            if event_id == self._mpv.MpvEventID.PLAYBACK_RESTART:
                self._seek_done.emit()
        except Exception:
            pass

    def _command(self, *args) -> None:
        """Fire-and-forget: never wait for the mpv core in the GUI thread."""
        try:
            self.player.command_async(*args)
        except Exception:
            pass

    # ------------------------------------------------------- control
    _preloaded: str | None = None

    def preload(self, path: Path, autoplay: bool) -> None:
        """E2 (55b'): start loading before ffprobe and the UI are done; `load` with the
        same path then skips its loadfile. mpv drops the video track if the file opens
        before the render context exists, so the loadfile waits for initializeGL."""
        self._preloaded = str(path)
        self._preload_waiting = True
        self._set("pause", not autoplay)
        self._set("alpha", "blend-tiles")    # mpv ≤ 0.37
        self._set("background", "tiles")     # mpv ≥ 0.38
        if self._render is not None:
            self._send_preload()

    _preload_waiting = False

    def _send_preload(self) -> None:
        if self._preload_waiting and self._preloaded is not None:
            self._preload_waiting = False
            startup_log.mark("mpv: loadfile sent (preload)", once=True)
            self._command("loadfile", self._preloaded, "replace")

    def cancel_preload(self) -> None:
        if self._preloaded is not None:
            sent = not self._preload_waiting
            self._preloaded = None
            self._preload_waiting = False
            if sent:
                self._command("stop")

    def load(self, path: Path | str, frame_size: tuple[int, int], aid: int | None,
             sid: int | None = None, autoplay: bool = True,
             frame_duration: float | None = None, sequence_fps: float | None = None) -> None:
        """`path` may be an mpv URL (T36: mf://@list for an image sequence)."""
        self.frame_size = frame_size
        self.frame_duration_s = frame_duration
        self.has_video = True
        self.reset_zoom()                # T20: each file opens at fit
        self._in_flight = self._pending = None
        paused = not autoplay        # T35: off → the first frame, paused
        self._state.update({"time-pos": None, "eof-reached": False, "pause": paused})
        # E2 build 2 (2.2): all async, in order — a synchronous property set waits while
        # the mpv core opens the (preloaded) file and froze the UI for ~200 ms.
        self._set("pause", paused)   # before loadfile: mpv shows frame 1 and waits
        if sequence_fps:
            self._set("mf-fps", f"{sequence_fps:.6f}")
        # T36: transparency on a checkerboard (export decides the rest)
        self._set("alpha", "blend-tiles")    # mpv ≤ 0.37
        self._set("background", "tiles")     # mpv ≥ 0.38
        if self._preloaded is not None and self._preloaded == str(path) \
                and not self._preload_waiting:
            startup_log.mark("mpv: preloaded file kept")       # E2: already loading
            self.set_audio(aid)          # mpv was idle before the preload: plain sets hold
            self.set_subtitle(sid)
        else:
            startup_log.mark("mpv: loadfile sent", once=True)
            self._loadfile(str(path), aid, sid)
        self._preloaded = None
        self._preload_waiting = False
        self._set("pause", paused)
        self.update()

    def _loadfile(self, url: str, aid: int | None, sid: int | None) -> None:
        """Tracks go with the loadfile as per-file options: an async `set aid` sent right
        after an async loadfile reaches the file being closed and is lost (sequence → mp4
        opened silent). Named arguments read the same in mpv ≤ 0.37 and ≥ 0.38 (which
        added an index argument). Rejected → the old way, sets after the loadfile."""
        tracks = f"aid={aid if aid else 'no'},sid={sid if sid else 'no'}"

        def reply(error, _data) -> None:                    # mpv event thread
            if error:
                self._command("loadfile", url, "replace")
                self.set_audio(aid)
                self.set_subtitle(sid)

        try:
            self.player.command_async("loadfile", url=url, flags="replace", options=tracks,
                                      callback=reply)
        except Exception:
            reply(True, None)

    def _set(self, name: str, value) -> None:
        """Async property/option set; an unknown name (renamed in this libmpv) only
        fails inside mpv, silently."""
        if isinstance(value, bool):
            value = "yes" if value else "no"
        self._command("set", name, str(value))

    def set_audio(self, aid: int | None) -> None:
        self._set("aid", aid if aid else "no")

    def set_subtitle(self, sid: int | None) -> None:
        self._set("sid", sid if sid else "no")

    def unload(self) -> None:
        self._command("stop")

    @property
    def time_pos(self) -> float | None:
        value = self._state.get("time-pos")
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def playing(self) -> bool:
        if time.monotonic() < self._stepping_until:
            return False   # frame-step unpauses for one frame — not "playing" for the UI
        return self.has_video and not self._state.get("pause") and not self._state.get("eof-reached")

    def seek(self, position: float, exact: bool = True) -> None:
        """Queued: while a seek runs, only the latest request is kept.

        Exact seeks land on the nearest frame: mpv shows the first frame that starts
        at or after the target, so aim a quarter frame before that frame's start
        (otherwise a playhead between frames showed the next one)."""
        fd = self.frame_duration_s
        if exact and fd:
            position = round(position / fd) * fd - fd / 4
        target = (max(0.0, position), exact)
        # Safety valve: if a restart event got lost, don't wait forever.
        if self._in_flight is not None and time.monotonic() - self._sent_at < 1.5:
            self._pending = target
            return
        self._send_seek(target)

    def seek_busy(self) -> bool:
        """A seek is running or queued — mpv's time-pos is stale until it lands."""
        return self._in_flight is not None or self._pending is not None

    def _send_seek(self, target: tuple[float, bool]) -> None:
        position, exact = target
        self._in_flight = target
        self._sent_at = time.monotonic()
        try:
            self.player.command_async(
                "seek", f"{position:.6f}", "absolute+exact" if exact else "absolute+keyframes",
                callback=self._seek_reply,
            )
        except Exception:
            self._in_flight = None

    def _seek_reply(self, error, _data) -> None:           # mpv event thread
        if error is not None:
            self._seek_done.emit()                         # failed seek: no restart will come

    def _dispatch_seek(self) -> None:
        if self._in_flight is not None and perf_log.ENABLED:
            kind = "exact" if self._in_flight[1] else "kf"
            perf_log.log(f"seek   {kind:5} {(time.monotonic() - self._sent_at) * 1000:7.1f} ms"
                         f"{'  (+pending)' if self._pending else ''}")
        self._in_flight = None
        if self._pending is not None:
            target, self._pending = self._pending, None
            self._send_seek(target)

    def set_loop(self, enabled: bool, start: float | None = None, end: float | None = None) -> None:
        """T21: loop the trim range (A–B) or, without one, the whole file."""
        if not enabled:
            self._command("set", "ab-loop-a", "no")
            self._command("set", "ab-loop-b", "no")
            self._command("set", "loop-file", "no")
        elif start is None or end is None:
            self._command("set", "ab-loop-a", "no")
            self._command("set", "ab-loop-b", "no")
            self._command("set", "loop-file", "inf")
        else:
            self._command("set", "loop-file", "no")
            self._command("set", "ab-loop-a", f"{start:.6f}")
            self._command("set", "ab-loop-b", f"{end:.6f}")

    def seek_relative(self, delta: float) -> None:
        self._command("seek", f"{delta:.6f}", "relative+exact")

    _resume_t0: float | None = None     # E2 (55g): Space pressed → waiting for a new frame
    _resume_stage = ""

    def _set_pause(self, paused: bool) -> None:
        if perf_log.resume.enabled:
            if not paused and self._state.get("pause"):
                self._resume_t0 = time.perf_counter()
                self._resume_stage = "sent"
                perf_log.resume.log(f"resume: pause=no sent at {self._state.get('time-pos')}")
            else:
                self._resume_t0 = None
        self._state["pause"] = paused          # optimistic: the play button reacts at once
        self._command("set", "pause", "yes" if paused else "no")

    def toggle_pause(self) -> None:
        if not self.has_video:
            return
        paused = bool(self._state.get("pause"))
        if paused and self._state.get("eof-reached"):
            self.seek(0.0)
            self._state["eof-reached"] = False
        self._set_pause(not paused)

    def pause(self) -> None:
        self._set_pause(True)

    def set_scrub_mode(self, enabled: bool) -> None:
        """Lighter software decoding while the timeline is dragged.

        Hardware decoding (the usual case) ignores these options, so they are only
        touched when mpv decodes in software.
        """
        if enabled == self._scrub_mode:
            return
        self._scrub_mode = enabled
        if enabled:
            self._light = str(self._state.get("hwdec-current") or "no") in ("no", "")
        if not self._light:
            return
        self._command("set", "vd-lavc-skiploopfilter", "all" if enabled else "default")
        self._command("set", "vd-lavc-fast", "yes" if enabled else "no")
        if not enabled:
            self._light = False

    def frame_step(self, backward: bool) -> None:
        if not backward and not self.playing:
            self._stepping_until = time.monotonic() + 0.3   # T13: no ▶ flash
        self._command("frame-back-step" if backward else "frame-step")

    def shutdown(self) -> None:
        self.makeCurrent()
        if self._render is not None:
            self._render.free()
            self._render = None
        self.doneCurrent()
        try:
            self.player.terminate()
        except Exception:
            pass

    # --------------------------------------------------------- mouse
    def set_crop_enabled(self, enabled: bool) -> None:
        self.crop_enabled = enabled
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.update()

    def set_crop(self, rect: QRect) -> None:
        self.crop = QRect(rect)
        self.update()

    # A press anywhere — in the black bars or (forwarded by the window) in the
    # margin around the view — starts a frame; the start point snaps to the
    # nearest edge/corner, so a frame from the very edge is easy to draw.
    def press_at(self, pos: QPointF) -> None:
        if not self.has_video:
            return
        self._press = QPointF(pos)
        self._press_frame = self.view_to_frame(pos)
        self._draft = None

    def move_to(self, pos: QPointF) -> None:
        if self._press_frame is None:
            return
        if self._draft is None and (pos - self._press).manhattanLength() < self.DRAG_THRESHOLD:
            return
        current = self.view_to_frame(pos)
        self._draft = QRectF(self._press_frame, current).normalized().toRect()
        self.update()

    def release_at(self, pos: QPointF) -> None:
        if self._press is None:
            return
        draft = self._draft
        if draft is not None and draft.width() >= 4 and draft.height() >= 4:
            self.crop = draft
            self.crop_drawn.emit(QRect(draft))
        self._press = self._press_frame = self._draft = None
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_at(event.position())
        elif event.button() == Qt.MouseButton.MiddleButton and self.has_video:
            self._pan = event.position()                 # T20: pan as on the timeline
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self._pan is not None:
            if self._scale is not None:
                delta = event.position() - self._pan
                k = self._scale / self.devicePixelRatioF()
                self._center -= QPointF(delta.x() / k, delta.y() / k)
                self._apply_zoom()
            self._pan = event.position()
            return
        self.move_to(event.position())

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.release_at(event.position())
        elif event.button() == Qt.MouseButton.MiddleButton and self._pan is not None:
            self._pan = None
            self.set_crop_enabled(self.crop_enabled)     # restores the cursor

    def wheelEvent(self, event) -> None:
        """T20: wheel = zoom around the cursor; horizontal wheel = pan."""
        if not self.has_video:
            return
        delta = event.angleDelta()
        if abs(delta.x()) > abs(delta.y()):
            if self._scale is not None:
                k = self._scale / self.devicePixelRatioF()
                self._center -= QPointF(delta.x() / 120 * 40 / k, 0)
                self._apply_zoom()
        elif delta.y():
            self.zoom_by(delta.y() / 120, event.position())
        event.accept()

    def contextMenuEvent(self, event) -> None:
        self.menu_requested.emit(event.globalPos())
