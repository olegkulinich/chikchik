from __future__ import annotations

import math
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import (QEasingCurve, QEvent, QMimeData, QPoint, QPointF, QProcess,
                            QPropertyAnimation, QRect, Qt, QTimer, QUrl)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QCursor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QImage,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

import perf_log
from version import APP_NAME
import theme
import win_frame
from backend import (
    find_sequence,
    format_ranges,
    is_image,
    mpv_list,
    probe_sequence,
    sequence_range,
    with_fps,
    PRECISE_CONTAINERS,
    SCALES,
    AudioTrack,
    CropRect,
    ExportJob,
    ExportWorker,
    MediaInfo,
    SubtitleTrack,
    build_ffmpeg_command,
    build_frame_command,
    build_gif_commands,
    build_png_command,
    commit_output,
    format_fps,
    clamp_crop,
    normalize_crop,
    palette_path,
    png_pattern,
    probe_media,
    same_path,
    scaled_size,
    temporary_output,
    unique_path,
    validate_video,
)
from config import FORMAT_HOTKEYS, MENU_ALIASES, MENU_SHORTCUTS, SCALE_HOTKEYS, Config, display_key
from popovers import HotkeysPopover, SettingsPopover
from video_view import VideoView
from widgets import (
    FieldGroup,
    _FieldEdit,
    Field,
    PlayButton,
    ProgressButton,
    Timeline,
    Toast,
    button,
    dim_label,
)

VIDEO_FILTER = ("Video or frame (*.mp4 *.mkv *.mov *.m4v *.avi *.webm *.mts *.ts *.png *.jpg *.jpeg)"
                ";;All files (*)")


def format_time(value: float) -> str:
    value = max(0.0, value)
    hours = int(value // 3600)
    minutes = int(value % 3600 // 60)
    secs = value % 60
    if hours:
        return f"{hours}:{minutes:02d}:{secs:06.3f}"
    return f"{minutes:02d}:{secs:06.3f}"


def format_scale(value: float) -> str:
    return f"{round(value, 2):g}x"


FORMATS = ("mp4", "gif", "png", "copy")   # T25: same order everywhere; copy = Original
# 1b: exact seeks while dragging once a pixel of the timeline covers this many frames or
# fewer (was "≤ 60 s visible": too late in a wide window, too early in a narrow one).
# Intra-only sources (sequences, ProRes…) always seek exactly.
EXACT_SCRUB_FRAMES_PER_PX = 4.0


def parse_time(text: str) -> float | None:
    text = text.strip().replace(",", ".")
    if not text:
        return None
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        return None
    if len(parts) > 3 or any(p < 0 for p in parts):
        return None
    value = 0.0
    for part in parts:
        value = value * 60 + part
    return value


def _short_track(track) -> str:
    """T40a: the current track in a few characters — "1 · rus", "None"."""
    if track is None:
        return "None"
    lang = track.language if track.language and track.language != "und" else track.codec
    return f"{track.number} · {lang}"


class MenuRow(QWidget):
    """Menu item with a dim shortcut on the right (QMenu can't color it)."""

    def __init__(self, menu: QMenu, text: str, hint: str = "", enabled: bool = True,
                 checked: bool = False, callback=None, check_column: bool = False,
                 cells: list[tuple[str, int, str, int]] | None = None,
                 sub: str = "", submenu: bool = False, root: QMenu | None = None):
        """`cells` replaces the right side: (text, fixed width, color or "", gap before).
        `sub` — dim text right after the label (Open Recent: the folder).
        `submenu` — the row opens a submenu (▸, a click doesn't close the menu).
        `root` — for rows inside a submenu: the top menu, closed too on click."""
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setMouseTracking(True)
        self.menu, self.callback, self.enabled = menu, callback, enabled
        self.submenu, self.root = submenu, root
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 5, 16, 5)
        layout.setSpacing(24)
        label = QLabel(text)
        if not enabled:
            label.setProperty("dim", True)
        if sub:                                  # 8 px after the name, not the 24 px column gap
            left = QHBoxLayout()
            left.setSpacing(8)
            left.addWidget(label)
            left.addWidget(dim_label(sub))
            layout.addLayout(left)
        else:
            layout.addWidget(label)
        layout.addStretch(1)
        if submenu:
            right = QHBoxLayout()
            right.setSpacing(8)
            if hint:
                right.addWidget(dim_label(hint))     # the current choice (T40a)
            arrow = QLabel("▸")
            if not enabled:
                arrow.setProperty("dim", True)
            right.addWidget(arrow)
            layout.addLayout(right)
            return
        if cells is not None:
            right = QHBoxLayout()
            right.setSpacing(0)
            for cell_text, width, color, gap in cells:
                right.addSpacing(gap)
                cell = QLabel(cell_text)
                cell.setFixedWidth(width)
                cell.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if color:
                    cell.setStyleSheet(f"color: {color};")
                right.addWidget(cell)
            layout.addLayout(right)
            return
        right = QHBoxLayout()
        right.setSpacing(8)              # hint and ✓ sit closer than text and hint
        if hint and (check_column or not checked):
            right.addWidget(dim_label(hint))
        if checked or check_column:
            check = QLabel("✓" if checked else "")
            if check_column:
                check.setFixedWidth(10)
            right.addWidget(check)
        layout.addLayout(right)

    # Highlight = the menu's "hot" row, set by a cursor poll (_MenuHover): Qt's hover,
    # leave and activeAction go stale once submenus open and grab the mouse (#12).
    def paintEvent(self, event):
        if self.enabled and getattr(self.menu, "_hot_row", None) is self:
            from PySide6.QtGui import QColor, QPainter
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(theme.BUTTON_HOVER))
            p.end()

    def mouseReleaseEvent(self, event):
        if self.submenu:
            return                               # Qt opens the submenu on hover
        if self.enabled and event.button() == Qt.MouseButton.LeftButton:
            self.menu.close()
            if self.root is not None:
                self.root.close()
            if self.callback:
                QTimer.singleShot(0, self.callback)


class _MenuHover:
    """Exactly one lit row per menu: the enabled row under the cursor; with the cursor
    elsewhere — the row whose submenu is open; otherwise none. Polled while shown."""

    def __init__(self, menu: QMenu):
        self.menu = menu
        self.timer = QTimer(menu)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self.tick)
        menu.aboutToShow.connect(self._start)
        menu.aboutToHide.connect(self._stop)
        menu._hot_row = None

    def _start(self):
        self.timer.start()
        QTimer.singleShot(0, self.tick)

    def _stop(self):
        self.timer.stop()
        self._set(None)

    def tick(self):
        pos = QCursor.pos()
        hot = None
        open_owner = None
        for row in self.menu.findChildren(MenuRow):
            if not row.isVisible():
                continue
            if row.enabled and row.rect().contains(row.mapFromGlobal(pos)):
                hot = row
            sub = getattr(row, "sub_menu", None)
            if sub is not None and sub.isVisible():
                open_owner = row
        self._set(hot or open_owner)

    def _set(self, row):
        old = getattr(self.menu, "_hot_row", None)
        if old is row:
            return
        self.menu._hot_row = row
        for widget in (old, row):
            if widget is not None:
                widget.update()


class _Reveal:
    """A bar shown on hover (22a): appears at once, fades out in 100 ms when the cursor
    leaves; `HYSTERESIS` px of slack on the video side once shown."""

    HYSTERESIS = 24

    def __init__(self, owner: QWidget, widget: QWidget, name: str, side: str, shown: bool):
        self.owner, self.widget, self.name, self.side = owner, widget, name, side
        self.shown = shown
        self.active = False
        self._fade: QPropertyAnimation | None = None

    def zone(self) -> QRect:
        rect = self.widget.geometry()
        if self.shown:
            rect = rect.adjusted(0, -self.HYSTERESIS, 0, 0) if self.side == "bottom" \
                else rect.adjusted(0, 0, 0, self.HYSTERESIS)
        return rect

    def set_shown(self, shown: bool, animate: bool) -> None:
        self.shown = shown
        self._stop()
        if shown:
            self.widget.show()                           # appears without animation
            return
        if not animate:
            self.widget.hide()
            return
        effect = QGraphicsOpacityEffect(self.widget)
        self.widget.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", self.owner)
        fade.setDuration(100)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.OutQuad)
        fade.finished.connect(self._done)
        self._fade = fade
        fade.start()

    def _done(self) -> None:
        self._fade = None
        self.widget.setGraphicsEffect(None)
        if not self.shown:
            self.widget.hide()

    def _stop(self) -> None:
        if self._fade is not None:
            fade, self._fade = self._fade, None
            fade.stop()
            self.widget.setGraphicsEffect(None)

    @property
    def fading(self) -> bool:
        return self._fade is not None


class _TitleStrip(QWidget):
    """T27 Hide UI: the title bar, shown on hover over the top edge. On Windows the
    strip is the native caption (WM_NCHITTEST) — snap, double-click maximize."""

    HEIGHT = 32
    BUTTON_W = 46

    def __init__(self, window: QMainWindow, parent: QWidget):
        super().__init__(parent)
        self.window_ = window
        self.setObjectName("TitleStrip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(theme.app_icon().pixmap(16, 16))
        layout.addWidget(icon)
        self.title = dim_label(window.windowTitle())
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.title, 1)
        window.windowTitleChanged.connect(self.title.setText)
        families = QFontDatabase.families()
        self._glyph_font = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets")
                                 if f in families), "")
        self.buttons = []
        for name, glyph, fallback, slot in (
                ("TitleButton", "\uE921", "—", window.showMinimized),
                ("TitleButton", "\uE922", "□", self._toggle_maximized),
                ("TitleClose", "\uE8BB", "✕", window.close)):
            b = QPushButton(glyph if self._glyph_font else fallback)
            b.setObjectName(name)
            b.setFixedSize(self.BUTTON_W, self.HEIGHT)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if self._glyph_font:
                font = QFont(self._glyph_font)
                font.setPixelSize(10)
                b.setFont(font)
            b.clicked.connect(slot)
            layout.addWidget(b)
            self.buttons.append(b)
        self.hide()

    def _toggle_maximized(self) -> None:
        w = self.window_
        w.showNormal() if w.isMaximized() else w.showMaximized()

    def sync(self) -> None:
        """Maximize ↔ restore glyph."""
        maximized = self.window_.isMaximized()
        if self._glyph_font:
            self.buttons[1].setText("\uE923" if maximized else "\uE922")
        else:
            self.buttons[1].setText("❐" if maximized else "□")

    def over_button(self, local: QPoint) -> bool:
        return any(b.geometry().contains(local) for b in self.buttons)

    # Elsewhere than Windows the strip isn't a native caption — move/maximize by hand.
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.window_.windowHandle():
            self.window_.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximized()


