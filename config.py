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
    "autoplay": "0",         # T35; T103: off by default (neutral defaults, principle 1)
    "default_format": "mp4", # T25: mp4 | gif | png | copy (Original)
    "last_used": "0",        # T25: open with the format of the last export
    "last_format": "",       # T25: format of the last successful export
    "frames_mode": "0",      # T39: show frames instead of timecode
    "pin_on_top": "0",       # T45: window stays above others (remembered)
    "loop": "0",             # T53: ∞ remembered between launches
    "volume": "100",         # T66: 0–100, remembered
    "muted": "0",            # T67 build 2 (user): remembered too; a "Muted" toast on play
    "sequence_fps": "25",    # T67 build 2 (user): the last rate set for a sequence
}


@dataclass(frozen=True)
class HotkeyAction:
    id: str
    title: str
    default: str


HOTKEY_ACTIONS = (
    HotkeyAction("trim", "Trim", "T"),
    HotkeyAction("trim_in", "Trim In", "I"),
    HotkeyAction("trim_out", "Trim Out", "O"),
    HotkeyAction("crop", "Crop", "C"),
    HotkeyAction("frame_next", "Forward 1 frame", "Right"),
    HotkeyAction("frame_prev", "Backward 1 frame", "Left"),
    HotkeyAction("sec_next", "Forward 1 sec", "Ctrl+Right"),
    HotkeyAction("sec_prev", "Backward 1 sec", "Ctrl+Left"),
    HotkeyAction("sec5_next", "Forward 5 sec", "Shift+Right"),
    HotkeyAction("sec5_prev", "Backward 5 sec", "Shift+Left"),
    HotkeyAction("play", "Play/Pause", "Space"),
    HotkeyAction("loop", "Loop", "Shift+L"),                 # T21; T65 (was L); Shift = mode (JKL b3 trial Ctrl+Shift+L reverted)
    HotkeyAction("jkl_forward", "Forward 10 sec", "L"),    # T67
    HotkeyAction("jkl_stop", "Pause", "K"),                                # T67
    HotkeyAction("jkl_back", "Backward 10 sec", "J"),      # T67
    HotkeyAction("go_start", "Go to Start", "Home"),         # T41
    HotkeyAction("go_end", "Go to End", "End"),              # T41
    HotkeyAction("undo", "Undo", "Ctrl+Z"),                  # T32
    HotkeyAction("redo", "Redo", "Ctrl+Shift+Z"),            # T32
    HotkeyAction("mute", "Mute", "M"),                       # T66
    HotkeyAction("volume_up", "Volume Up", "Up"),            # T66: a focused field keeps ↑/↓
    HotkeyAction("volume_down", "Volume Down", "Down"),      # T66
    HotkeyAction("fullscreen", "Fullscreen", "F"),           # T22
    HotkeyAction("jump_playhead", "Jump to Playhead", "/"),  # T50: physical key (RU types ".")
    # Key N = 1/N; 0.75x has no key (menu only).
    HotkeyAction("scale_1", "Scale 1x", "1"),
    HotkeyAction("scale_2", "Scale 0.5x", "2"),
    HotkeyAction("scale_3", "Scale 0.33x", "3"),
    HotkeyAction("scale_4", "Scale 0.25x", "4"),
    # T37: formats, same order as everywhere — .mp4 / .gif / .png / Original.
    # T65: no default keys (M went to Mute); remappable, e.g. Alt+1.
    HotkeyAction("fmt_mp4", "Format .mp4", ""),
    HotkeyAction("fmt_gif", "Format .gif", ""),
    HotkeyAction("fmt_png", "Format .png", ""),
    HotkeyAction("fmt_copy", "Format Original", ""),
)

FORMAT_HOTKEYS = {"fmt_mp4": "mp4", "fmt_gif": "gif", "fmt_png": "png", "fmt_copy": "copy"}

SCALE_HOTKEYS = {"scale_1": 1.0, "scale_2": 0.5, "scale_3": 1 / 3, "scale_4": 0.25}

