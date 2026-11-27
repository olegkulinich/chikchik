"""Video playback, crop geometry and input shared by both presentation paths."""
from __future__ import annotations

import math
import os
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QOpenGLContext, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication
from .video_backend import USE_D3D11

if USE_D3D11:
    from .d3d11_view import D3D11Surface as VideoSurface
else:
    from PySide6.QtOpenGLWidgets import QOpenGLWidget as VideoSurface

from . import perf_log
from . import startup_log
from . import theme
from .widgets import _is_pen


def _gl_renderer() -> str:
    """E4 (T95): which OpenGL the window got (desktop driver vs ANGLE / software)."""
    try:
        functions = QOpenGLContext.currentContext().functions()
        return f"{functions.glGetString(0x1F01)} · GL {functions.glGetString(0x1F02)}"
    except Exception as exc:
        return f"? ({exc.__class__.__name__})"


def _silence_minutes() -> float:
    """E3 (T93): minutes of pause with the audio device kept awake (QTC_SILENCE_MIN)."""
    try:
        return max(0.05, float(os.environ.get("QTC_SILENCE_MIN", "") or 10))
    except ValueError:
        return 10.0


class VideoView(VideoSurface):
    crop_drawn = Signal(QRect)         # new crop rect in frame pixels (draw / move / resize, on release)
    crop_changing = Signal(QRect)      # D: the rect while it is dragged (fields follow, no history)
    scrub_started = Signal()           # T100: drag on the video with Crop off
    scrub_moved = Signal(int)          # frames from where the scrub started
    scrub_finished = Signal()
    menu_requested = Signal(QPoint)    # global position
    zoomed = Signal()                  # T101: the user changed the canvas zoom (wheel)
    _frame_ready = Signal()
    _seek_done = Signal()              # from mpv's event thread → GUI thread
    _display_changed = Signal()        # dwidth/dheight arrived → re-apply zoom
    _pause_changed = Signal(bool)      # E3 (T93): mpv's pause → GUI thread

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
            # E4 build 2: no GPU interop — the -copy decoders hand frames back to the CPU, and
            # loading the interops (d3d11va, dxva2, cuda, vulkan…) cost ~65 ms of the render
            # context (bench 2026-11-15: window −70 ms, first frame −80 ms).
            gpu_hwdec_interop="no",
            osc=False,
            input_default_bindings=False,
            input_vo_keyboard=False,
            osd_level=0,
            sid="no",                     # subtitles off unless picked in the menu
            # E2: keep the audio device fed with silence while paused — after a pause
            # longer than ~10–15 s Windows let it sleep, and play started with a jump +
            # stall while it woke up (user test 2026-11-14: gone with this option).
            audio_stream_silence="yes",
        )
        if USE_D3D11:
            options.update(self.output_options)
            local_app_data = os.environ.get("LOCALAPPDATA")
            if local_app_data:
                # Embedded mpv needs an explicit directory to reuse compiled shaders.
                cache_dir = Path(local_app_data) / "ChikChik" / "shader-cache"
                try:
                    cache_dir.mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass  # Playback can proceed if the cache directory is unavailable.
                else:
                    options["gpu_shader_cache_dir"] = str(cache_dir)
        if os.environ.get("QTC_SAFE_GL"):     # software GL in tests
            options.update(gpu_dumb_mode="yes", hwdec="no")
        # E2 (55g): extra mpv options for experiments without a new build, e.g.
        # QTC_MPV_OPTS="audio-stream-silence=yes audio-buffer=0.1"
        from .backend import mpv_opts_env

        extra = mpv_opts_env()
        options.update(extra)
        # E3 (T93): the silence keeps a SYSTEM power request (blocks automatic sleep), so
        # it is fed only for the first QTC_SILENCE_MIN minutes of a pause (default 10).
        self._silence = str(options.get("audio_stream_silence", "no")) == "yes"
        self._silence_off = False
        self._silence_timer = QTimer(self)
        self._silence_timer.setSingleShot(True)
        self._silence_timer.setInterval(int(_silence_minutes() * 60_000))
        self._silence_timer.timeout.connect(self._silence_sleep)
        self._pause_changed.connect(self._on_pause_changed, Qt.ConnectionType.QueuedConnection)
        perf_log.resume.log(f"mpv options: {options}")
        startup_log.mark("mpv module imported")
        # E2 build 2 (E): the options of this run, so a bench with QTC_MPV_OPTS is provable
        startup_log.mark("mpv options: " + " ".join(f"{k}={v}" for k, v in options.items()))
        trace = {}
        if startup_log.TRACE:           # E4 (T95): mpv's own steps into the startup log
            trace = dict(log_handler=startup_log.mpv_message, loglevel="v")
        try:
            self.player = mpv.MPV(**trace, **options)
        except Exception as exc:     # a typo in QTC_MPV_OPTS must not stop the app
            if not extra and "gpu_hwdec_interop" not in options:
                raise
            startup_log.mark(f"mpv options rejected ({exc.__class__.__name__}), extras dropped")
            for name in [*extra, "gpu_hwdec_interop"]:    # E4: an older libmpv may not know it
                options.pop(name, None)
            self.player = mpv.MPV(**trace, **options)
        startup_log.mark("mpv core created")
        self._mpv = mpv
        self._playing_entry = None
        self._replacement_entry = None
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
        self._in_flight: tuple[float | None, bool] | None = None
        self._sent_at = 0.0
        self._pending: tuple[float | None, bool] | None = None
        self._landed: tuple[float, bool] | None = None   # T115: last seek that finished
        self._resume_after = False         # T115: unpause once the queue is empty

        self.has_video = False
        self.frame_size = (0, 0)          # crop space (after rotation)
        self.empty_text = "Drop video or Ctrl+O"
        self.crop_enabled = False
        self.crop = QRect()
        self._draft: QRect | None = None
        self._press: QPointF | None = None
        self._press_frame: QPointF | None = None
        self._press_raw = QPointF()      # D: press in frame px, not clamped (move / resize deltas)
        self._press_global = QPoint()
        self._press_pen = False
        self._gesture: str | None = None   # "draw" | "move" | "resize" | "scrub"
        self._edges: tuple[str | None, str | None] = (None, None)   # T52: ("L"/"R", "T"/"B")
        self._rect0 = QRectF()
        self._scrubbing = False
        self._scrub_acc = 0.0
        self._scrub_last_x = 0.0
        self._scrub_frames = 0
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
    EDIT_THRESHOLD = 2      # px before a press on the frame / with Ctrl moves or resizes it
    SCRUB_START = 3         # px before a press with Crop off becomes a frame scrub
    EDGE_ZONE = 6           # T52: screen px around the crop edges (same at any zoom)
    CORNER_ZONE = 9         # screen px around the corners (≥ half of the T108 handle + edge)
    MIN_CROP = 4            # frame px — the smallest rect drawn or resized
    WARP = 100              # T100: px from the press before the hidden cursor is put back
    # T100 hidden switch: screen px per frame of the scrub on the video (1–30). Default 10
    # (user, D build 1 check): at 1–3 the smallest hand move on a high-DPI mouse is a frame.
    try:
        SCRUB_PX = max(1, min(30, int(os.environ.get("QTC_SCRUB_PX", "10"))))
    except ValueError:
        SCRUB_PX = 10
    ZOOM_STEP = 1.25        # per wheel notch, as on the timeline
    MAX_SCALE = 32.0        # device px per video px
    # ------------------------------------------------------------ GL
    def initializeGL(self) -> None:
        startup_log.mark("GL: initializeGL entered (Qt context current)")

        def get_proc_address(_ctx, name):
            context = QOpenGLContext.currentContext()
            address = context.getProcAddress(name.decode()) if context else 0
            return int(address) if address else 0

        self._get_proc = self._mpv.MpvGlGetProcAddressFn(get_proc_address)
        if startup_log.ENABLED:
            startup_log.mark("GL: renderer " + _gl_renderer())
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
        try:                 # player[name] reads options/<name>: no current-ao there
            return getattr(self.player, name.replace("-", "_"))
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
        line = shown.adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRect(line)
        if self.crop_enabled:
            self._paint_handles(painter, line)

    HANDLE = 7          # T108: corner squares, odd → centred on the 1 px line (3 px each side)

    def _paint_handles(self, painter: QPainter, line: QRectF) -> None:
        """T108: white squares with a 1 px crop-colour stroke on the 4 corners. Whole
        where the widget has room (grey surround, zoom); clipped at its edges."""
        half = self.HANDLE // 2
        stroke, fill = QColor(theme.CROP), QColor(theme.CROP_HANDLE)
        painter.setPen(Qt.PenStyle.NoPen)
        for corner in (line.topLeft(), line.topRight(), line.bottomLeft(), line.bottomRight()):
            # the pixel the 1 px line passes through at this corner
            cx, cy = math.floor(corner.x()), math.floor(corner.y())
            outer = QRect(cx - half, cy - half, self.HANDLE, self.HANDLE)
            painter.fillRect(outer, stroke)
            painter.fillRect(outer.adjusted(1, 1, -1, -1), fill)

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

    def zoom_text(self) -> str:
        """T101: the canvas zoom for the toast — `250%`, at fit `Fit · 43%`."""
        percent = f"{self.zoom_scale() * 100:.0f}%"
        return f"Fit · {percent}" if self._scale is None else percent

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
        if steps > 0 and current < 1.0 < target:
            target = 1.0
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
        fw, fh = self.frame_size
        raw = self._view_to_frame_raw(point)
        return QPointF(max(0.0, min(fw, raw.x())), max(0.0, min(fh, raw.y())))

    def _view_to_frame_raw(self, point: QPointF) -> QPointF:
        vr = self.video_rect()
        fw, fh = self.frame_size
        return QPointF((point.x() - vr.x()) / vr.width() * fw,
                       (point.y() - vr.y()) / vr.height() * fh)

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
        if name == "pause":
            self._pause_changed.emit(bool(value))
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
            if USE_D3D11 and self.composition is not None and event_id in (
                self._mpv.MpvEventID.START_FILE, self._mpv.MpvEventID.VIDEO_RECONFIG
            ):
                self._composition_needs_attach = True
            if event_id == self._mpv.MpvEventID.PLAYBACK_RESTART:
                self._seek_done.emit()
            if USE_D3D11:
                self._on_native_file_event(event)
        except Exception:
            pass

    def _on_native_file_event(self, event) -> None:
        ids = self._mpv.MpvEventID
        event_id = event.event_id.value
        if event_id == ids.START_FILE:
            self._playing_entry = event.data.playlist_entry_id
        if self._replacement_entry is None:
            return
        entry, token = self._replacement_entry
        if ((event_id == ids.PLAYBACK_RESTART and self._playing_entry == entry)
                or (event_id == ids.END_FILE and event.data.playlist_entry_id == entry)):
            self._replacement_entry = None
            self._replacement_ready.emit(token)

    def _command(self, *args) -> None:
        """Fire-and-forget: never wait for the mpv core in the GUI thread."""
        try:
            self.player.command_async(*args)
        except Exception:
            pass

    # ------------------------------------------------------- control
    _preloaded: str | None = None

    def preload(self, url, autoplay: bool, decoder=None, sequence_fps: float | None = None
                ) -> None:
        """E2 (55b'): start loading before ffprobe and the UI are done; `load` with the
        same url then skips its loadfile. `url`: a path/str or (T96) a callable returning
        it or None (a sequence whose frame list isn't ready — asked again in
        initializeGL, then given up). `decoder`: () -> decoder loadfile options, asked when
        the video starts."""
        self._preload_url = url if callable(url) else (lambda u=str(url): u)
        self._preload_decoder = decoder
        self._preload_waiting = True
        if sequence_fps:
            self._set("mf-fps", f"{sequence_fps:.6f}")
        self._set("alpha", "blend-tiles")    # mpv ≤ 0.37
        self._set("background", "tiles")     # mpv ≥ 0.38
        self._set("pause", not autoplay)
        if USE_D3D11 or self._render is not None:
            self._send_preload()

    _preload_waiting = False
    _preload_decoder = None
    _preload_url = None

    def _decoder(self) -> str:
        decoder = self._preload_decoder() if self._preload_decoder else ""
        startup_log.mark(f"decoder (preload): {decoder or 'default'}")
        return decoder or ""

    def _send_preload(self) -> None:
        if self._preload_waiting and self._preload_url is not None:
            self._preload_waiting = False
            target = self._preload_url()
            if target is None:
                startup_log.mark("preload: url not ready, given up")
                return
            self._preloaded = target
            decoder = self._decoder()
            startup_log.mark("mpv: loadfile sent (preload)", once=True)
            self._loadfile(target, decoder)

    def cancel_preload(self) -> None:
        if self._preloaded is not None:
            sent = not self._preload_waiting
            self._preloaded = None
            self._preload_waiting = False
            if sent:
                self._command("stop")

    def load(self, path: Path | str, frame_size: tuple[int, int], aid: int | None,
             sid: int | None = None, autoplay: bool = True,
             frame_duration: float | None = None, sequence_fps: float | None = None,
             decoder: str = "", replacement_image=None) -> None:
        """`path` may be an mpv URL (T36: mf://@list for an image sequence). `decoder`
        (E4): per-file decoder loadfile options ("hwdec=no,vd-lavc-threads=4"), "" = the
        global defaults."""
        replacement = None
        if USE_D3D11 and (replacement_image is not None or (
                self._first_frame_ready and self.has_video and self._scale is not None)
                or self._replacement_token is not None):
            # The zoom reset can redraw the outgoing frame. Hide its HWND until the
            # incoming file renders, using the viewport snapshot while it loads.
            replacement = self.begin_replacement(replacement_image)
        self.frame_size = frame_size
        self.frame_duration_s = frame_duration
        self.has_video = True
        self.reset_zoom()                # T20: each file opens at fit
        self._in_flight = self._pending = self._landed = None
        self._resume_after = False
        paused = not autoplay        # T35: off → the first frame, paused
        self._state.update({"time-pos": None, "eof-reached": False, "pause": paused})
        self._silence_timer.stop()
        self._wake_audio()           # T93: a new file starts with the silence back on
        kept = self._preloaded is not None and self._preloaded == str(path) \
            and not self._preload_waiting
        # E2 build 2 (2.2): all async, in order — a synchronous property set waits while
        # the mpv core opens the (preloaded) file and froze the UI for ~200 ms.
        self._set("pause", paused)       # before loadfile: mpv shows frame 1 and waits
        if sequence_fps:
            self._set("mf-fps", f"{sequence_fps:.6f}")
        # T36: transparency on a checkerboard (export decides the rest)
        self._set("alpha", "blend-tiles")    # mpv ≤ 0.37
        self._set("background", "tiles")     # mpv ≥ 0.38
        if kept:
            startup_log.mark("mpv: preloaded file kept")       # E2: already loading
            self.set_audio(aid)          # mpv was idle before the preload: plain sets hold
            self.set_subtitle(sid)
        else:
            startup_log.mark(f"decoder: {decoder or 'default'}")
            startup_log.mark("mpv: loadfile sent", once=True)
            tracks = f"aid={aid if aid else 'no'},sid={sid if sid else 'no'}"
            self._loadfile(str(path), tracks + (f",{decoder}" if decoder else ""), aid, sid,
                           replacement=replacement)
        self._preloaded = None
        self._preload_waiting = False
        self._set("pause", paused)
        self.update()

    _KEEP = object()        # preload: tracks stay as they are

    def _loadfile(self, url: str, options: str, aid=_KEEP, sid=_KEEP,
                  *, replacement=None) -> None:
        """Tracks (and E4: the decoder) go with the loadfile as per-file options: an async
        `set aid` sent right after an async loadfile reaches the file being closed and is
        lost (sequence → mp4 opened silent). Named arguments read the same in mpv ≤ 0.37
        and ≥ 0.38 (which added an index argument). Rejected → the old way, sets after
        the loadfile (the decoder then stays the global default)."""
        if not options:
            self._command("loadfile", url, "replace")
            return

        def track_replacement(error, data) -> None:         # mpv event thread
            if replacement is not None:
                entry = data.get("playlist_entry_id") if isinstance(data, dict) else None
                if not error and entry is not None:
                    self._replacement_entry = (entry, replacement)
                else:
                    self._replacement_ready.emit(replacement)

        def reply(error, data) -> None:                     # mpv event thread
            if not error:
                track_replacement(error, data)
                return
            if replacement is None:
                self._command("loadfile", url, "replace")
            else:
                try:
                    self.player.command_async("loadfile", url, "replace",
                                              callback=track_replacement)
                except Exception:
                    self._replacement_ready.emit(replacement)
            if aid is not self._KEEP:
                self.set_audio(aid)
            if sid is not self._KEEP:
                self.set_subtitle(sid)

        try:
            self.player.command_async("loadfile", url=url, flags="replace", options=options,
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
    def at_end(self) -> bool:
        return bool(self._state.get("eof-reached"))

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
        if self._is_repeat(target):
            if perf_log.ENABLED:
                perf_log.log("seek   skip (same target)")
            return
        self._queue_seek(target)

    def _queue_seek(self, target: tuple[float | None, bool]) -> None:
        # Safety valve: if a restart event got lost, don't wait forever.
        if self._in_flight is not None and time.monotonic() - self._sent_at < 1.5:
            self._pending = target
            return
        self._send_seek(target)

    def _is_repeat(self, target: tuple[float, bool]) -> bool:
        """T115: a seek to where mpv already goes / already is restarts audio and
        shows the frame again (playing: 1–2 frames back) — skip it."""
        if self._in_flight is not None:
            if time.monotonic() - self._sent_at >= 1.5:
                return False                    # lost restart event: let the valve resend
            return target == (self._pending if self._pending is not None else self._in_flight)
        if target != self._landed or self.playing:
            return False
        pos = self.time_pos
        if pos is None:
            return False
        fd = self.frame_duration_s or 0.04
        shown = target[0] + fd / 4 if target[1] else target[0]
        return abs(pos - shown) < fd / 2

    def seek_busy(self) -> bool:
        """A seek is running or queued — mpv's time-pos is stale until it lands."""
        return self._in_flight is not None or self._pending is not None

    def _send_seek(self, target: tuple[float | None, bool]) -> None:
        position, exact = target
        self._in_flight = target
        self._sent_at = time.monotonic()
        if position is None:
            amount, mode = "0", "relative+exact"
        else:
            amount = f"{position:.6f}"
            mode = "absolute+exact" if exact else "absolute+keyframes"
        try:
            self.player.command_async("seek", amount, mode, callback=self._seek_reply)
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
        if self._in_flight is not None:
            position, exact = self._in_flight
            self._landed = (position, exact) if position is not None else None
        self._in_flight = None
        if self._pending is not None:
            target, self._pending = self._pending, None
            self._send_seek(target)
        elif self._resume_after:
            self._resume_queued()

    def resume_after_shuttle(self) -> None:
        """Resync at mpv's paused position and resume after the seek settles."""
        self._queue_seek((None, True))
        self.resume_after_seek()

    def resume_after_seek(self) -> None:
        """T115: unpause once the seek queue is empty — one clean audio start from the
        final frame instead of a restart per queued seek."""
        if self.seek_busy() and time.monotonic() - self._sent_at < 1.5:
            self._resume_after = True
            return
        self._resume_queued()

    def _resume_queued(self) -> None:
        self._resume_after = False
        if perf_log.ENABLED:
            perf_log.log("resume after scrub")
        self._set_pause(False)

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

    def set_volume(self, percent: int) -> None:
        """T66: 0–100."""
        self._command("set", "volume", str(int(percent)))

    def set_mute(self, on: bool) -> None:
        self._command("set", "mute", "yes" if on else "no")

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
        self._resume_after = False             # T115: any explicit pause/play wins
        self._state["pause"] = paused          # optimistic: the play button reacts at once
        if not paused:
            self._wake_audio()                 # T93: before pause=no, commands go in order
        self._command("set", "pause", "yes" if paused else "no")

    # E3 (T93): audio-stream-silence only takes effect when mpv opens the audio output,
    # so switching it means `ao-reload` (keeps the frame: mpv does a refresh seek).
    def _on_pause_changed(self, paused: bool) -> None:
        if not self._silence:
            return
        if paused:
            if not self._silence_off and not self._silence_timer.isActive():
                self._silence_timer.start()
        else:
            self._silence_timer.stop()
            self._wake_audio()                 # unpaused some other way (late but safe)

    def _silence_sleep(self) -> None:
        if not self._state.get("pause") or self._silence_off:
            return
        self._silence_off = True
        perf_log.resume.log("T93: long pause → audio-stream-silence=no + ao-reload")
        self._command("set", "audio-stream-silence", "no")
        self._command("ao-reload")

    def _wake_audio(self) -> None:
        if not self._silence_off:
            return
        self._silence_off = False
        perf_log.resume.log("T93: play after long pause → audio-stream-silence=yes + ao-reload")
        self._command("set", "audio-stream-silence", "yes")
        self._command("ao-reload")

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

    # --------------------------------------------------------- JKL (T67)
    def play(self) -> None:
        """Unpause (no toggle); at the end of the file starts over, like Play."""
        if not self.has_video:
            return
        if self._state.get("eof-reached"):
            self.seek(0.0)
            self._state["eof-reached"] = False
        self._set_pause(False)

    def set_speed(self, speed: float) -> None:
        self._command("set", "speed", f"{speed:g}")

    def drop_pending_seek(self) -> None:
        """The seek in flight lands; a queued one is forgotten (the frame on screen stays)."""
        self._pending = None

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
        if USE_D3D11:
            self._closing = True
            self._replacement_capture = None
            self._reveal_timer.stop()
            self.layer.hide()
            self._silence_timer.stop()
            self.player.unregister_event_callback(self._on_event)
            for name in self.OBSERVED:
                self.player.unobserve_property(name, self._on_property)
            self.player.pause = True
            return   # app.main terminates mpv after Qt releases the native windows
        try:
            self.makeCurrent()
            try:
                if self._render is not None:
                    self._render.free()
                    self._render = None
            finally:
                self.doneCurrent()
        finally:
            try:
                self.player.terminate()
            except Exception:
                pass

    # --------------------------------------------------------- mouse
    # D (T100, T51, T52, D1.1): Crop off → drag = frame scrub. Crop on → a press on
    # an edge / corner resizes (Shift keeps proportions, Alt from the centre),
    # Ctrl+press anywhere moves the rect, any other press draws a new one.
    def set_crop_enabled(self, enabled: bool) -> None:
        self.crop_enabled = enabled
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.update()

    def set_crop(self, rect: QRect) -> None:
        self.crop = QRect(rect)
        self.update()

    def _zone_at(self, pos: QPointF) -> tuple[str | None, str | None] | None:
        """T52: the edge / corner under pos (screen px zones), nearest one wins."""
        if not self.crop_enabled or self.crop.isNull() or not self.has_video:
            return None
        r = self.frame_to_view(self.crop)
        x, y = pos.x(), pos.y()
        best, best_d = None, None
        for hx, cx in (("L", r.left()), ("R", r.right())):
            for hy, cy in (("T", r.top()), ("B", r.bottom())):
                if abs(x - cx) <= self.CORNER_ZONE and abs(y - cy) <= self.CORNER_ZONE:
                    d = math.hypot(x - cx, y - cy)
                    if best_d is None or d < best_d:
                        best, best_d = (hx, hy), d
        if best is not None:
            return best
        z = self.EDGE_ZONE
        if r.top() - z <= y <= r.bottom() + z:
            for hx, cx in (("L", r.left()), ("R", r.right())):
                if abs(x - cx) <= z and (best_d is None or abs(x - cx) < best_d):
                    best, best_d = (hx, None), abs(x - cx)
        if r.left() - z <= x <= r.right() + z:
            for hy, cy in (("T", r.top()), ("B", r.bottom())):
                if abs(y - cy) <= z and (best_d is None or abs(y - cy) < best_d):
                    best, best_d = (None, hy), abs(y - cy)
        return best

    _ZONE_CURSORS = {
        ("L", "T"): Qt.CursorShape.SizeFDiagCursor, ("R", "B"): Qt.CursorShape.SizeFDiagCursor,
        ("R", "T"): Qt.CursorShape.SizeBDiagCursor, ("L", "B"): Qt.CursorShape.SizeBDiagCursor,
        ("L", None): Qt.CursorShape.SizeHorCursor, ("R", None): Qt.CursorShape.SizeHorCursor,
        (None, "T"): Qt.CursorShape.SizeVerCursor, (None, "B"): Qt.CursorShape.SizeVerCursor,
    }

    def cursor_at(self, pos: QPointF, modifiers) -> Qt.CursorShape:
        """Hover cursor (also asked by the window for its band around the view)."""
        if not self.crop_enabled or not self.has_video:
            return Qt.CursorShape.ArrowCursor
        if modifiers & Qt.KeyboardModifier.ControlModifier and not self.crop.isNull():
            return Qt.CursorShape.SizeAllCursor
        zone = self._zone_at(pos)
        return self._ZONE_CURSORS[zone] if zone else Qt.CursorShape.CrossCursor

    def hover_at(self, pos: QPointF, modifiers) -> None:
        if self._press is None and self._pan is None:
            shape = self.cursor_at(pos, modifiers)
            if self.cursor().shape() != shape:
                self.setCursor(shape)

    # A press anywhere — in the black bars or (forwarded by the window) in the
    # margin around the view — starts a gesture; a drawn frame's start point is
    # clamped to the frame, so a frame from the very edge is easy to draw.
    def press_at(self, pos: QPointF, event=None) -> None:
        if not self.has_video:
            return
        modifiers = event.modifiers() if event is not None else Qt.KeyboardModifier.NoModifier
        self._press = QPointF(pos)
        self._press_frame = self.view_to_frame(pos)
        self._press_raw = self._view_to_frame_raw(pos)
        self._press_global = (event.globalPosition().toPoint() if event is not None
                              else self.mapToGlobal(pos.toPoint()))
        self._press_pen = event is not None and _is_pen(event)
        self._draft = None
        self._rect0 = QRectF(self.crop)
        if not self.crop_enabled:
            self._gesture = "scrub"
            self._scrubbing = False
        elif modifiers & Qt.KeyboardModifier.ControlModifier and not self.crop.isNull():
            self._gesture = "move"                                   # T51
        else:
            zone = self._zone_at(pos)
            self._gesture = "resize" if zone else "draw"             # T52 / D1.1
            self._edges = zone or (None, None)

    def move_to(self, pos: QPointF, event=None) -> None:
        modifiers = event.modifiers() if event is not None else Qt.KeyboardModifier.NoModifier
        if self._press is None:
            self.hover_at(pos, modifiers)
            return
        gesture = self._gesture
        if gesture == "scrub":
            self._scrub_move(pos, event)
            return
        threshold = self.DRAG_THRESHOLD if gesture == "draw" else self.EDIT_THRESHOLD
        if self._draft is None and (pos - self._press).manhattanLength() < threshold:
            return
        if gesture == "draw":
            current = self.view_to_frame(pos)
            self._draft = QRectF(self._press_frame, current).normalized().toRect()
        elif gesture == "move":
            self._draft = self._moved(self._view_to_frame_raw(pos))
        else:
            self._draft = self._resized(self._view_to_frame_raw(pos), modifiers)
        if gesture != "draw" or (self._draft.width() >= self.MIN_CROP
                                 and self._draft.height() >= self.MIN_CROP):
            self.crop_changing.emit(QRect(self._draft))
        self.update()

    def release_at(self, pos: QPointF, event=None) -> None:
        if self._press is None:
            return
        gesture, draft = self._gesture, self._draft
        if gesture == "scrub":
            self._scrub_end()
        elif draft is not None:
            if gesture == "draw":
                ok = draft.width() >= self.MIN_CROP and draft.height() >= self.MIN_CROP
            else:
                ok = draft != self.crop
            if ok:
                self.crop = draft
                self.crop_drawn.emit(QRect(draft))
            else:
                self.crop_changing.emit(QRect(self.crop))   # fields back to the kept rect
        self._press = self._press_frame = self._draft = None
        self._gesture = None
        self.update()
        self.hover_at(pos, QApplication.keyboardModifiers())

    # --- T51: move the rect by the same picture px, stops at the frame edge
    def _moved(self, raw: QPointF) -> QRect:
        fw, fh = self.frame_size
        r0 = self._rect0
        left = max(0.0, min(fw - r0.width(), r0.left() + raw.x() - self._press_raw.x()))
        top = max(0.0, min(fh - r0.height(), r0.top() + raw.y() - self._press_raw.y()))
        return QRect(round(left), round(top), round(r0.width()), round(r0.height()))

    # --- T52 / D1.3: edges and corners; Shift = keep proportions (an edge changes the
    # other side symmetrically around the centre line, like Figma), Alt = from centre.
    def _resized(self, raw: QPointF, modifiers) -> QRect:
        fw, fh = self.frame_size
        r0 = self._rect0
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
        hx, hy = self._edges

        def axis(edge, lo0, size0, delta):
            """(new size, anchor: "lo" | "hi" | "mid", active)"""
            if edge is None:
                return size0, ("mid" if shift else "lo"), False
            k = 2 if alt else 1
            if edge in ("L", "T"):
                return size0 - delta * k, ("mid" if alt else "hi"), True
            return size0 + delta * k, ("mid" if alt else "lo"), True

        def room(anchor, lo0, size0, limit):
            if anchor == "lo":
                return limit - lo0
            if anchor == "hi":
                return lo0 + size0
            centre = lo0 + size0 / 2
            return 2 * min(centre, limit - centre)

        def place(anchor, lo0, size0, size):
            if anchor == "lo":
                return lo0
            if anchor == "hi":
                return lo0 + size0 - size
            return lo0 + size0 / 2 - size / 2

        w0, h0 = r0.width(), r0.height()
        w, ax, act_x = axis(hx, r0.left(), w0, raw.x() - self._press_raw.x())
        h, ay, act_y = axis(hy, r0.top(), h0, raw.y() - self._press_raw.y())
        w_room = room(ax, r0.left(), w0, fw)
        h_room = room(ay, r0.top(), h0, fh)
        if shift and w0 > 0 and h0 > 0:
            if act_x and act_y:
                s = max(w / w0, h / h0)
            else:
                s = w / w0 if act_x else h / h0
            s = max(s, self.MIN_CROP / w0, self.MIN_CROP / h0)
            s = min(s, w_room / w0, h_room / h0)
            w, h = w0 * s, h0 * s
        else:
            w = max(self.MIN_CROP, min(w, w_room))
            h = max(self.MIN_CROP, min(h, h_room))
        left = place(ax, r0.left(), w0, w)
        top = place(ay, r0.top(), h0, h)
        rect = QRect(round(left), round(top), max(1, round(w)), max(1, round(h)))
        rect.moveLeft(max(0, min(rect.left(), fw - rect.width())))
        rect.moveTop(max(0, min(rect.top(), fh - rect.height())))
        return rect

    # --- T100: endless frame scrub, like Alt+Drag on the timeline / the fields
    def _scrub_move(self, pos: QPointF, event) -> None:
        pen = self._press_pen
        if not self._scrubbing:
            if abs(pos.x() - self._press.x()) < self.SCRUB_START:
                return
            self._scrubbing = True
            self._scrub_acc = 0.0
            self._scrub_frames = 0
            self._scrub_last_x = (event.globalPosition().x() if pen and event is not None
                                  else float(QCursor.pos().x()))
            QApplication.setOverrideCursor(
                Qt.CursorShape.SizeHorCursor if pen else Qt.CursorShape.BlankCursor)
            self.scrub_started.emit()
            return
        # the real cursor: queued events may predate a warp (as in the fields)
        x = event.globalPosition().x() if pen and event is not None else float(QCursor.pos().x())
        self._scrub_acc += x - self._scrub_last_x
        self._scrub_last_x = x
        steps = int(self._scrub_acc / self.SCRUB_PX)
        if steps:
            self._scrub_acc -= steps * self.SCRUB_PX
            self._scrub_frames += steps
            self.scrub_moved.emit(self._scrub_frames)
        if not pen and abs(QCursor.pos().x() - self._press_global.x()) > self.WARP:
            QCursor.setPos(self._press_global)
            self._scrub_last_x = float(self._press_global.x())

    def _scrub_end(self) -> None:
        if not self._scrubbing:
            return
        self._scrubbing = False
        if not self._press_pen:
            QCursor.setPos(self._press_global)       # the cursor reappears where it was pressed
        QApplication.restoreOverrideCursor()
        self.scrub_finished.emit()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_at(event.position(), event)
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
        self.move_to(event.position(), event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.release_at(event.position(), event)
        elif event.button() == Qt.MouseButton.MiddleButton and self._pan is not None:
            self._pan = None
            self.set_crop_enabled(self.crop_enabled)     # restores the cursor
            self.hover_at(event.position(), event.modifiers())

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
            self.zoomed.emit()
        event.accept()

    def contextMenuEvent(self, event) -> None:
        self.menu_requested.emit(event.globalPos())
