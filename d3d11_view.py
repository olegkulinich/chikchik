"""Windows presentation. mpv owns the video pixels, Qt owns the crop and input."""
import ctypes
import os
from ctypes import wintypes

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QRegion
from PySide6.QtWidgets import QWidget

import startup_log
import theme
from win_composition import CompositionTarget


class CropLayer(QWidget):
    def __init__(self, view):
        super().__init__(view, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
                         | Qt.WindowTransparentForInput)
        self.view = view
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setClipRegion(self.view.exposed)
        if self.view._replacement_image is not None:
            self.view._paint_replacement(painter)
        self.view._paint_crop(painter)
        painter.end()


class D3D11Surface(QWidget):
    surface_ready = Signal()
    _replacement_ready = Signal(object)
    _replacement_captured = Signal(object, object)

    def __init__(self, parent):
        super().__init__(parent)
        self.host = QWidget(self)
        self.host.setAttribute(Qt.WA_DontCreateNativeAncestors)
        self.host.setAttribute(Qt.WA_TransparentForMouseEvents)
        palette = self.host.palette()
        palette.setColor(QPalette.Window, QColor(theme.VIDEO_BG))
        self.host.setPalette(palette)
        self.host.setAutoFillBackground(True)
        self.host.hide()
        hwnd = int(self.host.winId())
        # mpv creates another child below this HWND. Let Windows route input
        # past both, to the Qt video widget (also while Crop is off).
        enable_window = ctypes.windll.user32.EnableWindow
        enable_window.argtypes = [wintypes.HWND, wintypes.BOOL]
        enable_window.restype = wintypes.BOOL
        enable_window(hwnd, False)
        self.composition = None
        if os.environ.get("QTC_D3D11_WINDOW") != "1":
            try:
                self.composition = CompositionTarget(hwnd)
            except OSError as exc:
                startup_log.mark(f"DirectComposition unavailable: {exc}")
        self.output_options = dict(vo="gpu-next", gpu_context="d3d11")
        self._output_size = self._physical_size()
        if self.composition is not None:
            # A composition visual keeps the old buffer at its native size
            # while mpv draws a new one. A window swap chain can stretch it.
            self.output_options.update(d3d11_output_mode="composition",
                                       d3d11_composition_size=self._output_size)
        else:
            self.output_options["wid"] = str(hwnd)
        self.layer = CropLayer(self)
        self.exposed = QRegion()
        self.controls = []
        self.owner = self.window()
        self.owner.installEventFilter(self)
        self._ready = False
        self._closing = False
        self._first_frame_ready = False
        self._replacement_token = None
        self._replacement_capture = None
        self._replacement_image = None
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.timeout.connect(self._release_replacement_image)
        self._replacement_captured.connect(self._finish_capture, Qt.QueuedConnection)
        self._replacement_ready.connect(self._finish_replacement, Qt.QueuedConnection)
        self._composition_needs_attach = self.composition is not None
        self._missing_swapchain_logged = False
        self._seek_done.connect(self._show_rendered_surface, Qt.QueuedConnection)

    def _show_rendered_surface(self):
        if self._closing:
            return
        if self._composition_needs_attach:
            self._composition_needs_attach = False
            swapchain = getattr(self.player, "display_swapchain", None)
            if not swapchain:
                self._composition_needs_attach = True
                if not self._missing_swapchain_logged:
                    import logging

                    self._missing_swapchain_logged = True
                    message = "DirectComposition: mpv did not expose a display swap chain"
                    startup_log.mark(message)
                    logging.getLogger(__name__).warning(message)
                return
            self.composition.set_swapchain(swapchain)
        if not self._first_frame_ready:
            self._first_frame_ready = True
            self.sync_surface()

    def prepare_file_replacement(self, open_file):
        """Deliver a snapshot of the outgoing viewport without waiting on mpv in Qt."""
        self._replacement_capture = None
        if self._replacement_image is not None:
            open_file(self._replacement_image)
            return
        if not (self._first_frame_ready and self.has_video and self._scale is not None
                and self.isVisible() and not self.owner.isMinimized()):
            open_file(None)
            return

        request = object()
        self._replacement_capture = (request, open_file)

        def captured(error, data):  # mpv event thread
            self._replacement_captured.emit(request, None if error else data)

        try:
            self.player.command_async("screenshot-raw", "window", callback=captured)
        except Exception:
            self._finish_capture(request, None)
        else:
            # Loading can still proceed if a driver stalls or mpv never replies.
            QTimer.singleShot(120, self, lambda: self._finish_capture(request, None))

    def _finish_capture(self, request, data):
        pending = self._replacement_capture
        if self._closing or pending is None or request is not pending[0]:
            return
        _, open_file = pending
        self._replacement_capture = None
        image = None
        if isinstance(data, dict) and data.get("format") == "bgr0":
            try:
                # bgr0 has an unused high byte. Copy only the color channels so
                # the translucent crop layer composites the snapshot as opaque.
                raw = QImage(data["data"], data["w"], data["h"], data["stride"],
                             QImage.Format_RGB32)
                image = raw.convertToFormat(QImage.Format_RGB888)
            except (KeyError, TypeError, ValueError):
                pass
            if image is not None and image.isNull():
                image = None
        open_file(image)

    def begin_replacement(self, image):
        self._reveal_timer.stop()
        self._replacement_token = object()
        self._replacement_image = image
        self.sync_surface()
        self.repaint()
        if image is not None:
            self.layer.repaint()
        theme.dwm_flush()
        return self._replacement_token

    def _finish_replacement(self, token):
        if self._closing or token is not self._replacement_token:
            return
        self._replacement_token = None
        self.sync_surface()
        if self._replacement_image is not None:
            # Keep the snapshot above the HWND while DWM maps the new video.
            # Allow two refreshes for the HWND and the swap chain to become visible.
            refresh_rate = self.screen().refreshRate() or 60.0
            self._reveal_timer.start(round(2000 / refresh_rate))

    def _release_replacement_image(self):
        self._replacement_image = None
        self.sync_surface()
        self.update()

    def _paint_replacement(self, painter):
        painter.fillRect(self.rect(), QColor(theme.VIDEO_BG))
        size = self._replacement_image.size().scaled(self.size(), Qt.KeepAspectRatio)
        target = QRect(QPoint(), size)
        target.moveCenter(self.rect().center())
        painter.drawImage(target, self._replacement_image)

    def _physical_size(self):
        dpr = self.devicePixelRatioF()
        width = max(1, round(self.width() * dpr))
        height = max(1, round(self.height() * dpr))
        return f"{width}x{height}"

    def close_composition(self):
        if self.composition is not None:
            try:
                self.composition.close()
            except OSError as exc:
                startup_log.mark(f"DirectComposition teardown: {exc}")
            finally:
                self.composition = None
                self._composition_needs_attach = False

    def watch_control(self, control):
        self.controls.append(control)
        control.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Move, QEvent.Resize, QEvent.Show, QEvent.Hide,
                            QEvent.WindowStateChange, QEvent.DevicePixelRatioChange):
            self.sync_surface()
        return super().eventFilter(obj, event)

    def sync_surface(self):
        if self._closing or not self.isVisible() or self.owner.isMinimized():
            self.layer.hide()
            return
        region = QRegion(self.rect())
        origin = self.mapToGlobal(QPoint())
        for control in self.controls:
            if control.isVisible():
                corner = control.mapToGlobal(QPoint()) - origin
                region -= QRegion(QRect(corner, control.size()))
        # Keep the native child mapped at its real size during preroll. A
        # hidden QWidget can leave mpv rendering into its initial 100x30 HWND.
        preroll = not self._first_frame_ready
        geometry = self.rect()
        if preroll:
            geometry.moveLeft(-geometry.width())
        if self.composition is not None and hasattr(self, "player"):
            size = self._physical_size()
            if size != self._output_size:
                self._output_size = size
                self._command("set", "d3d11-composition-size", size)
        self.host.setGeometry(geometry)
        self.exposed = region
        # The same exposed rectangle can mean either a full surface or a
        # clipped one when the video grows underneath the overlay controls.
        mask = QRegion() if region == QRegion(self.rect()) else region
        if mask != self.host.mask():
            if mask.isEmpty():
                self.host.clearMask()
            else:
                self.host.setMask(mask)
        video = (not preroll and self._replacement_token is None
                 and getattr(self, "has_video", False) and not region.isEmpty())
        self.host.setVisible(preroll or video)
        frozen = self._replacement_image is not None and not region.isEmpty()
        if frozen or (video and (self.crop_enabled or self._draft is not None)):
            geometry = QRect(origin, self.size())
            if self.layer.geometry() != geometry:
                self.layer.setGeometry(geometry)
            self.layer.show()
            self.layer.update()
        else:
            self.layer.hide()

    def moveEvent(self, event):
        super().moveEvent(event)
        self.sync_surface()

    def hideEvent(self, event):
        self.layer.hide()
        super().hideEvent(event)

    def paintEvent(self, event):
        self.sync_surface()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.VIDEO_BG))
        if self._replacement_image is not None:
            self._paint_replacement(painter)
        elif not self.has_video:
            painter.setPen(QColor(theme.DIM))
            painter.drawText(self.rect(), Qt.AlignCenter, self.empty_text)
        painter.end()
        if not self._ready:
            self._ready = True
            self.surface_ready.emit()

    def update(self, *args):
        super().update(*args)
        # A native child can cover the entire Qt backing store. Crop changes
        # must still reach its independent translucent window while paused.
        if hasattr(self, "layer"):
            self.sync_surface()
