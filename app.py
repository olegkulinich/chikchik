from __future__ import annotations

import startup_log  # noqa: I001 — first: T55a timestamps start here

startup_log.mark("python: app.py started")   # F build 4: frozen app may start the log earlier

import locale
import os
import sys
from pathlib import Path

import gl_warm  # noqa: I001 — E2 (A): before PySide6, so the warm-up overlaps the imports
from video_backend import USE_D3D11

# E4: desktop OpenGL straight away — skips Qt's GPU blacklist probe (bench: native window
# −6 ms, window −10…−20 ms). Set QT_OPENGL=angle / software by hand to override.
os.environ.setdefault("QT_OPENGL", "desktop")

gl_warm.start()
if startup_log.ENABLED:          # which hidden switches this run had (bench variants)
    _switches = " ".join(f"{k}={v}" for k, v in sorted(os.environ.items())
                         if (k.startswith("QTC_") and k != "QTC_STARTUP_LOG")
                         or k == "QT_OPENGL")
    startup_log.mark(f"env: {_switches or '-'}")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication, QMessageBox

from version import VERSION

startup_log.mark("python: PySide6 imported")
startup_log.cpu("PySide6 imported")


def install_qt_trace() -> None:
    """E4 (T95, QTC_GL_TRACE=1): Qt's OpenGL / window debug messages with timestamps into
    the startup log — what winId and the GL context creation spend their time on."""
    from PySide6.QtCore import QLoggingCategory, qInstallMessageHandler

    def handler(_mode, context, message) -> None:
        category = context.category or "qt"
        if category.startswith("qt.qpa"):
            startup_log.trace(category, message)
        else:
            sys.stderr.write(message + "\n")

    QLoggingCategory.setFilterRules("qt.qpa.gl=true\nqt.qpa.window=true")
    qInstallMessageHandler(handler)
    startup_log.on_finish(lambda: QLoggingCategory.setFilterRules(
        "qt.qpa.gl=false\nqt.qpa.window=false"))

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
        from backend import Probe

        probe = Probe(Path(paths[0]))
        startup_log.mark("ffprobe thread started")
        preload = probe.path                  # T96: sequences too (frame list from the probe thread)

    if startup_log.TRACE:
        install_qt_trace()
    if not USE_D3D11:
        fmt = QSurfaceFormat.defaultFormat()
        fmt.setSwapInterval(1)
        QSurfaceFormat.setDefaultFormat(fmt)
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)
    app.setApplicationName("ChikChik")
    app.setOrganizationName("ChikChik")
    app.setApplicationVersion(VERSION)
    # B2-2b: tooltips appear at once — no slide-down / fade (it flickered)
    app.setEffectEnabled(Qt.UIEffect.UI_AnimateTooltip, False)
    app.setEffectEnabled(Qt.UIEffect.UI_FadeTooltip, False)
    locale.setlocale(locale.LC_NUMERIC, "C")   # required by libmpv
    startup_log.mark("QApplication created")
    startup_log.cpu("QApplication created")

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
        window = MainWindow(preload=preload, probe=probe)
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

    flush = os.environ.get("QTC_UNCLOAK_FLUSH") == "1"     # E3 (T91) experiment, default off

    def uncloak(source: str = "first frame swapped") -> None:
        nonlocal uncloaked
        if not uncloaked:
            uncloaked = True
            if USE_D3D11 or flush:      # T91: wait until DWM has composed the swapped frame (≤ 1 vsync)
                theme.dwm_flush()
                startup_log.mark("DwmFlush before uncloak")
            theme.cloak(window, False)
            startup_log.mark(f"window uncloaked ({source})")
            if not paths:
                startup_log.finish("done: no file")

    def uncloak_timer() -> None:
        if not uncloaked:
            startup_log.mark("uncloak by 1 s safety timer (no frame yet)")
        uncloak("1 s safety timer")

    if USE_D3D11:
        # The video widget paints inside Qt's backing-store update. Wait until
        # that event returns and the complete window has been flushed to DWM.
        def show_initial_surface():
            video = window.video
            if not video._ready:
                return
            if paths and video.has_video and not video._first_frame_ready:
                return
            uncloak("Qt backing store flushed")

        window.video.surface_ready.connect(show_initial_surface, Qt.QueuedConnection)
        if paths:
            window.video._seek_done.connect(show_initial_surface, Qt.QueuedConnection)
        if startup_log.ENABLED and paths:
            def finish_native_trace():
                if uncloaked and window.video._startup_restart:
                    startup_log.finish("D3D11: window shown and mpv ready (screen timed externally)")

            window.video.surface_ready.connect(finish_native_trace, Qt.QueuedConnection)
            window.video._seek_done.connect(finish_native_trace, Qt.QueuedConnection)
    else:
        window.video.frameSwapped.connect(lambda: uncloak())
    QTimer.singleShot(1000, uncloak_timer)    # safety net if no frame arrives
    window.show()
    startup_log.mark("window.show()")
    startup_log.cpu("window.show()")
    if paths:
        window.open_path(Path(paths[0]), probe=probe)
        startup_log.mark("open_path returned")
    if gif:          # after open_path: it sets the default format (T25)
        window.set_format("gif")
    if startup_log.ENABLED:
        QTimer.singleShot(0, lambda: startup_log.mark("event loop running"))
        QTimer.singleShot(15000, lambda: startup_log.finish("gave up: 15 s without a video frame"))
    try:
        return app.exec()
    finally:
        if USE_D3D11:
            import shiboken6
            player = window.video.player
            window.video.close_composition()
            shiboken6.delete(app)
            player.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
