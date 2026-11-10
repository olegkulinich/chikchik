"""mpv rendered into a QOpenGLWidget, so the crop frame and clicks live in Qt."""
from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QOpenGLContext, QPainter, QPainterPath, QPen
from PySide6.QtOpenGLWidgets import QOpenGLWidget

import perf_log
import theme


class VideoView(QOpenGLWidget):
    crop_drawn = Signal(QRect)         # new crop rect in frame pixels (drag draws, click doesn't)
    menu_requested = Signal(QPoint)    # global position
    _frame_ready = Signal()
    _seek_done = Signal()              # from mpv's event thread → GUI thread

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
        self.player = mpv.MPV(**options)
        self._mpv = mpv
        self._render = None
        self._get_proc = None
        self._frame_ready.connect(self.update, Qt.ConnectionType.QueuedConnection)
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

    DRAG_THRESHOLD = 5      # px before a press becomes a crop drag
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
        self._render.update_cb = self._frame_ready.emit

    def paintGL(self) -> None:
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

    def video_rect(self) -> QRectF:
        """Where mpv draws the picture (keep-aspect, centered)."""
        dw, dh = self.display_size()
        if not dw or not dh:
            dw, dh = self.frame_size
        if not dw or not dh:
            return QRectF(self.rect())
        scale = min(self.width() / dw, self.height() / dh)
        w, h = dw * scale, dh * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

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
        if name == "pause" and value:
            self._stepping_until = 0.0                     # the step is over
        if name in ("dwidth", "dheight"):
            self._frame_ready.emit()

    def _on_event(self, event) -> None:                    # mpv event thread
        try:
            if event.event_id.value == self._mpv.MpvEventID.PLAYBACK_RESTART:
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
    def load(self, path: Path, frame_size: tuple[int, int], aid: int | None,
             sid: int | None = None, autoplay: bool = True,
             frame_duration: float | None = None) -> None:
        self.frame_size = frame_size
        self.frame_duration_s = frame_duration
        self.has_video = True
        self._in_flight = self._pending = None
        paused = not autoplay        # T35: off → the first frame, paused
        self._state.update({"time-pos": None, "eof-reached": False, "pause": paused})
        self.player.pause = paused   # before loadfile: mpv shows frame 1 and waits
        self.player.command("loadfile", str(path), "replace")
        self.set_audio(aid)
        self.set_subtitle(sid)
        self.player.pause = paused
        self.update()

    def set_audio(self, aid: int | None) -> None:
        try:
            self.player.aid = aid if aid else "no"
        except Exception:
            pass

    def set_subtitle(self, sid: int | None) -> None:
        try:
            self.player.sid = sid if sid else "no"
        except Exception:
            pass

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

    def _set_pause(self, paused: bool) -> None:
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

    def mouseMoveEvent(self, event) -> None:
        self.move_to(event.position())

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.release_at(event.position())

    def contextMenuEvent(self, event) -> None:
        self.menu_requested.emit(event.globalPos())