class _Central(QWidget):
    """Presses in the margin around the video start a crop frame too (1.9b) —
    matters when the picture fills the whole width and there are no black bars."""

    BAND = 8

    def __init__(self):
        super().__init__()
        self.video: VideoView | None = None
        self._forward = False

    def _mapped(self, event) -> QPointF:
        return QPointF(self.video.mapFrom(self, event.position().toPoint()))

    def mousePressEvent(self, event) -> None:
        video = self.video
        if video is not None and video.has_video and event.button() == Qt.MouseButton.LeftButton:
            area = video.geometry().adjusted(-self.BAND, -self.BAND, self.BAND, self.BAND)
            if area.contains(event.position().toPoint()):
                self._forward = True
                video.press_at(self._mapped(event))
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._forward:
            self.video.move_to(self._mapped(event))
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._forward and event.button() == Qt.MouseButton.LeftButton:
            self._forward = False
            self.video.release_at(self._mapped(event))
            return
        super().mouseReleaseEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = Config(self)
        self.source: Path | None = None
        self.info: MediaInfo | None = None
        self.audio: AudioTrack | None = None
        self.subtitle: SubtitleTrack | None = None
        self.scale = 1.0
        self.fmt = "mp4"                    # mp4 | gif | png | copy (Original)
        self._frame_process: QProcess | None = None
        self.fps_out: float | None = None   # None = source rate
        self.trim_in = 0.0
        self.trim_out = 0.0
        self.crop = QRect()
        self.worker: ExportWorker | None = None
        self.job: ExportJob | None = None
        self._shortcuts: list[QShortcut] = []
        self._last_dims = None
        self._nudge_target = 0.0
        self._nudge_stamp = 0.0
        self._scrub_active = False
        self._hold_target = 0.0
        self._hold_until = 0.0
        self._edge_return: float | None = None
        self._quiet = False                 # programmatic Trim/Crop toggles: no capture/reset
        self._applying = False              # applying an undo step: no history writes

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(theme.app_icon())
        self.setAcceptDrops(True)
        self.setMinimumHeight(420)  # min width comes from the bottom row (~960 px)
        # T22 fullscreen: panels float over the video and show on hover
        self._mpv_list: Path | None = None   # T36: mf:// frame list of the open sequence
        self._ui_ready = False
        self._fullscreen = False
        self._window_geometry = None        # saveGeometry() from before fullscreen
        self._was_maximized = False
        self._overlay = False
        self._ui_mode = self.config.ui_mode()      # T27: normal | minimal | hidden
        self._frame_hidden = False
        self._press_origin = ""             # "video" | "panel" while a button is held
        self._cursor_hidden = False
        self._cursor_pos = QPoint()
        self._cursor_still_since = time.monotonic()
        self._rmb_press: QPoint | None = None
        self._rmb_dragging = False
        self._drag_offset = QPoint()

        self._build_ui()
        # T22, Windows: an OpenGL window covering the whole monitor gets a DWM mode in
        # which menus and dialogs open *behind* it. Qt's own fix — a 1 px border in
        # fullscreen — is read from this property when the native window is created.
        # The QWindow is replaced once the OpenGL video is embedded, so tag it after
        # _build_ui (WA_NativeWindow makes the QWindow without creating the native one).
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        if self.windowHandle() is not None:
            self.windowHandle().setProperty("_q_has_border_in_fullscreen", True)
        self.settings_popover = SettingsPopover(self.config, self)
        self.hotkeys_popover = HotkeysPopover(self.config, self)
        self._install_menu_actions()
        self._install_hotkeys()
        self.config.hotkeys_changed.connect(self._install_hotkeys)
        self.config.changed.connect(self._config_changed)                 # T42
        self._config_changed()

        # T1: a press anywhere outside the field being edited applies it and leaves it.
        QApplication.instance().installEventFilter(self)

        self.poll = QTimer(self)
        self.poll.setInterval(30)
        self.poll.timeout.connect(self._poll)
        self.poll.start()

        # T18: while the playhead is dragged by keyframes, a short pause → exact frame.
        self._exact_timer = QTimer(self)
        self._exact_timer.setSingleShot(True)
        self._exact_timer.setInterval(150)
        self._exact_timer.timeout.connect(self._scrub_rest)
        self._scrub_t: float | None = None
        self._kf_drag = False               # this drag used keyframe seeks

        # T32: undo/redo of trim & crop
        self._history: list[tuple] = []
        self._hpos = 0
        self._history_timer = QTimer(self)
        self._history_timer.setSingleShot(True)
        self._history_timer.setInterval(500)
        self._history_timer.timeout.connect(self._history_commit)

        self._update_enabled()
        self._ui_ready = True
        self.resize(1100, 720)
        if self.config.geometry() is not None:
            self.restoreGeometry(self.config.geometry())
            if self.windowState() & Qt.WindowState.WindowFullScreen:   # never start fullscreen
                self.setWindowState(self.windowState() & ~Qt.WindowState.WindowFullScreen)
        theme.dark_title_bar(self)
        self._apply_layout()                     # T27: the remembered UI mode

    # ================================================================ UI
    def _build_ui(self) -> None:
        central = _Central()
        root = QVBoxLayout(central)
        root.setContentsMargins(theme.GAP, 0, theme.GAP, theme.GAP2)   # T24: no gap above the video
        root.setSpacing(0)

        self.video = VideoView()
        central.video = self.video
        self.video.crop_drawn.connect(self._crop_drawn)
        self.video.menu_requested.connect(self.show_menu)
        root.addWidget(self.video, 1)
        self._video_gap = QSpacerItem(0, theme.GAP)
        root.addItem(self._video_gap)
        self._root = root
        self._central = central

        # T22: both rows live in one widget — under the video in the window, floating
        # over it (shown on hover) in fullscreen.
        self.panels = QWidget(central)
        self.panels.setObjectName("Panels")
        self.panels.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        panels_layout = QVBoxLayout(self.panels)
        panels_layout.setContentsMargins(0, 0, 0, 0)
        panels_layout.setSpacing(theme.GAP2)
        self._panels_layout = panels_layout

        self.transport_bar = QWidget()
        transport = QHBoxLayout(self.transport_bar)
        transport.setContentsMargins(0, 0, 0, 0)
        transport.setSpacing(theme.HGAP)
        self.play_button = PlayButton()
        self.play_button.clicked.connect(lambda: (self.toggle_play(), self._sync_play()))
        self.timeline = Timeline()
        self.timeline.seek_requested.connect(self._timeline_seek)
        self.timeline.edge_preview.connect(self._edge_preview)
        self.timeline.in_dragged.connect(lambda t: self.set_in(t, step=False))
        self.timeline.out_dragged.connect(lambda t: self.set_out(t, step=False))
        self.time_field = Field("⌛", 110)   # emoji, like the app icon
        self.time_field.setToolTip("Current time")
        self.time_field.setText("00:00.000")
        self.time_field.committed.connect(self._time_field_committed)
        self.time_field.nudged.connect(self._nudge_position)
        self.time_field.scrub_started.connect(self.video.pause)
        self.time_field.ctrl_click = True
        self.time_field.ctrl_clicked.connect(self.toggle_frames_mode)          # T39
        transport.addWidget(self.play_button)
        self.loop_button = button("∞", checkable=True, tooltip="Loop (L)")    # T21
        self.loop_button.setFixedWidth(32)
        self.loop_button.toggled.connect(lambda _on: self._update_loop())
        transport.addWidget(self.loop_button)
        transport.addWidget(self.timeline, 1)
        transport.addWidget(self.time_field)
        panels_layout.addWidget(self.transport_bar)

        self.bottom_bar = QWidget()
        bottom = QHBoxLayout(self.bottom_bar)
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(theme.HGAP)
        self.trim_button = button("Trim", checkable=True,
                                  tooltip="Export only the In–Out range (off: the whole file)")
        self.trim_button.toggled.connect(self._trim_toggled)
        bottom.addWidget(self.trim_button)
        self.in_field = Field("In", 114)
        self.out_field = Field("Out", 114)
        self.in_field.committed.connect(lambda text: self._time_committed(self.in_field, text, True))
        self.out_field.committed.connect(lambda text: self._time_committed(self.out_field, text, False))
        self.in_field.nudged.connect(lambda n, c: self._nudge_trim(True, n, c))
        self.out_field.nudged.connect(lambda n, c: self._nudge_trim(False, n, c))
        for field in (self.in_field, self.out_field):
            field.scrub_started.connect(self._begin_edge_preview)
            field.scrub_finished.connect(self._end_edge_preview)
        self.d_field = Field("D", 114)                                         # T39
        self.d_field.setToolTip("Duration — what will be exported")
        self.d_field.committed.connect(self._duration_committed)
        self.d_field.nudged.connect(self._nudge_duration)
        self.d_field.scrub_started.connect(self._begin_edge_preview)
        self.d_field.scrub_finished.connect(self._end_edge_preview)
        bottom.addWidget(FieldGroup([self.in_field, self.out_field, self.d_field]))
        bottom.addSpacing(theme.GAP2 - theme.HGAP)   # T34b: 16 px between Trim and Crop

        self.crop_button = button("Crop", checkable=True,
                                  tooltip="Drag on the video to draw a frame")
        self.crop_button.toggled.connect(self._crop_toggled)
        bottom.addWidget(self.crop_button)
        self.crop_fields = {}
        for key in ("X", "Y", "W", "H"):
            field = Field(key, 66)
            field.committed.connect(lambda text, k=key: self._crop_field_committed(k, text))
            field.nudged.connect(lambda n, c, k=key: self._nudge_crop(k, n, c))
            self.crop_fields[key] = field
        bottom.addWidget(FieldGroup(list(self.crop_fields.values())))
        bottom.addStretch(1)
        bottom.addSpacing(theme.GAP2 - theme.HGAP)   # + the row spacing = Crop ↔ export never closer than 16 px

        self.format_button = button(".mp4", tooltip="Output format")
        self.format_button.clicked.connect(self._format_clicked)
        bottom.addWidget(self.format_button)

        self.fps_field = Field("fps", 86)
        self.fps_field.setToolTip("Output frame rate (can't exceed the source)")
        self.fps_field.committed.connect(self._fps_committed)
        self.fps_field.nudged.connect(self._nudge_fps)
        bottom.addWidget(self.fps_field)

        self.scale_button = button(format_scale(self.scale),
                                   tooltip="Output scale (applied after crop)")
        self.scale_button.clicked.connect(self._scale_clicked)
        bottom.addWidget(self.scale_button)

        self.export_button = ProgressButton("Export")
        self.export_button.setMinimumWidth(64)
        self.export_button.clicked.connect(self._export_clicked)
        bottom.addWidget(self.export_button)
        panels_layout.addWidget(self.bottom_bar)
        root.addWidget(self.panels)

        for field in (self.time_field, self.in_field, self.out_field, self.d_field, self.fps_field,
                      *self.crop_fields.values()):
            field.left.connect(lambda: self.video.setFocus())

        self.setCentralWidget(central)
        central.toast_anchor = lambda: self.video.geometry()
        self.toast = Toast(central)
        self.toast.action_clicked.connect(self._show_in_folder)
        self._toast_path: Path | None = None

        # T22/T27: bars shown on hover
        self.title_strip = _TitleStrip(self, central)
        self._panels_reveal = _Reveal(self, self.panels, "panel", "bottom", shown=True)
        self._title_reveal = _Reveal(self, self.title_strip, "title", "top", shown=False)

        # T15: ▶ as wide as Trim → equal gaps ▶→timeline and Trim→In/Out.
        self.play_button.setFixedWidth(self.trim_button.sizeHint().width())

        self._show_times()
        self._show_crop()
        self._show_fps()

    def _install_menu_actions(self) -> None:
        handlers = {
            "hotkeys": lambda: self.hotkeys_popover.popup_over(self.video),
            "settings": self._show_settings,
            "open": self.open_dialog,
            "export": lambda: self.export(ask=False),       # T42: like Save
            "export_to": lambda: self.export(ask=True),     # T42: like Save As
            "copy_frame": self.copy_frame,
            "open_recent": lambda: self.open_recent(),
            "fit": self.zoom_fit,
            "minimize_ui": self.toggle_minimize_ui,
            "hide_ui": self.toggle_hide_ui,
            "actual_size": self.zoom_actual,
            "quit": self.close,
        }
        for key, (_title, shortcut) in MENU_SHORTCUTS.items():
            action = QAction(self)
            # Shift+\\ arrives as | on some layouts — accept both spellings
            keys = [QKeySequence(shortcut)] + [QKeySequence(k) for k in MENU_ALIASES.get(key, ())]
            action.setShortcuts(keys)
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            action.triggered.connect(handlers[key])
            self.addAction(action)
        self._menu_handlers = handlers

    def eventFilter(self, obj, event):  # noqa: N802
        """T1: pressing anywhere outside the field being edited applies it (as Enter).
        T22b/c: remember where a press started — panels stay or stay away meanwhile."""
        kind = event.type()
        if kind == QEvent.Type.Resize and obj is getattr(self, "_central", None):
            self._place_overlay()
        in_window = isinstance(obj, QWidget) and obj.window() is self
        if in_window and kind in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress) \
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier \
                and win_frame.is_backslash_key(event):
            # T27: by the physical key; ShortcutOverride is accepted so no other
            # shortcut (e.g. Ctrl+Shift+/ on a Russian layout) sees it
            if kind == QEvent.Type.KeyPress and not event.isAutoRepeat():
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.toggle_minimize_ui()
                else:
                    self.toggle_hide_ui()
            event.accept()
            return True
        if in_window and kind in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress) \
                and self._is_export_key(event):
            # T42: Ctrl+E / Ctrl+Shift+E handled here, once — on Windows the pair was
            # seen to start both exports. ShortcutOverride accepted → no QAction fires.
            if kind == QEvent.Type.KeyPress and not event.isAutoRepeat():
                self.export(ask=bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            event.accept()
            return True
        if in_window and kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove,
                                  QEvent.Type.MouseButtonRelease) and self._rmb_event(obj, event):
            return True
        if in_window and kind == QEvent.Type.ContextMenu \
                and event.reason() == event.Reason.Mouse:
            return True                    # the menu opens on release, in _rmb_event
        if kind == QEvent.Type.MouseButtonPress and isinstance(obj, QWidget) \
                and obj.window() is self and not self._press_origin:
            on_panels = obj is self.panels or self.panels.isAncestorOf(obj)
            on_title = obj is self.title_strip or self.title_strip.isAncestorOf(obj)
            self._press_origin = "panel" if on_panels else "title" if on_title else "video"
        elif kind == QEvent.Type.MouseButtonRelease and not event.buttons():
            self._press_origin = ""
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(obj, QWidget):
            focused = QApplication.focusWidget()
            if isinstance(focused, _FieldEdit):
                field = focused.parentWidget()
                if obj is not field and not (field is not None and field.isAncestorOf(obj)):
                    focused.clearFocus()        # editingFinished → applied; focus → video
        return False

    def _install_hotkeys(self) -> None:
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self._shortcuts = []
        handlers = {
            "trim": lambda: self.trim_button.toggle() if self._editable() else None,
            "reset_trim": self.reset_trim,
            "crop": lambda: self.crop_button.toggle() if self._editable() else None,
            "reset_crop": self.reset_crop,
            "frame_next": lambda: self.video.frame_step(False),
            "frame_prev": lambda: self.video.frame_step(True),
            "sec_next": lambda: self.video.seek_relative(1.0),
            "sec_prev": lambda: self.video.seek_relative(-1.0),
            "sec5_next": lambda: self.video.seek_relative(5.0),
            "sec5_prev": lambda: self.video.seek_relative(-5.0),
            "play": self.toggle_play,
            "loop": lambda: self.loop_button.toggle() if self.info is not None else None,
            "fullscreen": self.toggle_fullscreen,                         # T22
            "go_start": lambda: self._go_to(start=True),
            "go_end": lambda: self._go_to(start=False),
            "undo": self.undo,
            "redo": self.redo,
            "trim_in": lambda: self.set_in(self._position()),
            "trim_out": lambda: self.set_out(self._position()),
        }
        for action_id, value in SCALE_HOTKEYS.items():
            handlers[action_id] = lambda v=value: self.set_scale(v) if self._editable() else None
        for action_id, fmt in FORMAT_HOTKEYS.items():                   # T37
            handlers[action_id] = lambda f=fmt: self.set_format(f, explain=True) \
                if self._editable() else None
        for action_id, key in self.config.hotkeys().items():
            if not key:
                continue
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(handlers[action_id])
            self._shortcuts.append(shortcut)

    # ============================================================== menu
    def _new_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        menu._hover = _MenuHover(menu)
        return menu

    @staticmethod
    def _menu_header(menu: QMenu, text: str) -> None:
        header = QWidgetAction(menu)
        label = dim_label(text)
        label.setContentsMargins(14, 4, 16, 2)
        header.setDefaultWidget(label)
        menu.addAction(header)

    @staticmethod
    def _menu_choice(menu: QMenu, text: str, checked: bool, callback, enabled: bool = True,
                     hint: str = "", check_column: bool = False, root: QMenu | None = None) -> None:
        action = QWidgetAction(menu)
        action.setDefaultWidget(MenuRow(menu, text, hint, enabled, checked=checked,
                                        callback=callback, check_column=check_column, root=root))
        menu.addAction(action)

    def show_menu(self, pos) -> None:
        menu = self._new_menu()
        loaded = self.info is not None and self.worker is None

        def row(key: str, enabled: bool = True, title: str = ""):
            default_title, shortcut = MENU_SHORTCUTS[key]
            title = title or default_title
            action = QWidgetAction(menu)
            action.setDefaultWidget(MenuRow(menu, title, display_key(shortcut), enabled,
                                            callback=self._menu_handlers[key]))
            menu.addAction(action)

        def submenu_row(title: str, hint: str, sub: QMenu, enabled: bool = True):
            action = QWidgetAction(menu)
            menu_row = MenuRow(menu, title, hint, enabled, submenu=True)
            action.setDefaultWidget(menu_row)
            action.setEnabled(enabled)
            if enabled:
                action.setMenu(sub)
                menu_row.sub_menu = sub
            menu.addAction(action)

        row("hotkeys")
        row("settings")
        menu.addSeparator()
        row("open", self.worker is None)
        submenu_row("Open Recent", "", self._recent_menu(menu),               # T40
                    enabled=self.worker is None and bool(self._recent()))
        row("copy_frame", self.info is not None)
        row("export", loaded)                                               # T42
        row("export_to", loaded)
        menu.addSeparator()                                                 # T29
        self._menu_header(menu, "View")
        row("fit", self.info is not None)
        row("actual_size", self.info is not None)
        # Toggles name what a click will do (Fullscreen ↔ Exit Fullscreen) — no checkmarks
        self._menu_choice(menu, "Exit Fullscreen" if self._fullscreen else "Fullscreen", False,
                          self.toggle_fullscreen, hint=display_key(self.config.hotkey("fullscreen")),
                          check_column=False)
        # T27: names say what a click does
        row("minimize_ui", title="Restore UI" if self._ui_mode == "minimal" else "Minimize UI")
        row("hide_ui", title="Show UI" if self._ui_mode == "hidden" else "Hide UI")
        if self.info is not None and (self.info.audio or self.info.subtitles):
            # T40a: tracks in submenus (films can carry 20 subtitle tracks); the row shows
            # the current choice. Each row only when the file has such tracks (no sound —
            # no Audio Track, as with Subtitles).
            idle = self.worker is None
            menu.addSeparator()
            if self.info.audio:
                audio = self._new_menu()
                tracks: list[AudioTrack | None] = [*self.info.audio, None]
                for track in tracks:
                    self._menu_choice(audio, track.label() if track else "None",
                                      track == self.audio, lambda t=track: self.set_audio(t),
                                      idle, root=menu)
                submenu_row("Audio Track", _short_track(self.audio), audio)
            if self.info.subtitles:
                subs_menu = self._new_menu()
                subs: list[SubtitleTrack | None] = [None, *self.info.subtitles]
                for sub in subs:
                    self._menu_choice(subs_menu, sub.label() if sub else "None",
                                      sub == self.subtitle, lambda s=sub: self.set_subtitle(s),
                                      idle, root=menu)
                submenu_row("Subtitles", _short_track(self.subtitle), subs_menu)
        menu.addSeparator()
        row("quit")
        menu.setMinimumWidth(200)
        menu.exec(pos)

    def contextMenuEvent(self, event) -> None:
        self.show_menu(event.globalPos())

    # ====================================================== recent (T40)
    RECENT_SHOWN = 5

    def _recent(self) -> list[Path]:
        """Recent files, newest first, without the open one."""
        current = self.source
        items = [Path(p) for p in self.config.recent()]
        return [p for p in items if current is None or not same_path(p, current)][:self.RECENT_SHOWN]

    def _recent_menu(self, root: QMenu) -> QMenu:
        sub = self._new_menu()
        metrics = QFontMetrics(self.font())
        for i, path in enumerate(self._recent()):
            folder = metrics.elidedText(str(path.parent), Qt.TextElideMode.ElideMiddle, 260)
            action = QWidgetAction(sub)
            action.setDefaultWidget(MenuRow(
                sub, path.name, display_key(MENU_SHORTCUTS["open_recent"][1]) if i == 0 else "",
                callback=lambda p=path: self.open_recent(p), sub=folder, root=root))
            sub.addAction(action)
        return sub

    def open_recent(self, path: Path | None = None) -> None:
        """Ctrl+Shift+O opens the top one — the previous file, so pressing it again
        switches between the last two."""
        if self.worker is not None:
            return
        if path is None:
            items = self._recent()
            if not items:
                return
            path = items[0]
        if not path.exists():
            self.config.remove_recent(str(path))
            self.toast.show_message(f"File not found: {path.name}", action=False, error=True,
                                    ms=4000)
            return
        self.open_path(path)

    # ============================================================= files
    def open_dialog(self) -> None:
        if self.worker is not None:
            return
        start = str(self.source.parent) if self.source else ""
        filename, _ = QFileDialog.getOpenFileName(self, "Open Video", start, VIDEO_FILTER)
        if filename:
            self.open_path(Path(filename))

    def open_path(self, path: Path) -> None:
        if self.worker is not None:
            return
        path = path.expanduser().resolve()
        try:
            if is_image(path):                                   # T36: a frame → sequence
                info = probe_sequence(find_sequence(path))
            else:
                info = probe_media(path)
        except Exception as exc:
            self._error("Could not open video", str(exc))
            return
        self._open_info(path, info)

    def _open_info(self, path: Path, info: MediaInfo, keep: dict | None = None) -> None:
        """Load a probed file or sequence. `keep` (T36 fps change) restores trim, crop and
        the frame instead of the clean state."""
        sequence = info.sequence
        if sequence is not None:
            path = sequence.first_file if keep is None else path

        self.source, self.info = path, info
        if sequence is not None:
            if self._mpv_list is not None:
                self._remove(self._mpv_list)
            self._mpv_list = mpv_list(sequence)
            url: Path | str = f"mf://@{self._mpv_list}"
        else:
            url = path
        self.audio = info.default_audio()
        self.subtitle = None
        self.trim_in, self.trim_out = 0.0, info.duration
        self._set_checked(self.trim_button, False)
        self.fps_out = None
        self.crop = QRect(0, 0, info.width, info.height)
        self._set_checked(self.crop_button, False)
        self.video.set_crop(self.crop)
        autoplay = self.config.flag("autoplay") if keep is None else keep["playing"]
        self.video.load(url, (info.width, info.height),
                        self.audio.number if self.audio else None, None,
                        autoplay=autoplay,                                 # T35
                        frame_duration=info.frame_duration,
                        sequence_fps=info.fps if sequence is not None else None)
        if keep is None:
            start_format = self.config.start_format()                      # T25
            self.fmt = start_format if start_format in FORMATS else "mp4"
            if self.fmt == "copy" and sequence is not None:
                self.fmt = "mp4"                     # T36: frames have no "original" to copy
        self.timeline.set_duration(info.duration, info.frame_duration)   # also resets zoom
        self.timeline.set_frame_offset(self._frame_offset())             # T47
        self.timeline.set_frames_mode(self.config.flag("frames_mode"))
        self._last_dims = None

        name = sequence.display_name if sequence is not None else path.name
        self.setWindowTitle(f"{APP_NAME} — {name}")   # T23: name only
        self._show_times()
        self._show_crop()
        self._show_fps()
        self._show_format()
        self._update_enabled()
        self._update_loop()
        self._history_reset()                                              # T32
        self.config.push_recent(str(path))                                 # T40
        self.video.setFocus()
        if keep is not None:
            self._restore_kept(keep)
        elif sequence is not None and sequence.missing():
            self.toast.show_message(f"Missing frames: {format_ranges(sequence.missing())}",
                                    action=False, ms=6000)

    def set_audio(self, track: AudioTrack | None) -> None:
        self.audio = track
        self.video.set_audio(track.number if track else None)

    def set_subtitle(self, track: SubtitleTrack | None) -> None:
        """Preview only; Copy → MKV exports just this track (None → no subtitles)."""
        self.subtitle = track
        self.video.set_subtitle(track.number if track else None)

    # ========================================================== playback
    def _position(self) -> float:
        position = self.video.time_pos
        return position if position is not None else self.timeline.position

    def toggle_play(self) -> None:
        self.video.toggle_pause()

    def _sync_play(self) -> None:
        self.play_button.setChecked(self.video.playing)

    def _poll(self) -> None:
        if perf_log.ENABLED:
            started = time.perf_counter()
            self._poll_inner()
            spent = (time.perf_counter() - started) * 1000
            if spent > 2:
                perf_log.log(f"poll   {spent:6.2f} ms")
            return
        self._poll_inner()

    def _poll_inner(self) -> None:
        self._update_overlay()
        self._sync_play()
        if self.info is None:
            return
        position = self.video.time_pos
        if position is None:
            return
        if self.timeline.is_dragging() or self.video.seek_busy():
            # The playhead follows the mouse / the requested target, not mpv: while a
            # seek runs, mpv still reports the old position (made ⌛ scrub jitter, 4.1.1).
            return
        if time.monotonic() < self._hold_until:
            # mpv still reports the pre-seek position for a moment — don't jump back.
            if abs(position - self._hold_target) > self._step(False) * 1.5:
                return
            self._hold_until = 0.0
        self.timeline.set_position(position)
        if not self.time_field.is_busy():
            self.time_field.setText(self._fmt(position))
        dims = self.video.display_size()
        if dims != self._last_dims:
            self._last_dims = dims
            self.video.update()

    # ========================================================== timeline
    def _timeline_seek(self, t: float, final: bool) -> None:
        """Playhead/time field follow every move; mpv seeks through its queue
        (one in flight, latest wins — never waits in the GUI thread); release → exact."""
        t = self._snap(t)
        self.timeline.show_position(t)
        self.time_field.setText(self._fmt(t))
        if final and perf_log.ENABLED:
            perf_log.log(f"final  frame {self._frame_no(t)}")
        if final:
            self._exact_timer.stop()
            if self._scrub_active:
                self._scrub_active = False
                self.video.set_scrub_mode(False)
            keyframes, self._kf_drag = self._kf_drag, False
            self._scrub_t = None
            if keyframes:
                # T18: the frame found by eye stays — playhead/⌛ follow the picture
                # (the poll syncs them once the last seek has landed), no exact jump.
                self._hold_until = 0.0
                self._follow_picture()
                return
            self.video.seek(t, True)
            self._hold_target, self._hold_until = t, time.monotonic() + 0.5
            self._nudge_target, self._nudge_stamp = t, time.monotonic()
            return
        exact = self._exact_scrub()
        if not self._scrub_active:
            self._scrub_active = True
            if not self.info.intra_only:     # lighter decoding only helps H.264/HEVC
                self.video.set_scrub_mode(True)
        moved = self._scrub_t is None or abs(t - self._scrub_t) >= self._step(False) / 2
        self._scrub_t = t
        if not moved:          # jitter under a frame: keep the rest timer running
            return
        if perf_log.ENABLED:
            perf_log.log(f"drag   frame {self._frame_no(t)} {'exact' if exact else 'kf'}")
        self.video.seek(t, exact)
        if not exact:
            self._kf_drag = True
            self._exact_timer.start()     # T18: at rest the playhead moves to the picture
        else:
            # an exact seek shows the playhead's frame: release seeks exactly (no T18 follow)
            self._kf_drag = False
            self._exact_timer.stop()

    def _exact_scrub(self) -> bool:
        """T9 / 1b: keyframe seeks are fastest across the whole file, but zoomed in the
        whole drag fits between two keyframes (every 2–10 s in films) and the picture
        wouldn't change — there, seek exactly (the queue keeps the UI smooth)."""
        if self.info.intra_only or self.timeline.fine:     # Alt+drag: always exact
            return True
        span = self.timeline.view_end - self.timeline.view_start
        frames_per_px = span / self._step(False) / max(1, self.timeline.width())
        return frames_per_px <= EXACT_SCRUB_FRAMES_PER_PX

    def _frame_no(self, t: float) -> int:
        return int(round(t / self._step(False)))

    def _follow_picture(self) -> None:
        """Playhead and ⌛ jump to the frame mpv shows (keyframe scrub)."""
        if self.video.seek_busy():
            QTimer.singleShot(30, self._follow_picture)
            return
        position = self.video.time_pos
        if position is None:
            return
        self.timeline.show_position(position)
        self.time_field.setText(self._fmt(position))
        self._nudge_target, self._nudge_stamp = position, time.monotonic()

    def _scrub_rest(self) -> None:
        if self._scrub_t is not None and self.timeline.is_dragging():
            self._follow_picture()

    # ============================================================== trim
    def _editable(self) -> bool:
        return self.info is not None and self.worker is None

    def _set_checked(self, button_, checked: bool) -> None:
        """Programmatic toggle: no capture/reset (T31), no history step."""
        self._quiet = True
        try:
            button_.setChecked(checked)
        finally:
            self._quiet = False

    def _visible_range(self) -> tuple[float, float]:
        """T31: what the timeline shows (zoomed → the visible window, else the whole file)."""
        tl = self.timeline
        return self._snap(tl.view_start), self._snap(tl.view_end)

    def _trim_toggled(self, checked: bool) -> None:
        if self.info is not None and not self._quiet:
            self._history_flush(trim_on=not checked)
            # T31: off → reset; on → take exactly what is visible (the view doesn't move)
            if checked:
                self.trim_in, self.trim_out = self._visible_range()
            else:
                self.trim_in, self.trim_out = 0.0, self.info.duration
            self._show_times()
            self._history_commit()
        self.timeline.set_trim_enabled(checked and self.info is not None)
        self._show_times()
        self._update_loop()

    def _enable_trim(self) -> None:
        """Turn Trim on before editing an edge: captures the visible window (T31)."""
        if not self.trim_button.isChecked():
            self._set_checked(self.trim_button, True)
            self.trim_in, self.trim_out = self._visible_range()
            self.timeline.set_trim_enabled(True)

    def reset_trim(self) -> None:
        if not self._editable():
            return
        self._history_flush()
        self.trim_in, self.trim_out = 0.0, self.info.duration
        self._show_times()
        self._history_commit()

    def set_in(self, value: float, step: bool = True) -> None:
        if not self._editable():
            return
        if step:
            self._history_flush()
        self._enable_trim()
        value = self._snap(value)
        if value >= self.trim_out:
            self.trim_out = self.info.duration
        self.trim_in = value
        self._show_times()
        if step:
            self._history_commit()

    def set_out(self, value: float, step: bool = True) -> None:
        if not self._editable():
            return
        if step:
            self._history_flush()
        self._enable_trim()
        value = self._snap(value)
        if value <= self.trim_in:
            self.trim_in = 0.0
        self.trim_out = value
        self._show_times()
        if step:
            self._history_commit()

    def _time_committed(self, field: Field, text: str, is_in: bool) -> None:
        if self.info is None:
            return
        self._enable_trim()
        value = self._parse(text)
        other_ok = value is not None and (
            value < self.trim_out if is_in else value > self.trim_in
        )
        if value is None or value > self.info.duration or not other_ok:
            field.set_error(True)
            return
        value = self._snap(value)
        field.set_error(False)
        if is_in:
            self.trim_in = value
        else:
            self.trim_out = value
        self._enable_trim()
        self._show_times()
        self._show_at(value)
        self._history_commit()

    def _snap(self, t: float) -> float:
        """Nearest frame start — the playhead, trim edges and seeks sit on frames, so
        the picture always matches the playhead (no shift after stopping)."""
        if self.info is None:
            return t
        fd = self._step(False)
        return max(0.0, min(self.info.duration, round(t / fd) * fd))

    def _step(self, coarse: bool) -> float:
        frame = self.info.frame_duration if self.info and self.info.frame_duration else 1 / 30
        if coarse:
            return 10 * frame if self._frames_mode() else 1.0      # T39: Shift = 10 frames
        return frame

    def _nudge_trim(self, is_in: bool, steps: int, coarse: bool) -> None:
        if self.info is None:
            return
        frame = self._step(False)
        delta = steps * self._step(coarse)
        self._enable_trim()
        if is_in:
            self.trim_in = max(0.0, min(self.trim_in + delta, self.trim_out - frame))
            target = self.trim_in
        else:
            self.trim_out = max(self.trim_in + frame, min(self.trim_out + delta, self.info.duration))
            target = self.trim_out
        self._enable_trim()
        self._show_times()
        self._show_at(target)

    def _show_at(self, t: float) -> None:
        """Move the playhead/⌛ to t at once and seek there (mpv catches up)."""
        t = self._snap(t)
        self.timeline.show_position(t)
        self.time_field.setText(self._fmt(t))
        self.video.seek(t, True)
        now = time.monotonic()
        self._hold_target, self._hold_until = t, now + 0.5
        self._nudge_target, self._nudge_stamp = t, now

    # T8: while an In/Out edge is dragged with the mouse (timeline edge or field
    # scrub) the playhead shows the edge; on release it returns where it was.
    # Discrete changes (typing, arrows, wheel) move the playhead to the edge and stay.
    def _begin_edge_preview(self) -> None:
        if self._edge_return is None and self.info is not None:
            self.video.pause()
            self._edge_return = self.timeline.position

    def _edge_preview(self, t: float, final: bool) -> None:
        if final:
            self._end_edge_preview()
            return
        self._begin_edge_preview()
        self._show_at(t)

    def _end_edge_preview(self) -> None:
        if self._edge_return is None:
            return
        t, self._edge_return = self._edge_return, None
        self._show_at(t)

    def _nudge_position(self, steps: int, coarse: bool) -> None:
        if self.info is None:
            return
        self.video.pause()
        now = time.monotonic()
        # mpv reports the new position with a delay — chain fast steps from the last target.
        chain = now - self._nudge_stamp < 0.6 or self.video.seek_busy()
        base = self._nudge_target if chain else self._position()
        position = self._snap(base + steps * self._step(coarse))
        self._nudge_target, self._nudge_stamp = position, now
        self._hold_target, self._hold_until = position, now + 0.5
        self.timeline.set_position(position)
        self.time_field.setText(self._fmt(position))
        self.video.seek(position)

    def _time_field_committed(self, text: str) -> None:
        if self.info is None:
            return
        value = self._parse(text)
        if value is None or value > self.info.duration:
            self.time_field.set_error(True)
            return
        self.time_field.set_error(False)
        value = self._snap(value)
        self.time_field.setText(self._fmt(value))
        self._nudge_target, self._nudge_stamp = value, time.monotonic()
        self.video.seek(value)

    def _show_times(self) -> None:
        self.in_field.setText(self._fmt(self.trim_in))
        self.out_field.setText(self._fmt(self.trim_out))
        if self.info is not None:
            self.d_field.setText(self._fmt_length(self._trim_range()[1]))
        else:
            self.d_field.setText(self._fmt(0.0))
        for field in (self.in_field, self.out_field, self.d_field):
            field.set_error(False)
        self.timeline.set_range(self.trim_in, self.trim_out)
        if self.loop_button.isChecked():
            self._update_loop()
        self._history_touch()

    # ------------------------------------------------- Duration (T39)
    def _set_duration(self, length: float) -> None:
        """D edits move Out; In stays. Length = what's exported (Out frame included)."""
        if not self._editable():
            return
        if not self.trim_button.isChecked():
            self._enable_trim()
            self.trim_in = self._snap(self.timeline.position)    # D with Trim off: from the playhead
        frame = self._step(False)
        length = max(frame, length)
        self.trim_out = self._snap(max(self.trim_in, min(self.info.duration, self.trim_in + length - frame)))
        self._show_times()
        self._show_at(self.trim_out)

    def _duration_committed(self, text: str) -> None:
        if self.info is None:
            return
        value = self._parse(text, length=True)
        if value is None or value <= 0:
            self.d_field.set_error(True)
            return
        self._history_flush()
        self._set_duration(value)
        self._history_commit()

    def _nudge_duration(self, steps: int, coarse: bool) -> None:
        if self.info is None:
            return
        self._set_duration(self._trim_range()[1] + steps * self._step(coarse))

    # --------------------------------------------- frames mode (T39)
    def _frames_mode(self) -> bool:
        return self.config.flag("frames_mode")

    def toggle_frames_mode(self) -> None:
        self.config.set_flag("frames_mode", not self._frames_mode())
        self.timeline.set_frames_mode(self._frames_mode())
        self._show_times()
        self.time_field.setText(self._fmt(self.timeline.position))

    def _frame_of(self, t: float) -> int:
        return round(t / self._step(False))

    def _frame_offset(self) -> int:
        """T47: a sequence counts frames by the numbers in its file names (shot_0063 → 63);
        a video counts from 0. The timecode always starts at 0."""
        sequence = getattr(self.info, "sequence", None) if self.info is not None else None
        return sequence.first if sequence is not None else 0

    def _fmt(self, t: float) -> str:
        """Timecode, or the frame number (source fps; a sequence's own numbers) in frames mode."""
        if self._frames_mode() and self.info is not None:
            return str(self._frame_of(t) + self._frame_offset())
        return format_time(t)

    def _fmt_length(self, length: float) -> str:
        if self._frames_mode() and self.info is not None:
            return str(max(1, round(length / self._step(False))))   # frames that get exported
        return format_time(length)

    def _parse(self, text: str, length: bool = False) -> float | None:
        """Frames mode: "120" or "120f" → frame 120; with ":" it's a timecode anyway."""
        cleaned = text.strip().lower().rstrip("f").strip()
        if self.info is not None and self._frames_mode() and ":" not in cleaned:
            try:
                frames = float(cleaned.replace(",", "."))
            except ValueError:
                return None
            if frames < 0:
                return None
            if not length:                      # a position, not a length: own numbers (T47)
                frames = max(0.0, frames - self._frame_offset())   # below the first → first
            return frames * self._step(False)
        return parse_time(text if not text.strip().lower().endswith("f") else cleaned)

    # ------------------------------------------------- Home/End (T41)
    def _go_to(self, start: bool) -> None:
        """Start/end of the trim range (or the file); playback keeps its state."""
        if self.info is None:
            return
        if self.trim_button.isChecked():
            t = self.trim_in if start else self.trim_out
        else:
            t = 0.0 if start else max(0.0, self.info.duration - self._step(False))
        t = self._snap(t)
        self.timeline.show_position(t)
        self.time_field.setText(self._fmt(t))
        self.video.seek(t, True)
        now = time.monotonic()
        self._hold_target, self._hold_until = t, now + 0.5
        self._nudge_target, self._nudge_stamp = t, now

    # ------------------------------------------------------ Loop (T21)
    def _update_loop(self) -> None:
        if self.info is None:
            return
        if not self.loop_button.isChecked():
            self.video.set_loop(False)
        elif self.trim_button.isChecked():
            start, length = self._trim_range()
            self.video.set_loop(True, start, start + length)
        else:
            self.video.set_loop(True)

    # ------------------------------------------------- Undo/Redo (T32)
    HISTORY_DEPTH = 20

    def _snapshot(self, trim_on: bool | None = None, crop_on: bool | None = None) -> tuple:
        r = self.crop
        trim_on = self.trim_button.isChecked() if trim_on is None else trim_on
        crop_on = self.crop_button.isChecked() if crop_on is None else crop_on
        return (trim_on, round(self.trim_in, 6), round(self.trim_out, 6),
                crop_on, r.x(), r.y(), r.width(), r.height())

    def _history_reset(self) -> None:
        self._history_timer.stop()
        self._history = [self._snapshot()] if self.info is not None else []
        self._hpos = 0

    def _history_touch(self) -> None:
        """Something changed: a step is written after ~0.5 s of calm with no mouse
        button held — a drag or a run of arrow/wheel steps becomes one step."""
        if not self._applying and self.info is not None and self._history:
            self._history_timer.start()

    def _history_flush(self, **before) -> None:
        """Before a discrete action: a pending change (drag, arrows) becomes its own step.
        `before`: button states from before a toggle (Qt flips them before `toggled`)."""
        if self._history_timer.isActive():
            self._history_commit(self._snapshot(**before))

    def _history_commit(self, state: tuple | None = None) -> None:
        if self._applying or self.info is None or not self._history:
            return
        if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            self._history_timer.start()            # still dragging — wait for the release
            return
        self._history_timer.stop()
        state = state or self._snapshot()
        if state == self._history[self._hpos]:
            return
        del self._history[self._hpos + 1:]         # a new action drops the redo branch
        self._history.append(state)
        if len(self._history) > self.HISTORY_DEPTH + 1:
            del self._history[0]
        self._hpos = len(self._history) - 1

    def _history_apply(self, state: tuple) -> None:
        trim_on, t_in, t_out, crop_on, x, y, w, h = state
        self._applying = True
        try:
            self.trim_in, self.trim_out = t_in, t_out
            self._set_checked(self.trim_button, trim_on)
            self.timeline.set_trim_enabled(trim_on)
            self.crop = QRect(x, y, w, h)
            self._set_checked(self.crop_button, crop_on)
            self.video.set_crop_enabled(crop_on)
            self.video.set_crop(self.crop)
            self._show_times()
            self._show_crop()
            self._update_loop()
            self._reencode_changed("crop")
        finally:
            self._applying = False

    def undo(self) -> None:
        if not self._editable() or not self._history:
            return
        self._history_commit()                     # a pending change becomes a step first
        if self._hpos > 0:
            self._hpos -= 1
            self._history_apply(self._history[self._hpos])

    def redo(self) -> None:
        if not self._editable() or not self._history:
            return
        self._history_commit()
        if self._hpos < len(self._history) - 1:
            self._hpos += 1
            self._history_apply(self._history[self._hpos])

    # ============================================================== crop
    def _full_frame(self) -> QRect:
        return QRect(0, 0, self.info.width, self.info.height) if self.info else QRect()

    def _visible_frame(self) -> QRect:
        """T31: the visible part of the frame (canvas zoom comes with T20 — until then
        the whole frame is always visible)."""
        visible = getattr(self.video, "visible_frame_rect", None)
        rect = visible() if visible else None
        return QRect(rect) if rect else self._full_frame()

    def _crop_toggled(self, checked: bool) -> None:
        if self.info is not None and not self._quiet:
            self._history_flush(crop_on=not checked)
            # T31: off → reset; on → take exactly the visible part of the frame
            self.crop = self._visible_frame() if checked else self._full_frame()
            self.video.set_crop(self.crop)
            self._show_crop()
            self._history_commit()
        self.video.set_crop_enabled(checked and self.info is not None)
        self._reencode_changed("crop")

    def _crop_drawn(self, rect: QRect) -> None:
        if self.worker is not None:
            self.video.set_crop(self.crop)
            return
        self._history_flush()
        self.crop = rect
        self._show_crop()
        if not self.crop_button.isChecked():
            self._set_checked(self.crop_button, True)   # dragging on the video turns Crop on
            self.video.set_crop_enabled(True)
        self._reencode_changed("crop")
        self._history_commit()

    def reset_crop(self) -> None:
        if self.info is None:
            return
        self._history_flush()
        self.crop = self._full_frame()
        self.video.set_crop(self.crop)
        self._show_crop()
        self._history_commit()

    def _crop_field_committed(self, key: str, text: str) -> None:
        if self.info is None:
            return
        try:
            value = int(float(text.strip()))
        except ValueError:
            self.crop_fields[key].set_error(True)
            return
        self._history_flush()
        self._apply_crop_value(key, value)
        self._history_commit()

    def _nudge_crop(self, key: str, steps: int, coarse: bool) -> None:
        if self.info is None:
            return
        current = {"X": self.crop.x(), "Y": self.crop.y(),
                   "W": self.crop.width(), "H": self.crop.height()}[key]
        self._apply_crop_value(key, current + steps * (10 if coarse else 1))

    def _apply_crop_value(self, key: str, value: int) -> None:
        r = QRect(self.crop)
        fw, fh = self.info.width, self.info.height
        if key == "X":
            r.moveLeft(max(0, min(value, fw - r.width())))
        elif key == "Y":
            r.moveTop(max(0, min(value, fh - r.height())))
        elif key == "W":
            r.setWidth(max(2, min(value, fw - r.x())))
        else:
            r.setHeight(max(2, min(value, fh - r.y())))
        self.crop = r
        self.video.set_crop(r)
        if not self.crop_button.isChecked():
            self._set_checked(self.crop_button, True)
            self.video.set_crop_enabled(True)
        self._show_crop()
        self._reencode_changed("crop")

    def _show_crop(self) -> None:
        r = self.crop
        values = {"X": r.x(), "Y": r.y(), "W": r.width(), "H": r.height()} if self.info else {}
        for key, field in self.crop_fields.items():
            field.setText(str(values.get(key, 0)))
            field.set_error(False)
        self._history_touch()

    def _effective_crop(self, exact: bool | None = None) -> CropRect | None:
        """Crop for the export. H.264 (.mp4/Original) needs even geometry; PNG, GIF
        and Copy Frame take the exact pixels (W 741 stays 741)."""
        if self.info is None or not self.crop_button.isChecked() or self.crop == self._full_frame():
            return None
        if exact is None:
            exact = self.fmt in ("gif", "png")      # not _kind(): it calls back here
        rect = CropRect(self.crop.x(), self.crop.y(), self.crop.width(), self.crop.height())
        if exact:
            return clamp_crop(rect, self.info.width, self.info.height)
        return normalize_crop(rect, self.info.width, self.info.height)

    def _needs_reencode(self) -> bool:
        return (self._effective_crop() is not None or self.scale != 1.0
                or self._target_fps() is not None
                or (self.info is not None and self.info.sequence is not None))

    def _reencode_reason(self) -> str:
        if self.info is not None and self.info.sequence is not None:
            return "an image sequence"
        if self._effective_crop() is not None:
            return "crop"
        if self.scale != 1.0:
            return "scale"
        return "fps"

    def _reencode_changed(self, reason: str) -> None:
        """Original can't do crop/scale/fps → switch to .mp4 once and say why."""
        if self.fmt == "copy" and self._needs_reencode() and not self._prores_original():
            self.set_format("mp4")
            self.toast.show_message(f"Original → .mp4: {reason} needs re-encoding",
                                    action=False, ms=4000)

    # =============================================================== fps
    def _target_fps(self) -> float | None:
        """Output rate if lower than the source; None → no fps filter."""
        if self.info is None or not self.info.fps or self.fps_out is None:
            return None
        return self.fps_out if self.fps_out < self.info.fps - 0.0005 else None

    def _show_fps(self) -> None:
        if self.info is None or not self.info.fps:
            self.fps_field.setText("—" if self.info is None else "?")
        else:
            self.fps_field.setText(format_fps(self.fps_out or self.info.fps))
        self.fps_field.set_error(False)

    def _set_fps(self, value: float) -> None:
        if self.info.sequence is not None:            # T36: the field sets the frame rate
            self._set_sequence_fps(value)
            return
        source = self.info.fps
        value = max(1.0, min(source, value))
        self.fps_out = None if value >= source - 0.0005 else value
        self._show_fps()
        self._reencode_changed("fps")

    SEQUENCE_FPS_MAX = 1000.0

    def _set_sequence_fps(self, value: float) -> None:
        """T36: rate of the frames, no upper limit (but sane). Trim, crop and the frame
        stay where they were — counted in frames."""
        value = max(1.0, min(self.SEQUENCE_FPS_MAX, round(value, 3)))
        info = self.info
        if abs(value - info.fps) < 0.0005:
            self._show_fps()
            return
        fps = info.fps
        keep = {
            "position": round(self._position() * fps),
            "in": round(self.trim_in * fps), "out": round(self.trim_out * fps),
            "trim": self.trim_button.isChecked(), "crop_on": self.crop_button.isChecked(),
            "crop": QRect(self.crop), "scale": self.scale, "fmt": self.fmt,
            "playing": self.video.playing,
        }
        self._open_info(self.source, with_fps(info, value), keep)

    def _restore_kept(self, keep: dict) -> None:
        fps = self.info.fps
        last = len(self.info.sequence.files) - 1
        self.trim_in = min(keep["in"], last) / fps
        self.trim_out = min(keep["out"], last) / fps
        self._set_checked(self.trim_button, keep["trim"])
        self.crop = keep["crop"]
        self.video.set_crop(self.crop)
        self._set_checked(self.crop_button, keep["crop_on"])
        self.video.set_crop_enabled(keep["crop_on"])
        self.scale = keep["scale"]
        self.scale_button.setText(format_scale(self.scale))
        self.fmt = keep["fmt"]
        self._show_format()
        self._show_times()
        self._show_crop()
        self._update_loop()
        self._seek_when_loaded(min(keep["position"], last) / fps, tries=40)

    def _seek_when_loaded(self, target: float, tries: int) -> None:
        """A seek sent while mpv is still loading is dropped — wait until it plays."""
        if self.video.time_pos is not None or tries <= 0:
            self._show_at(target)
            return
        QTimer.singleShot(50, lambda: self._seek_when_loaded(target, tries - 1))

    def _fps_committed(self, text: str) -> None:
        if not self._editable() or not self.info.fps:
            return
        try:
            value = float(text.strip().replace(",", "."))
        except ValueError:
            value = 0.0
        if value <= 0:
            self.fps_field.set_error(True)
            return
        self._set_fps(value)

    def _nudge_fps(self, steps: int, coarse: bool) -> None:
        if not self._editable() or not self.info.fps:
            return
        step = 5.0 if coarse else 1.0
        value = self.fps_out or self.info.fps
        for _ in range(abs(steps)):     # snap to whole steps: 23.976 → 23 → 22 …
            units = round(value / step, 6)
            value = (math.floor(units) + 1 if steps > 0 else math.ceil(units) - 1) * step
        self._set_fps(value)

    # ============================================================ format
    def _format_label(self, fmt: str) -> str:
        if fmt == "copy":
            suffix = self.source.suffix.lower() if self.source else ".mkv"
            return f"Original ({suffix})"
        return f".{fmt}"

    def _show_format(self) -> None:
        self.format_button.setText(self._format_label(self.fmt))

    def set_format(self, fmt: str, explain: bool = False) -> None:
        if fmt not in FORMATS:
            return
        if fmt == "copy" and self._needs_reencode() and not self._prores_original():
            if explain:      # T37: the hotkey must not fail silently
                self.toast.show_message(
                    f"Original unavailable: {self._reencode_reason()} needs re-encoding",
                    action=False, ms=4000)
            return
        self.fmt = fmt
        self._show_format()

    def _format_clicked(self) -> None:
        self._show_format()          # the click itself must not toggle
        menu = self._new_menu()
        idle = self.worker is None
        reencode = self._needs_reencode()
        for fmt in FORMATS:
            blocked = fmt == "copy" and reencode and not self._prores_original()
            self._menu_choice(menu, self._format_label(fmt), fmt == self.fmt,
                              lambda f=fmt: self.set_format(f), idle and not blocked,
                              check_column=True)
        menu.setMinimumWidth(self.format_button.width())
        height = menu.sizeHint().height()
        menu.exec(self.format_button.mapToGlobal(QPoint(0, -height - theme.HGAP)))

    # ============================================================= scale
    def _scale_clicked(self) -> None:
        self._show_scale_menu()

    def _output_size(self, scale: float) -> tuple[int, int] | None:
        if self.info is None:
            return None
        crop = self._effective_crop()
        width, height = (crop.width, crop.height) if crop else (self.info.width, self.info.height)
        return scaled_size(width, height, scale)

    def _show_scale_menu(self) -> None:
        """Rows: scale · resolution (dim) · ✓ · hotkey (dim). Fixed columns, so
        nothing shifts for rows without ✓ or without a key."""
        menu = self._new_menu()
        keys = {value: display_key(self.config.hotkey(action_id))
                for action_id, value in SCALE_HOTKEYS.items()
                if self.config.hotkey(action_id)}
        sizes = {value: self._output_size(value) for value in SCALES}
        res_text = {v: f"{s[0]}×{s[1]}" if s else "" for v, s in sizes.items()}
        metrics = QFontMetrics(self.font())
        res_width = max((metrics.horizontalAdvance(t) for t in res_text.values()), default=0)
        key_width = max((metrics.horizontalAdvance(k) for k in keys.values()), default=0)
        check_width = metrics.horizontalAdvance("✓")
        for value in SCALES:
            cells = [(res_text[value], res_width, theme.DIM, 0),
                     ("✓" if value == self.scale else "", check_width, "", 10)]
            if key_width:
                cells.append((keys.get(value, ""), key_width, theme.HOTKEY_DIM, 14))
            action = QWidgetAction(menu)
            action.setDefaultWidget(MenuRow(
                menu, format_scale(value), enabled=self.worker is None,
                callback=lambda v=value: self.set_scale(v), cells=cells))
            menu.addAction(action)
        menu.setMinimumWidth(self.scale_button.width())
        height = menu.sizeHint().height()
        menu.exec(self.scale_button.mapToGlobal(QPoint(0, -height - theme.HGAP)))

    def set_scale(self, value: float) -> None:
        self.scale = value
        self.scale_button.setText(format_scale(value))
        self._reencode_changed("scale")

    # ============================================================ export
    def _export_clicked(self) -> None:
        if self.worker is not None:
            self.export_button.setEnabled(False)
            self.worker.cancel()
        else:
            self.export(ask=self._asks_where())   # T42: the button follows Save To

    def _prores_original(self) -> bool:
        """T46: Original stays possible with crop/scale/fps — re-encoded into ProRes."""
        return self.info is not None and self.info.prores

    def _kind(self) -> str:
        """mp4 | gif | png | copy | prores — Original falls back to mp4 if something needs
        re-encoding; a ProRes source re-encodes into its own profile instead (T46)."""
        if self.fmt == "copy" and self._needs_reencode():
            return "prores" if self._prores_original() else "mp4"
        return self.fmt

    def _suffix(self) -> str:
        kind = self._kind()
        return {"mp4": ".mp4", "gif": ".gif"}.get(kind, self.source.suffix.lower())

    # ==================================================== fullscreen (T22)
    def toggle_fullscreen(self) -> None:
        """F only. Fullscreen opens on the window's monitor; leaving restores
        maximized or normal, as it was."""
        if self._fullscreen:
            self._leave_fullscreen(restore_maximized=True)
        else:
            self._was_maximized = self.isMaximized()
            self._window_geometry = self.saveGeometry()
            self._fullscreen = True
            self.showFullScreen()
        self._apply_layout()

    def _leave_fullscreen(self, restore_maximized: bool) -> None:
        self._fullscreen = False
        if restore_maximized and self._was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self._apply_layout()

    # ------------------------------------------------ right-button drag (PureRef)
    RMB_DRAG = 5             # px before a right press moves the window instead of the menu

    @staticmethod
    def _is_export_key(event) -> bool:
        mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if mods not in (Qt.KeyboardModifier.ControlModifier,
                        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            return False
        return event.key() == Qt.Key.Key_E or (win_frame.ENABLED and event.nativeVirtualKey() == 0x45)

    def _rmb_event(self, obj, event) -> bool:
        """Right button anywhere in the window: click → menu, drag → move the window.
        Dragging out of fullscreen (or a maximized window) restores it under the cursor."""
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
            self._rmb_press = event.globalPosition().toPoint()
            self._rmb_dragging = False
            return True
        if self._rmb_press is None:
            return False
        if kind == QEvent.Type.MouseMove and event.buttons() & Qt.MouseButton.RightButton:
            pos = event.globalPosition().toPoint()
            if not self._rmb_dragging:
                if (pos - self._rmb_press).manhattanLength() < self.RMB_DRAG:
                    return True
                self._start_window_drag(self._rmb_press)
            self.move(pos - self._drag_offset)
            return True
        if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.RightButton:
            press, dragging = self._rmb_press, self._rmb_dragging
            self._rmb_press, self._rmb_dragging = None, False
            if not dragging:
                self.show_menu(press)
            return True
        return False

    def _start_window_drag(self, press: QPoint) -> None:
        self._rmb_dragging = True
        frame = self.frameGeometry()
        rel_x = (press.x() - frame.x()) / max(1, frame.width())
        offset_y = press.y() - frame.y()
        if self._fullscreen or self.isMaximized():
            # like dragging a maximized window's title bar: keep the cursor at the same
            # relative spot of the restored window
            if self._fullscreen:
                self._leave_fullscreen(restore_maximized=False)
            else:
                self.showNormal()
            normal = self.normalGeometry()
            offset_y = min(offset_y, max(0, normal.height() - 40))
            self._drag_offset = QPoint(round(rel_x * normal.width()), offset_y)
            self.move(press - self._drag_offset)
        else:
            self._drag_offset = press - frame.topLeft()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if not getattr(self, "_ui_ready", False):
            return
        if event.type() == QEvent.Type.WindowStateChange:
            self.title_strip.sync()
        if event.type() == QEvent.Type.WindowStateChange and self._fullscreen \
                and not self.isFullScreen() and not self.isMinimized():
            self._fullscreen = False            # left by the system (e.g. Win+↓)
            self._apply_layout()

    # ================================================== UI modes (T27)
    MIN_HEIGHT = {"normal": 420, "minimal": 300, "hidden": 180}

    def toggle_minimize_ui(self) -> None:
        """Ctrl+Shift+\\: hide the bottom row (title and timeline stay)."""
        self.set_ui_mode("normal" if self._ui_mode == "minimal" else "minimal")

    def toggle_hide_ui(self) -> None:
        """Ctrl+\\: only the video; the timeline row and the title show on hover."""
        self.set_ui_mode("normal" if self._ui_mode == "hidden" else "hidden")

    def set_ui_mode(self, mode: str) -> None:
        if mode == self._ui_mode:
            return
        self._ui_mode = mode
        self.config.set_ui_mode(mode)
        self._apply_layout()

    def _apply_layout(self) -> None:
        """Window: panels under the video. Fullscreen and Hide UI: video fills the
        window, panels float over its bottom (fullscreen 16 px from the edges, 22d;
        Hide UI in a window 8 px). Minimize UI and Hide UI: timeline row only."""
        mode = self._ui_mode
        overlay = self._fullscreen or mode == "hidden"
        self.bottom_bar.setVisible(mode == "normal")
        edge = theme.GAP2 if self._fullscreen else theme.GAP
        if overlay:
            self._panels_layout.setContentsMargins(edge, theme.GAP, edge, edge)
        if overlay != self._overlay:
            self._overlay = overlay
            if overlay:
                self._root.setContentsMargins(0, 0, 0, 0)
                self._video_gap.changeSize(0, 0)
                self._root.removeWidget(self.panels)
                theme.set_flag(self.panels, "overlay", True)
                self._panels_reveal.set_shown(False, animate=False)
            else:
                self._panels_reveal.set_shown(True, animate=False)
                self._root.setContentsMargins(theme.GAP, 0, theme.GAP, theme.GAP2)
                self._video_gap.changeSize(0, theme.GAP)
                self._panels_layout.setContentsMargins(0, 0, 0, 0)
                theme.set_flag(self.panels, "overlay", False)
                self._root.addWidget(self.panels)
                self._show_cursor()
        if not self._fullscreen:
            self.setMinimumHeight(self.MIN_HEIGHT[mode])
        # the hover title strip: Hide UI in a window only
        strip = mode == "hidden" and not self._fullscreen
        if not strip:
            self._title_reveal.set_shown(False, animate=False)
        self._title_reveal.active = strip
        self._panels_reveal.active = overlay
        frame_hidden = strip
        if frame_hidden != self._frame_hidden:
            self._frame_hidden = frame_hidden
            win_frame.refresh(self, frame_hidden)
        self._root.invalidate()
        self._central.updateGeometry()
        self._place_overlay()
        # leaving a compact mode in a narrow window: grow to what fits (nothing is clipped)
        QTimer.singleShot(0, self._fit_minimum)

    def _fit_minimum(self) -> None:
        if self._fullscreen or self.isMaximized():
            return
        need = self.minimumSizeHint().expandedTo(self.minimumSize())
        if self.width() < need.width() or self.height() < need.height():
            self.resize(max(self.width(), need.width()), max(self.height(), need.height()))

    def _place_overlay(self) -> None:
        if not getattr(self, "_ui_ready", False):
            return
        central = self._central
        if self._overlay:
            height = self.panels.sizeHint().height()
            self.panels.setGeometry(0, central.height() - height, central.width(), height)
            self.panels.raise_()
        self.title_strip.setGeometry(0, 0, central.width(), self.title_strip.HEIGHT)
        self.title_strip.raise_()
        self.toast.raise_()

    CURSOR_HIDE_S = 1.5      # fullscreen: hide a still cursor

    def _update_overlay(self) -> None:
        """Called from the poll (30 ms): hover → show at once, leave → fade out (22a–c)."""
        if not (self._panels_reveal.active or self._title_reveal.active):
            return
        global_pos = QCursor.pos()
        pos = self._central.mapFromGlobal(global_pos)
        held = bool(QApplication.mouseButtons())
        focus = QApplication.focusWidget()
        popup = QApplication.activePopupWidget() is not None
        for reveal in (self._panels_reveal, self._title_reveal):
            if not reveal.active:
                continue
            if held and self._press_origin == "video":
                want = False                                   # 22b: crop / pan drag
            elif held and self._press_origin == reveal.name:
                want = True                                    # 22c: playhead, edges, scrub
            else:
                editing = focus is not None and isinstance(focus, _FieldEdit) \
                    and reveal.widget.isAncestorOf(focus)
                want = reveal.zone().contains(pos) or editing or (popup and reveal.shown)
            if want != reveal.shown:
                reveal.set_shown(want, animate=not want)
                if want:
                    self._place_overlay()
        self._update_cursor(global_pos, held)

    def _update_cursor(self, global_pos: QPoint, held: bool) -> None:
        """Fullscreen: a still cursor over the video hides after 1.5 s; any move shows it."""
        now = time.monotonic()
        if global_pos != self._cursor_pos:
            self._cursor_pos = QPoint(global_pos)
            self._cursor_still_since = now
            self._show_cursor()
            return
        if (self._fullscreen and not self._cursor_hidden and not held
                and not self._panels_reveal.shown and QApplication.activePopupWidget() is None
                and self.isActiveWindow()
                and now - self._cursor_still_since >= self.CURSOR_HIDE_S):
            QApplication.setOverrideCursor(Qt.CursorShape.BlankCursor)
            self._cursor_hidden = True

    def _show_cursor(self) -> None:
        if self._cursor_hidden:
            self._cursor_hidden = False
            QApplication.restoreOverrideCursor()

    # ----------------------------------------------- Windows frame (T27 Hide UI)
    RESIZE_BAND = 6          # px at the edges that resize the frameless window

    def nativeEvent(self, event_type, message):  # noqa: N802
        if self._frame_hidden and not self._fullscreen:
            msg = win_frame.message(event_type, message)
            if msg is not None:
                kind, hwnd, wparam, lparam = msg
                if kind == win_frame.WM_NCCALCSIZE and win_frame.calc_size(hwnd, wparam, lparam):
                    return True, 0
                if kind == win_frame.WM_NCHITTEST:
                    hit = self._hit_test()
                    if hit:
                        return True, hit
        return super().nativeEvent(event_type, message)

    def _hit_test(self) -> int:
        """Edges resize, the shown title strip (outside its buttons) is the caption."""
        pos = self.mapFromGlobal(QCursor.pos())
        if not self.isMaximized():
            hit = win_frame.edge_hit(pos.x(), pos.y(), self.width(), self.height(),
                                     self.RESIZE_BAND)
            if hit:
                return hit
        strip = self.title_strip
        if self._title_reveal.shown and strip.isVisible():
            local = strip.mapFrom(self, pos)
            if strip.rect().contains(local) and not strip.over_button(local):
                return win_frame.HTCAPTION
        return 0

    # ========================================================= zoom (T20)
    def zoom_fit(self) -> None:
        if self.info is not None:
            self.video.zoom_fit()

    def zoom_actual(self) -> None:
        if self.info is not None:
            self.video.zoom_actual()
            self.toast.show_message("100%", action=False, ms=1200)

    def _stem(self) -> str:
        """Base name for exports: the file's stem, or the sequence name (shot_#### → shot)."""
        if self.info is not None and self.info.sequence is not None:
            return self.info.sequence.stem
        return self.source.stem

    def _last_frame_number(self) -> int:
        info = self.info
        if info.sequence is not None:
            return info.sequence.numbers[-1]
        return round(info.duration * (info.fps or 0.0))

    def _asks_where(self) -> bool:
        return self.config.get("save_to") == "ask"

    def _export_title(self) -> str:
        """T42: the button's label; "…" = a dialog will open."""
        return "Export to…" if self._asks_where() else "Export"

    def _config_changed(self) -> None:
        self.export_button.set_idle_text(self._export_title())

    def export(self, ask: bool = False) -> None:
        """T42: Ctrl+E — next to the source, Ctrl+Shift+E — asks where (Save / Save As);
        Save To only decides what the button does."""
        if self.info is None or self.worker is not None:
            return
        if self._kind() == "png":
            self._export_png(ask)
            return
        kind, suffix = self._kind(), self._suffix()
        default = self.source.with_name(f"{self._stem()}.cut{suffix}")

        if not ask:
            self._start_export(unique_path(default))
            return

        file_filter = {
            "mp4": "MP4 (*.mp4);;Matroska (*.mkv);;QuickTime (*.mov)",
            "gif": "GIF (*.gif)",
        }.get(kind, f"Source container (*{suffix})")
        filename, _ = QFileDialog.getSaveFileName(self, "Export", str(default), file_filter)
        if not filename:
            return
        output = Path(filename)
        if not output.suffix:
            output = output.with_suffix(suffix)
        out_suffix = output.suffix.lower()
        if kind == "mp4" and out_suffix not in PRECISE_CONTAINERS:
            self._error("Export", "Re-encoding writes MP4, MKV or MOV.")
            return
        if kind == "gif" and out_suffix != ".gif":
            self._error("Export", "GIF export writes a .gif file.")
            return
        if kind in ("copy", "prores") and out_suffix != self.source.suffix.lower():
            self._error("Export", f"Original keeps the source container ({self.source.suffix}).")
            return
        if same_path(output, self.source):
            # Export never replaces the source — export, then delete it by hand.
            self._error("Export", "Choose another name — Export never replaces the source.")
            return
        self._start_export(output.resolve())

    def _trim_range(self) -> tuple[float, float]:
        """(start, duration). Trim off → the whole file; Out frame is included."""
        info = self.info
        if not self.trim_button.isChecked():
            return 0.0, info.duration
        start = self.trim_in
        return start, min(self.trim_out - start + info.frame_duration, info.duration - start)

    def _start_export(self, output: Path) -> None:
        info = self.info
        kind = self._kind()
        start, duration = self._trim_range()
        if start > 0 and kind != "copy":
            # FFmpeg starts at the first frame at/after -ss: aim a bit early → the In frame
            quarter = info.frame_duration / 4
            start, duration = start - quarter, duration
        start_number = 0
        if kind == "png":      # output = folder; frames are numbered by source frame (T19b)
            fps = info.fps or 0.0
            start_number = round(start * fps)          # rounds back to the In frame
            if info.sequence is not None:              # T36: the In frame's own number
                first, _count = sequence_range(info, start, duration)
                start_number = info.sequence.numbers[first]
            output.mkdir(parents=True, exist_ok=True)
            output = png_pattern(output, self._stem(), self._last_frame_number())
        try:
            job = ExportJob(
                source=self.source, info=info,
                temporary_output=output if kind == "png" else temporary_output(output),
                final_output=output, start_number=start_number,
                start=start, duration=duration, crop=self._effective_crop(),
                fast=kind == "copy",
                crf=self.config.crf, preset=self.config.preset,
                audio=self.audio if kind != "gif" else None,
                subtitle=self.subtitle, scale=self.scale,
                fps=self._target_fps(), kind=kind,
                palette=palette_path(output) if kind == "gif" else None,
            )
            if kind == "gif":
                commands, warning = build_gif_commands(job)
            elif kind == "png":
                command, warning = build_png_command(job)
                commands = [command]
            else:
                command, warning = build_ffmpeg_command(job)
                commands = [command]
        except Exception as exc:
            self._error("Export", str(exc))
            return

        self.job = job
        self.video.pause()
        self.worker = ExportWorker(commands, job.duration, self)
        self.worker.progress_changed.connect(self.export_button.set_progress)
        self.worker.completed.connect(self._export_done)
        self.worker.finished.connect(self.worker.deleteLater)
        self.export_button.set_busy(True)
        self._update_enabled()
        self.worker.start()
        if warning:
            self.toast.show_message(warning, action=False, error=True, ms=8000)

    def _export_done(self, success: bool, error: str) -> None:
        job, self.job, self.worker = self.job, None, None
        self.export_button.set_busy(False)
        self.export_button.setEnabled(True)
        self._update_enabled()
        if job is None:
            return
        if job.palette is not None:
            self._remove(job.palette)       # always: success, cancel or error
        if job.kind == "png":
            self._png_done(job, success, error)
            return

        if not success:
            self._remove(job.temporary_output)
            if error:
                self._error("FFmpeg error", error)
            else:
                self.toast.show_message("Export cancelled", action=False, ms=3000)
            return

        try:
            validate_video(job.temporary_output)
        except Exception as exc:
            self._remove(job.temporary_output)
            self._error("Export failed", str(exc))
            return

        position = self._position()
        try:
            commit_output(job.temporary_output, job.final_output)
        except Exception as exc:
            # Keep the finished temp file — the export itself succeeded.
            self._error(
                "Could not move the result into place",
                f"{exc}\n\nThe exported file is kept here:\n{job.temporary_output}",
            )
            return

        self.toast.show_message(f"Saved: {job.final_output.name}", action=True)
        self._toast_path = job.final_output
        self.video.seek(position)
        self.config.set("last_format", job.kind)      # T25: Last Used

    # --------------------------------------------------------- PNG (T19b)
    def _export_png(self, ask: bool) -> None:
        """Frames go straight into a folder: name.cut\\ next to the source, or a picked one."""
        if not ask:
            folder = self.source.with_name(f"{self._stem()}.cut")
            counter = 2
            while folder.exists():
                folder = self.source.with_name(f"{self._stem()}.cut-{counter}")
                counter += 1
        else:
            chosen = QFileDialog.getExistingDirectory(self, "Export PNG sequence to",
                                                      str(self.source.parent))
            if not chosen:
                return
            folder = Path(chosen)
        self._start_export(folder)

    @staticmethod
    def _png_frames(job: ExportJob) -> list[Path]:
        # No glob: names like "[VCB-Studio] …" contain [ ] — glob wildcards.
        folder = job.final_output.parent
        prefix = job.final_output.name.split("%")[0]
        try:
            return sorted(p for p in folder.iterdir()
                          if p.name.startswith(prefix) and p.suffix.lower() == ".png")
        except OSError:
            return []

    def _png_done(self, job: ExportJob, success: bool, error: str) -> None:
        folder = job.final_output.parent
        self._drop_gap_frames(job)
        count = len(self._png_frames(job))
        self._toast_path = folder
        if success:
            self.toast.show_message(f"Saved: {folder.name} · {count} frames", action=True)
            self.config.set("last_format", "png")
            return
        if count == 0:
            try:
                folder.rmdir()           # only if empty — never touches the user's files
            except OSError:
                pass
        if error:
            self._error("FFmpeg error", error)
        elif count:
            # T19c: frames written so far stay, like other programs do
            self.toast.show_message(f"Export cancelled · {count} frames kept", action=True)
        else:
            self.toast.show_message("Export cancelled", action=False, ms=3000)

    @staticmethod
    def _drop_gap_frames(job: ExportJob) -> None:
        """T36: missing source frames stay missing in a .png export (shot_1012.png isn't
        written) — the numbering keeps matching the source, the gap stays visible."""
        sequence = job.info.sequence
        if sequence is None:
            return
        for first, last in sequence.missing():
            for number in range(first, last + 1):
                try:
                    (job.final_output.parent / (job.final_output.name % number)).unlink(
                        missing_ok=True)
                except (OSError, TypeError):
                    pass

    # ---------------------------------------------------- Copy Frame (T19a)
    def copy_frame(self) -> None:
        """Exact current frame → clipboard as PNG, with crop and scale. Runs in the
        background (FFmpeg), the UI stays responsive."""
        if self.info is None or self._frame_process is not None:
            return
        # mpv reports the frame's start; half a frame back keeps FFmpeg on the same frame.
        time_pos = max(0.0, self._position() - self.info.frame_duration / 2)
        command = build_frame_command(self.source, self.info, time_pos,
                                      self._effective_crop(exact=True), self.scale)
        process = QProcess(self)
        process.setProgram(command[0])
        process.setArguments(command[1:])
        process.finished.connect(lambda *_: self._frame_copied(process))
        process.errorOccurred.connect(lambda *_: self._frame_copied(process))
        self._frame_process = process
        process.start()

    def _frame_copied(self, process: QProcess) -> None:
        if self._frame_process is not process:
            return
        self._frame_process = None
        data = bytes(process.readAllStandardOutput())
        process.deleteLater()
        image = QImage.fromData(data, "PNG")
        if image.isNull():
            self.toast.show_message("Could not copy the frame", action=False, error=True, ms=4000)
            return
        mime = QMimeData()
        mime.setImageData(image)
        if image.hasAlphaChannel():
            # T36: the bitmap formats drop transparency; Figma, Photoshop and browsers
            # read the PNG one
            mime.setData("PNG", data)
            mime.setData("image/png", data)
        QApplication.clipboard().setMimeData(mime)
        self.toast.show_message(f"Frame copied · {image.width()}×{image.height()}",
                                action=False, ms=2500)

    def _show_settings(self) -> None:
        # T16b: Quality/Speed only matter for x264 (.mp4)
        self.settings_popover.set_encoder_enabled(self.info is None or self._kind() == "mp4")
        self.settings_popover.popup_over(self.video)

    @staticmethod
    def _remove(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _show_in_folder(self) -> None:
        path = self._toast_path
        if path is None:
            return
        if sys.platform == "win32":
            subprocess.Popen(f'explorer /select,"{path}"')
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    # ============================================================ helpers
    def _update_enabled(self) -> None:
        loaded = self.info is not None
        idle = self.worker is None
        for widget in (self.in_field, self.out_field, self.d_field, *self.crop_fields.values()):
            widget.setEnabled(loaded and idle)
        self.fps_field.setEnabled(loaded and idle and bool(self.info and self.info.fps))
        self.time_field.setEnabled(loaded)
        for widget in (self.trim_button, self.crop_button, self.format_button, self.scale_button):
            widget.setEnabled(loaded and idle)
        self.play_button.setEnabled(loaded)
        self.loop_button.setEnabled(loaded)        # like ▶ — inactive until a file is open
        self.export_button.setEnabled(loaded)

    def _error(self, title: str, text: str) -> None:
        box = QMessageBox(QMessageBox.Icon.NoIcon, title, text, QMessageBox.StandardButton.Ok, self)
        box.exec()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if getattr(self, "_ui_ready", False) and self.toast.isVisible():
            self.toast.reposition()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            self.open_path(Path(urls[0].toLocalFile()))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker is not None:
            answer = QMessageBox.question(self, "Export in progress", "Cancel the export and quit?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.cancel()
            self.worker.wait(5000)
            if self.job is not None:
                self._remove(self.job.temporary_output)
                if self.job.palette is not None:
                    self._remove(self.job.palette)
        if self._fullscreen and self._window_geometry is not None:
            self.config.set_geometry(self._window_geometry)   # T22: fullscreen isn't remembered
        else:
            self.config.set_geometry(self.saveGeometry())
        self.poll.stop()
        self.video.shutdown()
        event.accept()
