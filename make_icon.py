# make_icon.py
# Run once before building with PyInstaller to generate claude_usage_bar.ico.
# Usage: python make_icon.py

import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap, QPainter, QColor, QBrush, QIcon
from PySide6.QtCore import Qt

app = QApplication.instance() or QApplication(sys.argv)

def draw_icon(size):
    px = QPixmap(size, size)
    px.fill(QColor(0, 0, 0, 0))
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    r = max(3, size // 6)
    p.setBrush(QBrush(QColor(28, 28, 32)))
    p.drawRoundedRect(0, 0, size, size, r, r)
    m = max(2, size // 8)
    bw = size - 2 * m
    bh = max(2, size // 6)
    track = QColor(70, 70, 78)
    fill = QColor(217, 119, 87)
    y1 = size // 3 - bh // 2
    p.setBrush(QBrush(track))
    p.drawRoundedRect(m, y1, bw, bh, bh // 2, bh // 2)
    p.setBrush(QBrush(fill))
    p.drawRoundedRect(m, y1, int(bw * 0.6), bh, bh // 2, bh // 2)
    y2 = (2 * size) // 3 - bh // 2
    p.setBrush(QBrush(track))
    p.drawRoundedRect(m, y2, bw, bh, bh // 2, bh // 2)
    p.setBrush(QBrush(fill))
    p.drawRoundedRect(m, y2, int(bw * 0.35), bh, bh // 2, bh // 2)
    p.end()
    return px

# Save 32x32 as ICO (single-size is sufficient for PyInstaller on Windows)
px = draw_icon(32)
saved = px.save("claude_usage_bar.ico")
if saved:
    print("claude_usage_bar.ico written.")
else:
    print("ICO save failed — try saving as PNG and converting manually.")
    px.save("claude_usage_bar.png")
    print("claude_usage_bar.png written instead.")
