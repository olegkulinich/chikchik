"""Settings and Hotkeys popovers."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton

import theme
from config import HOTKEY_ACTIONS, Config, display_key
from version import APP_NAME, VERSION
from widgets import Popover, Segmented, button, dim_label


class SettingsPopover(Popover):
    def __init__(self, config: Config, parent=None):
        super().__init__("Settings", parent)
        self.config = config
        self.setFixedWidth(312)

        self.quality = self._section("Quality", [
            ("high", "High", "CRF 18"),
            ("normal", "Medium", "CRF 20"),
            ("small", "Low", "CRF 25"),
        ], "quality", hint="Higher quality — bigger file.")
        self.speed = self._section("Speed", [
            ("fast", "Fast", "x264 preset fast"),
            ("medium", "Medium", "x264 preset medium"),
            ("slow", "Slow", "x264 preset slow"),
        ], "speed", hint="Slower — smaller file, same quality.")
        self.save_to = self._section("Save to", [
            ("same", "Same Folder", "Save next to the source as name.cut.mp4 / .gif"),
            ("ask", "Ask", "Show a Save As dialog"),
        ], "save_to", last=True)

        self.body.addSpacing(14)
        version = dim_label(f"{APP_NAME} {VERSION}")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(version)

        config.changed.connect(self.refresh)
        self.refresh()

    def _section(self, title: str, options, key: str, last: bool = False,
                 hint: str = "") -> Segmented:
        self.body.addWidget(QLabel(title))
        seg = Segmented(options)
        seg.selected.connect(lambda value, k=key: self.config.set(k, value))
        self.body.addWidget(seg)
        if hint:                       # T26: one line under the buttons
            self.body.addWidget(dim_label(hint))
        if not last:
            self.body.addSpacing(10)
        return seg

    def refresh(self) -> None:
        for seg, key in ((self.quality, "quality"), (self.speed, "speed"),
                         (self.save_to, "save_to")):
            seg.set_value(self.config.get(key))


class KeyCap(QPushButton):
    """Click → empty & waiting; press a combo → assigned. Esc cancels, Backspace clears."""

    captured = Signal(str)   # portable text, "" = cleared

    def __init__(self, parent=None):
        self.capturing = False
        self.portable = ""
        super().__init__(parent)
        self.setProperty("keycap", True)
        self.setFixedWidth(72)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self.start)

    def set_key(self, portable: str) -> None:
        self.portable = portable
        self.setText(display_key(portable))

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
    def __init__(self, config: Config, parent=None):
        super().__init__("Hotkeys", parent)
        self.config = config
        self.setFixedWidth(312)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(theme.GAP2)
        grid.setVerticalSpacing(6)
        self.caps: dict[str, KeyCap] = {}
        for row, action in enumerate(HOTKEY_ACTIONS):
            grid.addWidget(QLabel(action.title), row, 0)
            cap = KeyCap()
            cap.captured.connect(lambda combo, a=action.id: self._assign(a, combo))
            grid.addWidget(cap, row, 1, Qt.AlignmentFlag.AlignRight)
            self.caps[action.id] = cap
        self.body.addLayout(grid)

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

    def refresh(self) -> None:
        for action_id, cap in self.caps.items():
            cap.set_key(self.config.hotkey(action_id))

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
