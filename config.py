"""Persistent settings and hotkeys (QSettings → registry on Windows)."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGui import QKeySequence

# Stored ids stay "normal"/"small" (shown as Medium/Low) so saved choices survive updates.
QUALITY = {"high": 18, "normal": 20, "small": 25}
SPEEDS = ("fast", "medium", "slow")

DEFAULTS = {
    "quality": "normal",     # high | normal | small  (High / Medium / Low)
    "speed": "medium",       # fast | medium | slow
    "save_to": "same",       # same | ask
    "autoplay": "1",         # T35: start playing when a file opens
    "default_format": "mp4", # T25: mp4 | gif | png | copy (Original)
    "last_used": "0",        # T25: open with the format of the last export
    "last_format": "",       # T25: format of the last successful export
    "frames_mode": "0",      # T39: show frames instead of timecode
}


@dataclass(frozen=True)
class HotkeyAction:
    id: str
    title: str
    default: str


HOTKEY_ACTIONS = (
    HotkeyAction("trim", "Trim", "T"),
    HotkeyAction("reset_trim", "Reset Trim", "Shift+Esc"),
    HotkeyAction("trim_in", "Trim In", "I"),
    HotkeyAction("trim_out", "Trim Out", "O"),
    HotkeyAction("crop", "Crop", "C"),
    HotkeyAction("reset_crop", "Reset Crop", "Esc"),
    HotkeyAction("frame_next", "Jump Forward 1 frame", "Right"),
    HotkeyAction("frame_prev", "Jump Backward 1 frame", "Left"),
    HotkeyAction("sec_next", "Jump Forward 1 sec", "Ctrl+Right"),
    HotkeyAction("sec_prev", "Jump Backward 1 sec", "Ctrl+Left"),
    HotkeyAction("sec5_next", "Jump Forward 5 sec", "Shift+Right"),
    HotkeyAction("sec5_prev", "Jump Backward 5 sec", "Shift+Left"),
    HotkeyAction("play", "Play/Pause", "Space"),
    HotkeyAction("loop", "Loop", "L"),                       # T21
    HotkeyAction("go_start", "Go to Start", "Home"),         # T41
    HotkeyAction("go_end", "Go to End", "End"),              # T41
    HotkeyAction("undo", "Undo", "Ctrl+Z"),                  # T32
    HotkeyAction("redo", "Redo", "Ctrl+Shift+Z"),            # T32
    # Key N = 1/N; 0.75x has no key (menu only).
    HotkeyAction("scale_1", "Scale 1x", "1"),
    HotkeyAction("scale_2", "Scale 0.5x", "2"),
    HotkeyAction("scale_3", "Scale 0.33x", "3"),
    HotkeyAction("scale_4", "Scale 0.25x", "4"),
    # T37: formats, same order as everywhere — .mp4 / .gif / .png / Original
    HotkeyAction("fmt_mp4", "Format .mp4", "M"),
    HotkeyAction("fmt_gif", "Format .gif", "G"),
    HotkeyAction("fmt_png", "Format .png", "P"),
    HotkeyAction("fmt_copy", "Format Original", "0"),
)

FORMAT_HOTKEYS = {"fmt_mp4": "mp4", "fmt_gif": "gif", "fmt_png": "png", "fmt_copy": "copy"}

SCALE_HOTKEYS = {"scale_1": 1.0, "scale_2": 0.5, "scale_3": 1 / 3, "scale_4": 0.25}

# Fixed shortcuts of the right-click menu (not editable, but checked for conflicts).
MENU_SHORTCUTS = {
    "hotkeys": ("Hotkeys", "Ctrl+/"),
    "settings": ("Settings", "Ctrl+K"),
    "open": ("Open", "Ctrl+O"),
    "export": ("Export", "Ctrl+E"),
    "overwrite": ("Overwrite", "Ctrl+Shift+S"),
    "copy_frame": ("Copy Frame", "Ctrl+C"),   # T19a
    "quit": ("Quit", "Ctrl+Q"),
}

_ARROWS = {"Left": "←", "Right": "→", "Up": "↑", "Down": "↓"}


def display_key(portable: str) -> str:
    if not portable:
        return "—"
    text = QKeySequence(portable).toString(QKeySequence.SequenceFormat.NativeText)
    parts = text.split("+")
    return "+".join(_ARROWS.get(p, p) for p in parts) if parts else text


class Config(QObject):
    changed = Signal()
    hotkeys_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Old name kept on purpose: settings/hotkeys survive the rename to ChikChik!
        self._store = QSettings("QuickTrimCrop", "QuickTrimCrop")

    # --- export settings
    def get(self, key: str) -> str:
        value = str(self._store.value(f"export/{key}", DEFAULTS[key]))
        if key == "speed" and value not in SPEEDS:   # e.g. "veryfast" from 4.x
            return DEFAULTS["speed"]
        return value

    def set(self, key: str, value: str) -> None:
        if self.get(key) != value:
            self._store.setValue(f"export/{key}", value)
            self.changed.emit()

    def flag(self, key: str) -> bool:
        return self.get(key) in ("1", "true", "True")

    def set_flag(self, key: str, value: bool) -> None:
        self.set(key, "1" if value else "0")

    def start_format(self) -> str:
        """T25: the format a newly opened file starts with."""
        if self.flag("last_used") and self.get("last_format"):
            return self.get("last_format")
        return self.get("default_format")

    @property
    def crf(self) -> int:
        return QUALITY.get(self.get("quality"), 20)

    @property
    def preset(self) -> str:
        return self.get("speed")

    # --- hotkeys
    def hotkey(self, action_id: str) -> str:
        default = next(a.default for a in HOTKEY_ACTIONS if a.id == action_id)
        return str(self._store.value(f"hotkeys/{action_id}", default))

    def hotkeys(self) -> dict[str, str]:
        return {a.id: self.hotkey(a.id) for a in HOTKEY_ACTIONS}

    def set_hotkey(self, action_id: str, portable: str) -> None:
        self._store.setValue(f"hotkeys/{action_id}", portable)
        self.hotkeys_changed.emit()

    def reset_hotkeys(self) -> None:
        self._store.remove("hotkeys")
        self.hotkeys_changed.emit()

    def owner_of(self, portable: str, exclude: str = "") -> str:
        """Title of the action that already uses this shortcut, or ''."""
        if not portable:
            return ""
        wanted = QKeySequence(portable)
        for action in HOTKEY_ACTIONS:
            if action.id != exclude and QKeySequence(self.hotkey(action.id)) == wanted:
                return action.title
        for title, key in MENU_SHORTCUTS.values():
            if QKeySequence(key) == wanted:
                return title
        return ""

    # --- window
    def geometry(self):
        return self._store.value("window/geometry")

    def set_geometry(self, data) -> None:
        self._store.setValue("window/geometry", data)
