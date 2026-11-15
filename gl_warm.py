"""E2 build 2 (A): warm up OpenGL in a background thread while Python imports Qt.

On the user's PC the first `winId()` (native window + OpenGL driver load, ~290 ms) and
the GL render context (~250 ms) are the biggest startup costs. Loading opengl32.dll and
the driver's ICD here, in parallel with the imports, may take some of that off the main
thread. Invisible: the window is 1×1, never shown, destroyed in the same thread.

Hidden switch QTC_GL_WARM (Windows only):
  0 — off
  1 — load opengl32.dll + pick a pixel format on a hidden window (loads the driver ICD)
  2 / unset — 1 + create a dummy GL context, make it current, read GL_RENDERER, delete it
    (default since E2 build 2 bench: native window ~290 → ~195 ms; level 1 ~220)

No Qt imports: app.py starts this before PySide6.
"""
from __future__ import annotations

import os
import sys
import threading

import startup_log

_value = os.environ.get("QTC_GL_WARM", "").strip()
LEVEL = int(_value) if _value.isdigit() else 2


def _warm(level: int) -> None:
    import ctypes
    from ctypes import wintypes as wt

    class PFD(ctypes.Structure):                       # PIXELFORMATDESCRIPTOR, 40 bytes
        _fields_ = [("nSize", ctypes.c_uint16), ("nVersion", ctypes.c_uint16), ("dwFlags", ctypes.c_uint32),
                    ("iPixelType", ctypes.c_ubyte), ("cColorBits", ctypes.c_ubyte),
                    ("cRedBits", ctypes.c_ubyte), ("cRedShift", ctypes.c_ubyte),
                    ("cGreenBits", ctypes.c_ubyte), ("cGreenShift", ctypes.c_ubyte),
                    ("cBlueBits", ctypes.c_ubyte), ("cBlueShift", ctypes.c_ubyte),
                    ("cAlphaBits", ctypes.c_ubyte), ("cAlphaShift", ctypes.c_ubyte),
                    ("cAccumBits", ctypes.c_ubyte), ("cAccumRedBits", ctypes.c_ubyte),
                    ("cAccumGreenBits", ctypes.c_ubyte), ("cAccumBlueBits", ctypes.c_ubyte),
                    ("cAccumAlphaBits", ctypes.c_ubyte), ("cDepthBits", ctypes.c_ubyte),
                    ("cStencilBits", ctypes.c_ubyte), ("cAuxBuffers", ctypes.c_ubyte),
                    ("iLayerType", ctypes.c_ubyte), ("bReserved", ctypes.c_ubyte),
                    ("dwLayerMask", ctypes.c_uint32), ("dwVisibleMask", ctypes.c_uint32),
                    ("dwDamageMask", ctypes.c_uint32)]

    HGLRC = wt.HANDLE                                   # not in ctypes.wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    opengl32 = ctypes.WinDLL("opengl32", use_last_error=True)
    startup_log.mark("GL warm: opengl32.dll loaded")

    user32.CreateWindowExW.restype = wt.HWND
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
    user32.GetDC.restype = wt.HDC
    user32.GetDC.argtypes = [wt.HWND]
    user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    user32.DestroyWindow.argtypes = [wt.HWND]
    gdi32.ChoosePixelFormat.restype = ctypes.c_int
    gdi32.ChoosePixelFormat.argtypes = [wt.HDC, ctypes.POINTER(PFD)]
    gdi32.SetPixelFormat.restype = wt.BOOL
    gdi32.SetPixelFormat.argtypes = [wt.HDC, ctypes.c_int, ctypes.POINTER(PFD)]
    opengl32.wglCreateContext.restype = HGLRC
    opengl32.wglCreateContext.argtypes = [wt.HDC]
    opengl32.wglMakeCurrent.restype = wt.BOOL
    opengl32.wglMakeCurrent.argtypes = [wt.HDC, HGLRC]
    opengl32.wglDeleteContext.argtypes = [HGLRC]
    opengl32.glGetString.restype = ctypes.c_char_p
    opengl32.glGetString.argtypes = [ctypes.c_uint]

    WS_POPUP = 0x80000000
    hwnd = user32.CreateWindowExW(0, "STATIC", "ChikChik GL warm-up", WS_POPUP,
                                  0, 0, 1, 1, None, None, None, None)
    if not hwnd:
        startup_log.mark("GL warm: no window")
        return
    hdc = user32.GetDC(hwnd)
    try:
        pfd = PFD()
        pfd.nSize = ctypes.sizeof(PFD)
        pfd.nVersion = 1
        pfd.dwFlags = 0x4 | 0x20 | 0x1          # DRAW_TO_WINDOW | SUPPORT_OPENGL | DOUBLEBUFFER
        pfd.cColorBits, pfd.cAlphaBits, pfd.cDepthBits, pfd.cStencilBits = 32, 8, 24, 8
        index = gdi32.ChoosePixelFormat(hdc, ctypes.byref(pfd))
        startup_log.mark(f"GL warm: pixel format {index} (driver loaded)")
        if level < 2 or not index or not gdi32.SetPixelFormat(hdc, index, ctypes.byref(pfd)):
            return
        ctx = opengl32.wglCreateContext(hdc)
        if not ctx:
            startup_log.mark("GL warm: no context")
            return
        try:
            if opengl32.wglMakeCurrent(hdc, ctx):
                renderer = opengl32.glGetString(0x1F01) or b"?"          # GL_RENDERER
                startup_log.mark(f"GL warm: context current · {renderer.decode(errors='replace')}")
                opengl32.wglMakeCurrent(None, None)
        finally:
            opengl32.wglDeleteContext(ctx)
    finally:
        user32.ReleaseDC(hwnd, hdc)
        user32.DestroyWindow(hwnd)