# Fixed shortcuts of the right-click menu (not editable, but checked for conflicts).
MENU_SHORTCUTS = {
    "hotkeys": ("Hotkeys", "Ctrl+/"),
    "settings": ("Settings", "Ctrl+K"),
    "open": ("Open", "Ctrl+O"),
    "open_recent": ("Open Recent", "Ctrl+Shift+O"),   # T40: the top recent file
    "export": ("Export", "Ctrl+E"),                # T42: "Export to…" when Save To = Ask
    "export_to": ("Export to…", "Ctrl+Shift+E"),   # T42: always asks where
    "copy_frame": ("Copy Frame", "Ctrl+C"),   # T19a
    "fit": ("Fit", "Ctrl+0"),                 # T20
    "actual_size": ("Actual Size", "Ctrl+1"), # T20
    "minimize_ui": ("Minimize UI", "Ctrl+Shift+\\"),   # T27
    "hide_ui": ("Hide UI", "Ctrl+\\"),                  # T27
    "fit_window": ("Fit Window to Video", "Ctrl+Shift+R"),  # T43
    "pin": ("Pin on Top", "Ctrl+Shift+A"),                  # T45 (↔ Unpin)
    "quit": ("Quit", "Ctrl+Q"),
}

# Extra key spellings for menu shortcuts (Shift+\ is reported as | on some layouts).
MENU_ALIASES = {"minimize_ui": ("Ctrl+|", "Ctrl+Shift+|")}

# T65: Esc never changes data — it only leaves fullscreen (fields and windows
# handle their own Esc). Fixed, but checked for conflicts like the menu shortcuts.
ESC_KEY = ("Exit Fullscreen", "Esc")

UI_MODES = ("normal", "minimal", "hidden")

_ARROWS = {"Left": "←", "Right": "→", "Up": "↑", "Down": "↓"}


EMPTY_KEY = "…"           # T104 (B2): like PureRef — "—" read as a minus


def display_key(portable: str) -> str:
    if not portable:
        return EMPTY_KEY
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
        stored = self._store.value(f"hotkeys/{action_id}")
        if stored is not None:
            return str(stored)
        default = next(a.default for a in HOTKEY_ACTIONS if a.id == action_id)
        # T65 (B2): a key the user gave to another action stays theirs — a new default
        # that lands on it stays empty (assign it in Hotkeys if wanted).
        if default:
            wanted = QKeySequence(default)
            for action in HOTKEY_ACTIONS:
                if action.id == action_id:
                    continue
                other = self._store.value(f"hotkeys/{action.id}")
                if other is not None and other and QKeySequence(str(other)) == wanted:
                    return ""
        return default

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
        for title, key in (*MENU_SHORTCUTS.values(), ESC_KEY):
            if QKeySequence(key) == wanted:
                return title
        return ""

    # --- recent files (T40)
    RECENT_KEPT = 11         # 10 shown + the open one (12.18)

    def recent(self) -> list[str]:
        value = self._store.value("window/recent", [])
        if isinstance(value, str):           # QSettings returns a bare str for one item
            value = [value]
        return [str(v) for v in (value or []) if v]

    def push_recent(self, path: str) -> None:
        items = [path] + [p for p in self.recent() if p.lower() != path.lower()]
        self._store.setValue("window/recent", items[:self.RECENT_KEPT])

    def remove_recent(self, path: str) -> None:
        self._store.setValue("window/recent",
                             [p for p in self.recent() if p.lower() != path.lower()])

    # --- window
    def popover_offset(self, name: str) -> tuple[int, int] | None:
        """B2-2b: where the user dragged a popover — offset of its centre from the
        video centre (remembered between launches). None = never moved → centred."""
        try:
            x, y = str(self._store.value(f"ui/popover_{name}", "")).split(",")
            return int(x), int(y)
        except ValueError:
            return None

    def set_popover_offset(self, name: str, x: int, y: int) -> None:
        self._store.setValue(f"ui/popover_{name}", f"{x},{y}")

    def ui_mode(self) -> str:
        """T27: normal | minimal (Minimize UI) | hidden (Hide UI) — remembered."""
        value = str(self._store.value("window/ui_mode", "normal"))
        return value if value in UI_MODES else "normal"

    def set_ui_mode(self, mode: str) -> None:
        self._store.setValue("window/ui_mode", mode)

    def geometry(self):
        return self._store.value("window/geometry")

    def set_geometry(self, data) -> None:
        self._store.setValue("window/geometry", data)

    def set_placeholder(self, value: str) -> None:
        """E3 (B): plain-text window placement for placeholder.py (read without Qt)."""
        self._store.setValue("window/placeholder", value)
