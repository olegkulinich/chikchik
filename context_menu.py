"""T30: the Explorer right-click items "ChikChik!" and "ChikChik! → GIF" (HKCU, no admin).

Used by Settings → Explorer Menu (applied at once) and from the command line in dev:
    python context_menu.py install | uninstall
The installer adds both on the first install, leaves the user's choice alone on updates
and removes them on uninstall.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ENABLED = os.name == "nt"
if ENABLED:
    import winreg

BASE_DIR = Path(__file__).resolve().parent
EXTENSIONS = (".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm", ".mts", ".ts")
ROOT = r"Software\Classes\SystemFileAssociations"
# id → (registry key, title, extra argument). The old key names stay: existing installs
# keep working, and Explorer sorts verbs by key name, so the two items stay together.
ITEMS = {
    "main": ("QuickTrimCrop", "ChikChik!", ""),
    "gif": ("QuickTrimCropGIF", "ChikChik! → GIF", "--gif"),
}


def _program() -> tuple[str, Path]:
    """(command prefix, folder with the icons)."""
    if getattr(sys, "frozen", False):                      # installed ChikChik.exe
        exe = Path(sys.executable)
        return f'"{exe}"', exe.parent
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else python
    return f'"{interpreter}" "{BASE_DIR / "app.py"}"', BASE_DIR


def _icon(item: str) -> str:
    _, folder = _program()
    frozen = getattr(sys, "frozen", False)
    icons = folder / "icons" if frozen else folder / "assets" / "icons"
    if item == "gif":
        for candidate in (folder / "gif.ico", icons / "gif.ico"):
            if candidate.exists():
                return str(candidate)
    if frozen:
        return f"{sys.executable},0"
    return str(icons / "icon.ico")


def is_installed(item: str) -> bool:
    if not ENABLED:
        return False
    key_name = ITEMS[item][0]
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{ROOT}\.mp4\shell\{key_name}").Close()
        return True
    except OSError:
        return False


def _delete_tree(root, path: str) -> None:
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(root, f"{path}\\{child}")
        winreg.DeleteKey(root, path)
    except FileNotFoundError:
        pass


def set_installed(item: str, on: bool) -> None:
    """Add or remove one item for all video extensions; Explorer picks it up at once."""
    if not ENABLED:
        return
    key_name, title, flag = ITEMS[item]
    prefix, _folder = _program()
    command = f'{prefix}{" " + flag if flag else ""} "%1"'
    icon = _icon(item)
    for extension in EXTENSIONS:
        key_path = rf"{ROOT}\{extension}\shell\{key_name}"
        if not on:
            _delete_tree(winreg.HKEY_CURRENT_USER, key_path)
            continue
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, title)
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, icon)
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path + r"\command", 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
    _notify_explorer()


def _notify_explorer() -> None:
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)   # SHCNE_ASSOCCHANGED
    except Exception:
        pass


if __name__ == "__main__":
    if not ENABLED:
        raise SystemExit("Windows only")
    if len(sys.argv) != 2 or sys.argv[1] not in ("install", "uninstall"):
        raise SystemExit("Usage:\n  python context_menu.py install\n  python context_menu.py uninstall")
    for name in ITEMS:
        set_installed(name, sys.argv[1] == "install")
    print("Done:", sys.argv[1], " ".join(EXTENSIONS))
