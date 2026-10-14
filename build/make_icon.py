"""Writes assets/icon.ico (PNG-compressed, multi-size) for the exe build."""
import os
import struct
import sys

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QGuiApplication

FILES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(FILES, "app"))
from icon import ICON_SIZES, cube_pixmap  # noqa: E402

app = QGuiApplication([])

images = []
for size in ICON_SIZES:
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    cube_pixmap(size).save(buf, "PNG")
    images.append((size, bytes(buf.data())))

header = struct.pack("<HHH", 0, 1, len(images))
offset = 6 + 16 * len(images)
entries, blobs = b"", b""
for size, data in images:
    dim = 0 if size >= 256 else size
    entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
    blobs += data
    offset += len(data)

os.makedirs(os.path.join(FILES, "assets"), exist_ok=True)
with open(os.path.join(FILES, "assets", "icon.ico"), "wb") as f:
    f.write(header + entries + blobs)
print("icon.ico written")
