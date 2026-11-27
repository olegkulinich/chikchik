"""Present mpv's DXGI swap chain in a Win32 DirectComposition visual."""
import ctypes
from ctypes import wintypes
from uuid import UUID


_PTR = ctypes.c_void_p
_OUT = ctypes.POINTER(_PTR)
_IID_DEVICE = (ctypes.c_ubyte * 16).from_buffer_copy(
    UUID("c37ea93a-e7aa-450d-b16f-9746cb0407f3").bytes_le
)


def _call(obj, slot, result, argtypes, *args):
    table = ctypes.cast(obj, ctypes.POINTER(_OUT)).contents
    method = ctypes.WINFUNCTYPE(result, _PTR, *argtypes)(table[slot])
    return method(obj, *args)


def _check(result, operation):
    if result < 0:
        raise OSError(f"{operation} failed (HRESULT 0x{result & 0xffffffff:08x})")


class CompositionTarget:
    def __init__(self, hwnd):
        self.device = _PTR()
        self.target = _PTR()
        self.visual = _PTR()
        self._swapchain = None
        create = ctypes.windll.dcomp.DCompositionCreateDevice
        create.argtypes = [_PTR, _PTR, _OUT]
        create.restype = ctypes.c_long
        try:
            _check(create(None, _IID_DEVICE, ctypes.byref(self.device)),
                   "DCompositionCreateDevice")
            _check(_call(self.device, 6, ctypes.c_long, (wintypes.HWND, wintypes.BOOL, _OUT),
                         hwnd, True, ctypes.byref(self.target)), "CreateTargetForHwnd")
            _check(_call(self.device, 7, ctypes.c_long, (_OUT,), ctypes.byref(self.visual)),
                   "CreateVisual")
            _check(_call(self.target, 3, ctypes.c_long, (_PTR,), self.visual), "SetRoot")
        except OSError:
            self.close()
            raise

    def set_swapchain(self, swapchain):
        if swapchain == self._swapchain:
            return
        _check(_call(self.visual, 15, ctypes.c_long, (_PTR,), swapchain), "SetContent")
        _check(_call(self.device, 3, ctypes.c_long, ()), "Commit")
        self._swapchain = swapchain

    def close(self):
        try:
            if self._swapchain is not None:
                self.set_swapchain(None)
        finally:
            for obj in (self.visual, self.target, self.device):
                if obj:
                    _call(obj, 2, wintypes.ULONG, ())  # IUnknown::Release
                    obj.value = None
