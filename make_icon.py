"""Render the ✂️ emoji (Segoe UI Emoji) into icon.ico for the .exe and the menu item."""
import sys

from PySide6.QtWidgets import QApplication

import theme

app = QApplication(sys.argv)
pixmap = theme.emoji_icon("✂️", 256).pixmap(256, 256)
ok = pixmap.save("icon.ico", "ICO")
print("icon.ico written" if ok else "icon.ico FAILED")
sys.exit(0 if ok else 1)
