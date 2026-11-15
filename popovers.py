"""Settings and Hotkeys popovers."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QWidget)

import context_menu
import theme
import win_frame
from config import ESC_KEY, HOTKEY_ACTIONS, MENU_SHORTCUTS, Config, display_key
from version import APP_NAME, BUILD, VERSION
from widgets import CheckBox, Popover, Segmented, button, dim_label


class SettingsPopover(Popover):
    """T25 order: Playback → Quality → Speed → Default Format → Export → version.
    Width follows the content (widest row + 16 px on both sides)."""

    def __init__(self, config: Config, parent=None):
        super().__init__("Settings", parent)
        self.toggle_keys = (MENU_SHORTCUTS["settings"][1],)          # 9a: Ctrl+K closes too
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
        ], "save_to", last=not context_menu.ENABLED)

        # T30: Explorer right-click items, applied at once (Windows only)
        self.explorer: dict[str, CheckBox] = {}
        if context_menu.ENABLED:
            self.body.addWidget(QLabel("Explorer Menu"))
            for item, title in (("main", "ChikChik!"), ("gif", "ChikChik! → GIF")):
                box = CheckBox(title)
                box.setToolTip("Right-click menu of video files")
                box.toggled.connect(lambda on, i=item: self._explorer_toggled(i, on))
                self.body.addWidget(box)
                self.explorer[item] = box

        self.body.addSpacing(14)
        version = dim_label(" · ".join(filter(None, (f"{APP_NAME} {VERSION}", BUILD))))
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

    def _explorer_toggled(self, item: str, on: bool) -> None:
        try:
            context_menu.set_installed(item, on)
        except OSError:
            pass
        self.explorer[item].set_checked(context_menu.is_installed(item))

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
        for item, box in self.explorer.items():     # the registry is the truth
            box.set_checked(context_menu.is_installed(item))

    def popup_over(self, widget) -> None:
        self.refresh()
        super().popup_over(widget)


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
        theme.set_flag(self, "empty", not portable)          # T104: "…" stays dim

    def natural_width(self) -> int:
        """Text + padding (8 px each side) + 2 px air each side, at least 64 px."""
        return max(64, self.fontMetrics().horizontalAdvance(self.text()) + 16 + 4)

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


# T49 (B2): the Hotkeys window in groups. A str = remappable action (config id),
# a tuple = reference line (title, keys) — gestures and fixed keys, not remappable.
GROUPS = (
    ("Playback", ("play", "loop", "frame_next", "frame_prev", "sec_next", "sec_prev",
                  "sec5_next", "sec5_prev", "go_start", "go_end",
                  "mute", "volume_up", "volume_down")),
    ("Edit", ("trim", "trim_in", "trim_out", "reset_trim", "crop", "reset_crop",
              "undo", "redo",
              ("Frames / Timecode", "Ctrl+Click ⌛"),
              ("Frame-Exact Scrub", "Alt+Drag"))),
    ("Export", ("fmt_mp4", "fmt_gif", "fmt_png", "fmt_copy",
                "scale_1", "scale_2", "scale_3", "scale_4")),
    ("View", ("fullscreen", (ESC_KEY[0], ESC_KEY[1]), "jump_playhead",
              ("Resize Keeping Proportions", "Shift+Drag Edge"))),          # T44 / T106
)


class HotkeysPopover(Popover):
    """T29: width follows the content (16 px at the sides and between the columns),
    all key caps as wide as the widest one, at most MAX_ROWS rows — then it scrolls."""

    MAX_ROWS = 20            # visible height = 20 key rows (~ the RMB menu), then it scrolls
    ROW_SPACING = 6
    GROUP_GAP = 14           # T49: air above a group header

    def __init__(self, config: Config, parent=None):
        super().__init__("Hotkeys", parent)
        self.toggle_keys = (MENU_SHORTCUTS["hotkeys"][1],)           # 9a: Ctrl+/ closes too
        self.toggle_scan = win_frame.SCAN_SLASH if win_frame.ENABLED else None   # …on RU too
        self.config = config

        content = QWidget()
        self._grid = grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(theme.GAP2)
        grid.setVerticalSpacing(self.ROW_SPACING)
        self.caps: dict[str, KeyCap] = {}
        self._refs: list[QLabel] = []
        self._headers: list[QLabel] = []
        titles = {a.id: a.title for a in HOTKEY_ACTIONS}
        row = 0
        for group, items in GROUPS:
            if row:
                grid.setRowMinimumHeight(row, self.GROUP_GAP - self.ROW_SPACING)
                row += 1
            header = dim_label(group)                        # T49: grey group header
            grid.addWidget(header, row, 0, 1, 2)
            self._headers.append(header)
            row += 1
            for item in items:
                if isinstance(item, tuple):                  # reference: not a key
                    title, keys = item
                    grid.addWidget(QLabel(title), row, 0)
                    ref = dim_label(keys)
                    ref.setAlignment(Qt.AlignmentFlag.AlignCenter)   # centred like the caps
                    grid.addWidget(ref, row, 1, Qt.AlignmentFlag.AlignRight)
                    self._refs.append(ref)
                else:
                    grid.addWidget(QLabel(titles[item]), row, 0)
                    cap = KeyCap()
                    cap.captured.connect(lambda combo, a=item: self._assign(a, combo))
                    grid.addWidget(cap, row, 1, Qt.AlignmentFlag.AlignRight)
                    self.caps[item] = cap
                row += 1

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
        """Caps share the widest width; at most MAX_ROWS key rows of height, then scroll.
        T68: reference lines are as tall as a key row — same air as the other rows."""
        widest = max(cap.natural_width() for cap in self.caps.values())
        widest = max([widest] + [ref.sizeHint().width() + 20 for ref in self._refs])
        row_h = max(cap.sizeHint().height() for cap in self.caps.values())
        for cap in self.caps.values():
            cap.setFixedWidth(widest)
        for ref in self._refs:
            ref.setFixedSize(widest, row_h)
        visible = self.MAX_ROWS * row_h + (self.MAX_ROWS - 1) * self.ROW_SPACING
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._content.adjustSize()
        scrolls = self._content.sizeHint().height() > visible
        # keys ↔ scrollbar: the same 16 px as between the columns
        self._grid.setContentsMargins(0, 0, theme.GAP2 if scrolls else 0, 0)
        self._content.adjustSize()
        hint = self._content.sizeHint()
        bar = self.scroll.verticalScrollBar().sizeHint().width() if scrolls else 0
        self.scroll.setFixedSize(hint.width() + bar, min(hint.height(), visible))
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
