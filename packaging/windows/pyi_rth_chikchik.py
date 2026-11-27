"""F build 4 (pre-Python boot): PyInstaller runtime hook, passed by build.bat.

Custom runtime hooks run before PyInstaller's own ones, and `pyi_rth_pyside6` imports
PySide6.QtCore (Qt6Core.dll + shiboken) before app.py starts. Starting the startup log
and the OpenGL warm-up here lets the warm-up overlap that load too (before: it started
only after QtCore). gl_warm.start() is idempotent, app.py calls it again.

Hidden switch QTC_EARLY_WARM=0 — off (= before F build 4: both start in app.py).
Not used when running from source (python app.py).
"""
import os

if os.environ.get("QTC_EARLY_WARM", "").strip() != "0":
    os.environ.setdefault("QT_OPENGL", "desktop")   # same default as app.py
    from chikchik import startup_log

    startup_log.mark("boot: runtime hook (early warm)")
    from chikchik import gl_warm

    gl_warm.start()