def _run(level: int) -> None:
    try:
        _warm(level)
        startup_log.mark("GL warm: done")
    except Exception as exc:                     # never let the warm-up hurt the start
        startup_log.mark(f"GL warm: failed · {exc}")


D3D9 = os.environ.get("QTC_D3D9_WARM") == "1"     # E4 experiment, default off


def _warm_d3d9() -> None:
    """E4 (T95): Qt's first winId asks Direct3D 9 for the GPU (Direct3DCreate9 +
    GetAdapterIdentifier → driver blacklist check, ~56 ms on the main thread even with
    QT_OPENGL=desktop). Doing it once here loads d3d9.dll and the driver's D3D part early."""
    import ctypes

    try:
        d3d9 = ctypes.WinDLL("d3d9")
        d3d9.Direct3DCreate9.restype = ctypes.c_void_p
        d3d9.Direct3DCreate9.argtypes = [ctypes.c_uint]
        obj = d3d9.Direct3DCreate9(32)                      # D3D_SDK_VERSION
        if not obj:
            startup_log.mark("D3D9 warm: no object")
            return
        vtable = ctypes.cast(ctypes.c_void_p.from_address(obj).value,
                             ctypes.POINTER(ctypes.c_void_p))
        identify = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint,
                                      ctypes.c_uint32, ctypes.c_void_p)(vtable[5])
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
        buffer = ctypes.create_string_buffer(2048)          # D3DADAPTER_IDENTIFIER9 ~1104
        identify(obj, 0, 0, buffer)
        release(obj)
        startup_log.mark("D3D9 warm: done")
    except Exception as exc:
        startup_log.mark(f"D3D9 warm: failed · {exc}")


def start() -> None:
    if sys.platform != "win32":
        return
    if D3D9:
        threading.Thread(target=_warm_d3d9, name="d3d9-warm", daemon=True).start()
        startup_log.mark("D3D9 warm: thread started")
    if LEVEL <= 0:
        return
    threading.Thread(target=_run, args=(LEVEL,), name="gl-warm", daemon=True).start()
    startup_log.mark(f"GL warm: thread started (level {LEVEL})")
