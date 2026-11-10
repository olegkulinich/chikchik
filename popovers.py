"""Settings and Hotkeys popovers."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QWidget)

import theme
from config import HOTKEY_ACTIONS, Config, display_key
from version import APP_NAME, VERSION
from widgets import CheckBox, Popover, Segmented, button, dim_label


class SettingsPopover(Popover):
    """T25 order: Playback → Quality → Speed → Default Format → Export → version.
    Width follows the content (widest row + 16 px on both sides)."""

    def __init__(self, config: Config, parent=None):
        super().__init__("Settings", parent)
        self.config = config
        self._encoder_titles: list[QLabel] = []
        self._encoder_hints: list[QLabel] = []

        self.body.addWidget(QLabel("Playback"))
        self.autoplay = CheckBox("Autoplay")
        self.autoplay.setToolTip("Start playing when a file opens")
        self.autoplay.toggled.connect(lambda on: config.set_flag("autoplay", on))
        self.body.addWidget(self.autoplay)
        self.body.addSpacing(10)

        self.quality = self._section("Quality", [
            ("high", "High", "CRF 18"),
            ("normal", "Medium", "CRF 20"),
            ("small", "Low", "CRF 25"),
        ], "quality", hint="Higher quality — bigger file.", encoder=True)
        self.speed = self._section("Speed", [
            ("fast", "Fast", "x264 preset fast"),
            ("medium", "Medium", "x264 preset medium"),
            ("slow", "Slow", "x264 preset slow"),
        ], "speed", hint="Slower — smaller file, same quality.", encoder=True)

        self.body.addWidget(QLabel("Default Format"))
        self.default_format = Segmented([
            ("mp4", ".mp4", ""), ("gif", ".gif", ""), ("png", ".png", ""),
            ("copy", "Original", "Source container, no re-encoding"),
        ])
        self.default_format.selected.connect(lambda v: config.set("default_format", v))
        self.body.addWidget(self.default_format)
        self.last_used = CheckBox("Last Used")
        self.last_used.setToolTip("Open files with the format of the last export")
        self.last_used.toggled.connect(lambda on: config.set_flag("last_used", on))
        self.body.addWidget(self.last_used)
        self.body.addSpacing(10)

        self.save_to = self._section("Export", [
            ("same", "Same Folder", "Save next to the source as name.cut.mp4"),
            ("ask", "Ask", "Choose where to save each time"),
        ], "save_to", last=True)

        self.body.addSpacing(14)
        version = dim_label(f"{APP_NAME} {VERSION}")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(version)

        config.changed.connect(self.refresh)
        self.refresh()

    def _section(self, title: str, options, key: str, last: bool = False,
                 hint: str = "", encoder: bool = False) -> Segmented:
        label = QLabel(title)
        self.body.addWidget(label)
        seg = Segmented(options)
        seg.selected.connect(lambda value, k=key: self.config.set(k, value))
        self.body.addWidget(seg)
        if hint:                       # T26: one line under the buttons
            hint_label = dim_label(hint)
            self.body.addWidget(hint_label)
            if encoder:
                self._encoder_hints.append(hint_label)
        if encoder:
            self._encoder_titles.append(label)
        if not last:
            self.body.addSpacing(10)
        return seg

    def set_encoder_enabled(self, enabled: bool) -> None:
        """T16b: Quality/Speed are x264 settings — grey (whole section) for Original/.gif/.png."""
        for seg in (self.quality, self.speed):
            seg.setEnabled(enabled)
        for label in self._encoder_titles:
            label.setProperty("dim", not enabled)
            theme.repolish(label)

    def refresh(self) -> None:
        for seg, key in ((self.quality, "quality"), (self.speed, "speed"),
                         (self.save_to, "save_to"), (self.default_format, "default_format")):
            seg.set_value(self.config.get(key))
        self.autoplay.set_checked(self.config.flag("autoplay"))
        last = self.config.flag("last_used")
        self.last_used.set_checked(last)
        self.default_format.setEnabled(not last)   # the picked one stays highlighted


class KeyCap(QPushButton):
    """Click → empty & waiting; press a combo → assigned. Esc cancels, Backspace clears."""

    captured = Signal(str)   # portable text, "" = cleared

    def __init__(self, parent=None):
        self.capturing = False
        self.portable = ""
        super().__init__(parent)
        self.setProperty("keycap", True)
        self.setMinimumWidth(72)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self.start)

    def set_key(self, portable: str) -> None:
        self.portable = portable
        self.setText(display_key(portable))

    def natural_width(self) -> int:
        """Text + padding (8 px each side) + 8 px air each side, at least 72 px."""
        return max(72, self.fontMetrics().horizontalAdvance(self.text()) + 16 + 16)

    def start(self) -> None:
        self.capturing = True
        theme.set_flag(self, "error", False)
        theme.set_flag(self, "capturing", True)
        self.setText("")
        self.setFocus()

    def stop(self) -> None:
        self.capturing = False
        theme.set_flag(self, "capturing", False)
        self.set_key(self.portable)

    def show_error(self, portable: str) -> None:
        self.capturing = False
        theme.set_flag(self, "capturing", False)
        theme.set_flag(self, "error", True)
        self.setText(display_key(portable))

    def clear_error(self) -> None:
        theme.set_flag(self, "error", False)
        self.set_key(self.portable)

    def event(self, event) -> bool:
        if self.capturing and event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            if event.type() == QEvent.Type.KeyPress:
                self._key(event)
            event.accept()
            return True
        if event.type() == QEvent.Type.FocusOut and self.capturing:
            self.stop()
        return super().event(event)

    def _key(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt,
                   Qt.Key.Key_Meta, Qt.Key.Key_unknown):
            return
        if key == Qt.Key.Key_Escape and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            self.stop()
            return
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self.capturing = False
            theme.set_flag(self, "capturing", False)
            self.captured.emit("")
            return
        combo = QKeySequence(event.keyCombination()).toString(
            QKeySequence.SequenceFormat.PortableText
        )
        self.capturing = False
        theme.set_flag(self, "capturing", False)
        self.captured.emit(combo)


class HotkeysPopover(Popover):
    """T29: width follows the content (16 px at the sides and between the columns),
    all key caps as wide as the widest one, at most MAX_ROWS rows — then it scrolls."""

    MAX_ROWS = 20
    ROW_SPACING = 6

    def __init__(self, config: Config, parent=None):
        super().__init__("Hotkeys", parent)
        self.config = config

        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(theme.GAP2)
        grid.setVerticalSpacing(self.ROW_SPACING)
        self.caps: dict[str, KeyCap] = {}
        for row, action in enumerate(HOTKEY_ACTIONS):
            grid.addWidget(QLabel(action.title), row, 0)
            cap = KeyCap()
            cap.captured.connect(lambda combo, a=action.id: self._assign(a, combo))
            grid.addWidget(cap, row, 1, Qt.AlignmentFlag.AlignRight)
            self.caps[action.id] = cap
        # Reference only: frames mode is a Ctrl+click, not a reassignable key.
        row = len(HOTKEY_ACTIONS)
        grid.addWidget(QLabel("Frames / Timecode"), row, 0)
        hint = dim_label("Ctrl+Click ⌛")
        hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(hint, row, 1)
        self._rows = row + 1

        self.scroll = QScrollArea()
        self.scroll.setObjectName("HotkeysScroll")
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(content)
        self._content = content
        self.body.addWidget(self.scroll)

        self.message = QLabel(" ")
        self.message.setStyleSheet(f"color: {theme.ERROR_FG};")
        self.body.addSpacing(4)
        self.body.addWidget(self.message)

        footer = QHBoxLayout()
        footer.addStretch(1)
        reset = button("Reset")
        reset.setToolTip("Restore default hotkeys")
        reset.clicked.connect(config.reset_hotkeys)
        footer.addWidget(reset)
        footer.addStretch(1)
        self.body.addLayout(footer)

        self._error_timer = QTimer(self)
        self._error_timer.setSingleShot(True)
        self._error_timer.timeout.connect(self._clear_error)
        self._error_cap: KeyCap | None = None

        config.hotkeys_changed.connect(self.refresh)
        self.refresh()

    def _fit(self) -> None:
        """Caps share the widest width; the list shows at most MAX_ROWS rows."""
        widest = max(cap.natural_width() for cap in self.caps.values())
        for cap in self.caps.values():
            cap.setFixedWidth(widest)
        self._content.adjustSize()
        hint = self._content.sizeHint()
        row_h = max(cap.sizeHint().height() for cap in self.caps.values())
        rows = min(self._rows, self.MAX_ROWS)
        visible = rows * row_h + (rows - 1) * self.ROW_SPACING
        scrolls = self._rows > self.MAX_ROWS
        # the scrollbar gets its own 16 px gap instead of covering the keys
        extra = theme.GAP2 if scrolls else 0
        self.scroll.setFixedSize(hint.width() + extra, min(hint.height(), visible))
        self.adjustSize()

    def refresh(self) -> None:
        for action_id, cap in self.caps.items():
            cap.set_key(self.config.hotkey(action_id))
        self._fit()

    def _assign(self, action_id: str, combo: str) -> None:
        owner = self.config.owner_of(combo, exclude=action_id)
        cap = self.caps[action_id]
        if owner:
            self._clear_error()
            cap.show_error(combo)
            self.message.setText(f"{display_key(combo)} is already used by {owner}")
            self._error_cap = cap
            self._error_timer.start(1800)
            return
        self.config.set_hotkey(action_id, combo)

    def _clear_error(self) -> None:
        if self._error_cap is not None:
            self._error_cap.clear_error()
            self._error_cap = None
        self.message.setText(" ")
