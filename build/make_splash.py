"""Renders assets/splash.png (shown by the exe bootloader while it unpacks)."""
import os
import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen

FILES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(FILES, "app"))
from icon import cube_pixmap  # noqa: E402

W, H = 560, 250

app = QGuiApplication([])
img = QImage(W, H, QImage.Format_RGB32)
img.fill(QColor("#1e1f24"))

p = QPainter(img)
p.setRenderHint(QPainter.Antialiasing)
p.setRenderHint(QPainter.SmoothPixmapTransform)
p.setPen(QPen(QColor("#3a3c44"), 2))
p.drawRect(1, 1, W - 2, H - 2)

p.drawPixmap(36, 40, cube_pixmap(112))

name_font = QFont("Segoe UI", 1)
name_font.setPixelSize(38)
name_font.setBold(True)
p.setFont(name_font)
p.setPen(QColor("#f0f0f0"))
p.drawText(QRectF(172, 52, W - 200, 50), Qt.AlignLeft | Qt.AlignVCenter, "3DConvert")

quote_font = QFont("Segoe UI", 1)
quote_font.setPixelSize(16)
quote_font.setItalic(True)
p.setFont(quote_font)
p.setPen(QColor("#9a9da8"))
p.drawText(QRectF(174, 104, W - 200, 30), Qt.AlignLeft | Qt.AlignVCenter, "Open file converter for Blender")
p.end()

os.makedirs(os.path.join(FILES, "assets"), exist_ok=True)
img.save(os.path.join(FILES, "assets", "splash.png"))
print("splash.png written")
