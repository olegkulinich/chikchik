"""Windows presentation. mpv owns the video pixels, Qt owns the crop and input."""
import ctypes
import os
from ctypes import wintypes

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QRegion
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
        self.view._paint_crop(painter)
        painter.end()


class D3D11Surface(QWidget):
    surface_ready = Signal()

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
        self._composition_needs_attach = self.composition is not None
        self._seek_done.connect(self._show_rendered_surface, Qt.QueuedConnection)

    def _show_rendered_surface(self):
        if self._closing:
            return
        if self._composition_needs_attach:
            self._composition_needs_attach = False
            swapchain = getattr(self.player, "display_swapchain", None)
            if swapchain:
                self.composition.set_swapchain(swapchain)
            else:
                self._composition_needs_attach = True
        if not self._first_frame_ready:
            self._first_frame_ready = True
            self.sync_surface()

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
        # The parent clips this child until the first frame is ready.
        geometry = self.rect()
        if not self._first_frame_ready:
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
        video = (self._first_frame_ready and getattr(self, "has_video", False)
                 and not region.isEmpty())
        self.host.setVisible(not self._first_frame_ready or video)
        if video and (self.crop_enabled or self._draft is not None):
            self.layer.setGeometry(QRect(origin, self.size()))
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
        if not self.has_video:
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
