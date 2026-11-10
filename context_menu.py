"""Add / remove 'Trim / Crop' and 'Trim / Crop → GIF' in the Explorer right-click menu (HKCU, no admin)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

if os.name != "nt":
    raise SystemExit("Windows only")

import winreg

BASE_DIR = Path(__file__).resolve().parent
EXTENSIONS = (".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm", ".mts", ".ts")
# Key names keep the two items next to each other (Explorer sorts verbs by key name).
MENU_ITEMS = (("QuickTrimCrop", "Trim / Crop", ""), ("QuickTrimCropGIF", "Trim / Crop → GIF", "--gif"))
ROOT = r"Software\Classes\SystemFileAssociations"


def target(flag: str = "") -> tuple[str, str]:
    extra = f" {flag}" if flag else ""
    packaged = BASE_DIR / "dist" / "ChikChik" / "ChikChik.exe"
    if packaged.exists():
        return f'"{packaged}"{extra} "%1"', str(packaged)
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else python
    return f'"{interpreter}" "{BASE_DIR / "app.py"}"{extra} "%1"', str(BASE_DIR / "icon.ico")


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


def install() -> None:
    for name, title, flag in MENU_ITEMS:
        command, icon = target(flag)
        for extension in EXTENSIONS:
            key_path = rf"{ROOT}\{extension}\shell\{name}"
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, title)
                if Path(icon).exists():
                    winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, icon)
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path + r"\command", 0,
                                    winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
        print(f"Added '{title}':", command)
    print("For:", " ".join(EXTENSIONS))


def uninstall() -> None:
    for name, title, _flag in MENU_ITEMS:
        for extension in EXTENSIONS:
            _delete_tree(winreg.HKEY_CURRENT_USER, rf"{ROOT}\{extension}\shell\{name}")
        print(f"Removed '{title}'.")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("install", "uninstall"):
        raise SystemExit("Usage:\n  python context_menu.py install\n  python context_menu.py uninstall")
    install() if sys.argv[1] == "install" else uninstall()
