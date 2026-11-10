from __future__ import annotations

import locale
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication, QMessageBox

from version import VERSION

BASE_DIR = Path(__file__).resolve().parent
_DLL_DIRECTORY_HANDLES = []


def prepare_mpv_dll() -> None:
    """Add the folder with mpv-2.dll to the DLL search path before importing mpv."""
    candidates = [BASE_DIR]
    if getattr(sys, "frozen", False):
        candidates.insert(0, Path(sys.executable).resolve().parent)
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if bundle_dir:
            candidates.insert(0, Path(bundle_dir))

    for directory in candidates:
        if not directory.is_dir():
            continue
        if not any((directory / n).exists() for n in ("mpv-2.dll", "libmpv-2.dll", "mpv-1.dll")):
            continue
        os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
        if os.name == "nt" and hasattr(os, "add_dll_directory"):
            try:
                _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(directory)))
            except OSError:
                pass
        break


def main() -> int:
    prepare_mpv_dll()

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(fmt)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)
    app.setApplicationName("ChikChik")
    app.setOrganizationName("ChikChik")
    app.setApplicationVersion(VERSION)
    locale.setlocale(locale.LC_NUMERIC, "C")   # required by libmpv

    import theme

    theme.load_fonts(app)
    app.setStyleSheet(theme.stylesheet())
    app.setWindowIcon(theme.emoji_icon())

    from backend import executable

    missing = []
    for name in ("ffmpeg", "ffprobe"):
        try:
            executable(name)
        except RuntimeError:
            missing.append(name)
    if missing:
        QMessageBox.critical(
            None, "ChikChik!",
            "Not found: " + ", ".join(missing) + "\n\nReinstall ChikChik!, or put FFmpeg "
            "into the ffmpeg folder next to the program, or add it to PATH.",
        )
        return 1

    try:
        from main_window import MainWindow

        window = MainWindow()
    except Exception as exc:
        QMessageBox.critical(
            None, "ChikChik! — startup error",
            f"{exc}\n\nPut mpv-2.dll (or libmpv-2.dll) next to app.py, "
            "or reinstall ChikChik!.\nPython and the DLL must both be 64-bit.",
        )
        return 1

    # ChikChik.exe [--gif] [video]   (--gif: "Trim / Crop → GIF" context menu item)
    args = [a for a in sys.argv[1:] if a.strip()]
    gif = "--gif" in args
    paths = [a for a in args if a != "--gif"]
    # T38: show the window only after its first frame is drawn (no light flash).
    theme.cloak(window, True)
    uncloaked = False

    def uncloak() -> None:
        nonlocal uncloaked
        if not uncloaked:
            uncloaked = True
            theme.cloak(window, False)

    window.video.frameSwapped.connect(uncloak)
    QTimer.singleShot(1000, uncloak)          # safety net if no frame arrives
    window.show()
    if paths:
        window.open_path(Path(paths[0]))
    if gif:          # after open_path: it sets the default format (T25)
        window.set_format("gif")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
