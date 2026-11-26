"""Colors, sizes and the global stylesheet (values from the Figma mockup)."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

# Colors
WINDOW = "#1E1E1E"
PANEL = "#222222"        # menus/popovers: a touch lighter than the window
BUTTON = "#2C2C2C"
BUTTON_HOVER = "#3B3B3B"
ACTIVE = "#1B3032"
ACTIVE_HOVER = "#23403F"
ERROR_BG = "#3A1C1D"
ERROR_FG = "#E05555"
FIELD = "#151515"
STROKE = "#333333"
TEXT = "#E6E6E6"         # T28: values ~11–14:1 — quieter, focus on the video
DIM = "#757575"          # T28: labels, hints, disabled ~3–4:1
HOTKEY_DIM = DIM
MINIMAP_TRACK = "#383838"   # timeline minimap: whole file
MINIMAP_VIEW = DIM    # timeline minimap: visible window
RULER = DIM               # timeline ruler ticks
FRAME_TICK = "#3D3D3D"    # T71: per-frame ticks when zoomed in (darker, shorter)
VIDEO_BG = "#000000"
CROP = "#00F0FF"      # bright cyan — inverse of the red playhead
CROP_HANDLE = "#FFFFFF"   # T108: corner handle fill (stroke = CROP)
PLAYHEAD = "#FF2A2A"
TRIM_RANGE = ACTIVE   # same teal as the active Crop button

# Sizes (everything on the 8 px grid)
H = 24
RADIUS = 8
GAP = 8
HGAP = 6     # between buttons/fields in a row
GAP2 = 16
FONT_PX = 12

FONT_FAMILY = "Inter"
EMOJI_FAMILY = "Segoe UI Emoji" if sys.platform == "win32" else "Noto Color Emoji"


def base_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def load_fonts(app: QApplication) -> None:
    global FONT_FAMILY
    families: list[str] = []
    for name in ("Inter-Regular.otf", "Inter-Medium.otf"):
        font_id = QFontDatabase.addApplicationFont(str(base_dir() / "fonts" / name))
        if font_id >= 0:
            families += QFontDatabase.applicationFontFamilies(font_id)
    FONT_FAMILY = families[0] if families else "Segoe UI"
    font = QFont(FONT_FAMILY)
    font.setPixelSize(FONT_PX)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(font)


def app_icon() -> QIcon:
    """T30: Fluent Emoji 3D ✂️ (icon.ico, all sizes); the emoji font as a fallback."""
    path = base_dir() / "icon.ico"
    if path.exists():
        icon = QIcon(str(path))
        if not icon.isNull():
            return icon
    return emoji_icon()


def emoji_icon(emoji: str = "✂️", size: int = 256) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    font = QFont(EMOJI_FAMILY)
    font.setPixelSize(int(size * 0.78))
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, emoji)
    painter.end()
    return QIcon(pixmap)


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def set_flag(widget: QWidget, name: str, value: bool) -> None:
    if bool(widget.property(name)) != value:
        widget.setProperty(name, value)
        repolish(widget)


def dark_title_bar(widget: QWidget) -> None:
    """Windows 10/11: dark native title bar in the window color."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))
        color = QColor(WINDOW)
        caption = ctypes.c_int(color.red() | color.green() << 8 | color.blue() << 16)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
        text = QColor(DIM)
        text_ref = ctypes.c_int(text.red() | text.green() << 8 | text.blue() << 16)
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text_ref), ctypes.sizeof(text_ref))
    except Exception:
        pass


def cloak(widget: QWidget, on: bool) -> None:
    """T38, Windows: keep the window invisible to DWM until Qt has drawn it.

    The whole window is drawn through OpenGL (QOpenGLWidget), so without this
    Windows shows an uncleared light-gray surface for a split second on start."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        value = ctypes.c_int(1 if on else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(widget.winId()), 13, ctypes.byref(value), ctypes.sizeof(value))  # DWMWA_CLOAK
    except Exception:
        pass


def dwm_flush() -> None:
    """E3 (T91): block until DWM's next composition (QTC_UNCLOAK_FLUSH=1)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.dwmapi.DwmFlush()
    except Exception:
        pass


