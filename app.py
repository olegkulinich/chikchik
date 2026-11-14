from __future__ import annotations

import startup_log  # noqa: I001 — first: T55a timestamps start here

import locale
import os
import sys
from pathlib import Path

import gl_warm  # noqa: I001 — E2 (A): before PySide6, so the warm-up overlaps the imports

gl_warm.start()
if startup_log.ENABLED:          # which hidden switches this run had (bench variants)
    _switches = " ".join(f"{k}={v}" for k, v in sorted(os.environ.items())
                         if k.startswith("QTC_") and k != "QTC_STARTUP_LOG")
    startup_log.mark(f"env: {_switches or '-'}")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication, QMessageBox

from version import VERSION

startup_log.mark("python: PySide6 imported")

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
    startup_log.mark("mpv dll path ready")

    # ChikChik.exe [--gif] [video]   (--gif: "Trim / Crop → GIF" context menu item)
    args = [a for a in sys.argv[1:] if a.strip()]
    gif = "--gif" in args
    paths = [a for a in args if a != "--gif"]
    # E2 (55c): ffprobe runs in a thread while Qt and the window start up.
    probe = None
    preload = None
    if paths:
        from backend import Probe, is_image

        probe = Probe(Path(paths[0]))
        startup_log.mark("ffprobe thread started")
        if not is_image(probe.path):          # sequences need the probe for their frame list
            preload = probe.path

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(fmt)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)
    app.setApplicationName("ChikChik")
    app.setOrganizationName("ChikChik")
    app.setApplicationVersion(VERSION)
    locale.setlocale(locale.LC_NUMERIC, "C")   # required by libmpv
    startup_log.mark("QApplication created")

    import theme

    theme.load_fonts(app)
    app.setStyleSheet(theme.stylesheet())
    app.setWindowIcon(theme.app_icon())
    startup_log.mark("theme: fonts, stylesheet, icon")

    from backend import executable

    missing = []
    for name in ("ffmpeg", "ffprobe"):
        try:
            executable(name)
        except RuntimeError:
            missing.append(name)
    startup_log.mark("ffmpeg/ffprobe found")
    if missing:
        QMessageBox.critical(
            None, "ChikChik!",
            "Not found: " + ", ".join(missing) + "\n\nReinstall ChikChik!, or put FFmpeg "
            "into the ffmpeg folder next to the program, or add it to PATH.",
        )
        return 1

    try:
        from main_window import MainWindow

        startup_log.mark("main_window imported")
        window = MainWindow(preload=preload)
        startup_log.mark("MainWindow created")
    except Exception as exc:
        QMessageBox.critical(
            None, "ChikChik! — startup error",
            f"{exc}\n\nPut mpv-2.dll (or libmpv-2.dll) next to app.py, "
            "or reinstall ChikChik!.\nPython and the DLL must both be 64-bit.",
        )
        return 1

    # T38: show the window only after its first frame is drawn (no light flash).
    theme.cloak(window, True)
    uncloaked = False

    def uncloak() -> None:
        nonlocal uncloaked
        if not uncloaked:
            uncloaked = True
            theme.cloak(window, False)
            startup_log.mark("window uncloaked (first frame swapped)")
            if not paths:
                startup_log.finish("done: no file")

    def uncloak_timer() -> None:
        if not uncloaked:
            startup_log.mark("uncloak by 1 s safety timer (no frame yet)")
        uncloak()

    window.video.frameSwapped.connect(uncloak)
    QTimer.singleShot(1000, uncloak_timer)    # safety net if no frame arrives
    window.show()
    startup_log.mark("window.show()")
    if paths:
        window.open_path(Path(paths[0]), probe=probe)
        startup_log.mark("open_path returned")
    if gif:          # after open_path: it sets the default format (T25)
        window.set_format("gif")
    if startup_log.ENABLED:
        QTimer.singleShot(0, lambda: startup_log.mark("event loop running"))
        QTimer.singleShot(15000, lambda: startup_log.finish("gave up: 15 s without a video frame"))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
