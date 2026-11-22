"""Windows presentation. mpv owns the video pixels, Qt owns the crop and input."""
import ctypes
from ctypes import wintypes

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QRegion
from PySide6.QtWidgets import QWidget

import theme


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
        self.host.setAttribute(Qt.WA_NoSystemBackground)
        self.host.hide()
        hwnd = int(self.host.winId())
        # mpv creates another child below this HWND. Let Windows route input
        # past both, to the Qt video widget (also while Crop is off).
        enable_window = ctypes.windll.user32.EnableWindow
        enable_window.argtypes = [wintypes.HWND, wintypes.BOOL]
        enable_window.restype = wintypes.BOOL
        enable_window(hwnd, False)
        self.output_options = dict(vo="gpu-next", gpu_context="d3d11", wid=str(hwnd))
        self.layer = CropLayer(self)
        self.exposed = QRegion()
        self.controls = []
        self.owner = self.window()
        self.owner.installEventFilter(self)
        self._ready = False
        self._closing = False

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
        self.host.setGeometry(self.rect())
        if region != self.exposed:
            self.exposed = region
            if region == QRegion(self.rect()):
                self.host.clearMask()
            else:
                self.host.setMask(region)
        video = getattr(self, "has_video", False) and not region.isEmpty()
        self.host.setVisible(video)
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