def stylesheet() -> str:
    return f"""
* {{
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_PX}px;
    color: {TEXT};
    outline: none;
}}
QMainWindow, QDialog, QMessageBox {{ background: {WINDOW}; }}
QLabel {{ background: transparent; }}
QLabel[dim="true"] {{ color: {DIM}; }}

QPushButton {{
    background: {BUTTON};
    border: none;
    border-radius: {RADIUS}px;
    padding: 0 10px;
    min-height: {H}px; max-height: {H}px;
}}
QPushButton:hover {{ background: {BUTTON_HOVER}; }}
QPushButton:disabled {{ background: {BUTTON}; color: {DIM}; }}
QPushButton:checked {{ background: {ACTIVE}; }}
QPushButton:checked:hover {{ background: {ACTIVE_HOVER}; }}
QPushButton[error="true"] {{ background: {ERROR_BG}; color: {ERROR_FG}; }}

QPushButton[seg="true"]:hover {{ background: {BUTTON_HOVER}; }}
QPushButton[seg="true"]:checked {{ background: {ACTIVE}; }}
QPushButton[seg="true"]:disabled {{ color: {DIM}; background: {BUTTON}; }}
QPushButton[seg="true"]:checked:disabled {{ color: {DIM}; background: {ACTIVE}; }}   /* T25: the pick stays visible */

/* T68 (B2): key caps look like input fields (recessed), not buttons */
QPushButton[keycap="true"] {{ background: {FIELD}; color: {TEXT}; padding: 0 8px; }}
QPushButton[keycap="true"][empty="true"] {{ color: {DIM}; }}
QWidget#Panels[overlay="true"] {{ background: {WINDOW}; }}
QWidget#TitleStrip {{ background: {WINDOW}; }}
QPushButton#TitleButton, QPushButton#TitleClose {{
    background: transparent; border: none; border-radius: 0; padding: 0; color: {DIM};
    min-height: 32px; max-height: 32px;
}}
QPushButton#TitleButton:hover {{ background: {BUTTON_HOVER}; color: {TEXT}; }}
QPushButton#TitleClose:hover {{ background: #C42B1C; color: #FFFFFF; }}
QPushButton#RecentRemove {{ background: transparent; border: none; color: {DIM}; padding: 0;
    min-height: 20px; max-height: 20px; }}
QPushButton#RecentRemove:hover {{ background: {ACTIVE_HOVER}; color: {TEXT}; }}
QPushButton#PopoverClose {{ background: transparent; border: none; color: {DIM}; padding: 0;
    min-width: 20px; max-width: 20px; min-height: 20px; max-height: 20px; }}
QPushButton#PopoverClose:hover {{ background: transparent; color: {TEXT}; }}
QScrollArea#HotkeysScroll, QScrollArea#HotkeysScroll > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 4px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BUTTON_HOVER}; border-radius: 2px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QPushButton[keycap="true"]:hover {{ background: {FIELD}; color: {TEXT}; }}
QPushButton[keycap="true"][capturing="true"] {{ background: {ACTIVE}; color: {TEXT}; }}
QPushButton[keycap="true"][error="true"] {{ background: {ERROR_BG}; color: {ERROR_FG}; }}

/* No outline: a 1px rounded stroke renders unevenly at 125/150% on Windows.
   Fields are darker than the window ("recessed"), buttons lighter. */
QFrame#Field {{
    background: {FIELD};
    border: none;
    border-radius: {RADIUS}px;
}}
QFrame#Field[error="true"] {{ background: {ERROR_BG}; }}
QFrame#Field[seg="first"] {{ border-top-right-radius: 0; border-bottom-right-radius: 0; }}
QFrame#Field[seg="mid"] {{ border-radius: 0; }}
QFrame#Field[seg="last"] {{ border-top-left-radius: 0; border-bottom-left-radius: 0; }}
QFrame#Field QLineEdit {{
    background: transparent; border: none; padding: 0;
    selection-background-color: {ACTIVE};
}}
QFrame#Field QLineEdit:disabled {{ color: {DIM}; }}
QFrame#Field[error="true"] QLineEdit {{ color: {ERROR_FG}; }}

QFrame#Panel {{
    background: {PANEL};
    border: 1px solid {STROKE};
    border-radius: {RADIUS}px;
}}
QFrame#Toast {{
    background: {BUTTON};
    border: 1px solid {STROKE};
    border-radius: {RADIUS}px;
}}
QFrame#Toast QPushButton {{ background: {BUTTON_HOVER}; }}
QFrame#Toast QPushButton:hover {{ background: #4A4A4A; }}

QMenu {{
    background: {PANEL};
    border: 1px solid {STROKE};
    border-radius: {RADIUS}px;
    padding: 6px 0;
}}
QMenu::item {{ padding: 5px 16px 5px 14px; background: transparent; }}
QMenu::item:selected {{ background: {BUTTON_HOVER}; }}
QMenu::item:disabled {{ color: {DIM}; }}
QMenu::separator {{ height: 1px; background: {STROKE}; margin: 6px 12px; }}
QMenu::indicator {{ width: 0; }}
QMenu::right-arrow {{ width: 8px; }}

QToolTip {{
    background: {BUTTON}; color: {TEXT};
    border: 1px solid {STROKE}; padding: 4px 6px;
}}
QMessageBox QLabel {{ min-width: 280px; }}
QMessageBox QPushButton {{ min-width: 64px; }}
"""
