"""Blender default cube icon, drawn at runtime so no image file is needed."""

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def cube_pixmap(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    cx = cy = size / 2
    r = size * 0.44
    w = r * 0.866
    top = QPointF(cx, cy - r)
    ur = QPointF(cx + w, cy - r / 2)
    lr = QPointF(cx + w, cy + r / 2)
    bot = QPointF(cx, cy + r)
    ll = QPointF(cx - w, cy + r / 2)
    ul = QPointF(cx - w, cy - r / 2)
    mid = QPointF(cx, cy)

    faces = [
        ([top, ur, mid, ul], "#a6a6a6"),
        ([ul, mid, bot, ll], "#7a7a7a"),
        ([mid, ur, lr, bot], "#575757"),
    ]
    edge = QPen(QColor("#2b2b2b"), max(1.0, size / 64))
    for pts, color in faces:
        p.setBrush(QColor(color))
        p.setPen(edge)
        p.drawPolygon(QPolygonF(pts))

    # Selected-object orange outline, as on Blender's startup cube
    outline = QPen(QColor("#ff9a1f"), max(1.5, size / 22))
    outline.setJoinStyle(Qt.RoundJoin)
    p.setBrush(Qt.NoBrush)
    p.setPen(outline)
    p.drawPolygon(QPolygonF([top, ur, lr, bot, ll, ul]))
    p.end()
    return pm


def cube_icon() -> QIcon:
    icon = QIcon()
    for s in ICON_SIZES:
        icon.addPixmap(cube_pixmap(s))
    return icon
