import os
import re
import sys
from html import escape

try:
    import pyi_splash  # only available inside the PyInstaller exe
except ImportError:
    pyi_splash = None


def close_splash():
    if pyi_splash:
        try:
            pyi_splash.close()
        except Exception:  # noqa: BLE001
            pass


if pyi_splash:
    try:
        pyi_splash.update_text("Loading...")  # plain ASCII: the splash font has no block glyphs
    except Exception:  # noqa: BLE001
        pass

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from formats import EXPORT_HINTS, NATIVE_FORMATS, PROGRAMS, SUPPORTED, program_status
from icon import cube_icon, cube_pixmap
from pipeline import ConvertError, Converter, blender_status, cancel_scan, steps_for, unique_path
from scanners import describe_file

APP_NAME = "3DConvert"
APP_VERSION = "v1.10"  # bump with every edit, together with LAST_UPDATE
LAST_UPDATE = "2026-10-14"


def format_note(ext):
    """(extension, one-line description) shown under the file name in the drop zone."""
    if ext not in SUPPORTED:
        return ext or "unknown", "unsupported file format"
    if ext in NATIVE_FORMATS:
        return ext, "native Blender file input, cleaning only"
    return ext, "non-native file input, conversion + cleaning"


COL_W = 130  # shared width of the status pill, side buttons and Create button
TITLE_H = 40  # title bar height; its centre line carries logo, title, tagline and window buttons
LOGO_PX = 26
BTN_W = 22  # title bar button width
GROUP_W = 5 * BTN_W + 4 * 4  # file, cog, i, minimise, close
TITLE_RIGHT = 18 + COL_W // 2 - GROUP_W // 2  # window buttons are centred above the Clear data button

STYLE = """
#root { background: #1e1f24; border: 1px solid #3a3c44; }
#titleBar { background: #17181c; }
#titleLabel { color: #e6e6e6; font-size: 19px; font-weight: 600; }
QPushButton#winBtn, QPushButton#closeBtn, QPushButton#infoBtn, QPushButton#cogBtn, QPushButton#fileBtn { background: #3a3d46;
    color: #e6e6e6; border: none; border-radius: 5px; font-size: 12px; padding: 0;
    min-width: 22px; max-width: 22px; min-height: 20px; max-height: 20px; }
QPushButton#infoBtn { font-weight: 700; font-style: italic; }
QPushButton#winBtn:hover, QPushButton#infoBtn:hover, QPushButton#cogBtn:hover, QPushButton#fileBtn:hover { background: #4a4d58; }
QPushButton#infoBtn:checked, QPushButton#cogBtn:checked, QPushButton#fileBtn:checked { background: #ff9a1f; color: #1e1f24; }
QPushButton#closeBtn:hover { background: #c42b1c; color: white; }
QProgressBar#sizeBar { background: #34363e; border-radius: 4px; max-height: 8px; min-height: 8px; }
#footer { color: #6d707a; font-size: 11px; }
QProgressBar#sizeBar::chunk { background: #ff9a1f; border-radius: 4px; }
#tagline { color: #6d707a; font-size: 12px; font-style: italic; }
#version { color: #6d707a; }
#dropTitle { color: #f0f0f0; background: transparent; }
#dropSub { color: #9a9da8; background: transparent; }
#dropHint { color: #6d707a; font-size: 12px; background: transparent; }
#infoBox { background: #26282e; border-radius: 8px; }
#infoHeader { color: #f0f0f0; font-size: 14px; font-weight: 600; }
#infoScroll, #infoInner { background: transparent; border: none; }
#infoKey { color: #8b8f9a; font-size: 12px; }
#infoVal { color: #f4f4f6; font-size: 12px; }
#infoMsg { color: #8b8f9a; font-size: 12px; }
QScrollBar:vertical { background: #1e1f24; width: 10px; border-radius: 5px; margin: 0; }
QScrollBar::handle:vertical { background: #6a6e7b; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #8a8fa0; }
QScrollBar:vertical:disabled { background: #25272c; }
QScrollBar::handle:vertical:disabled { background: #34363e; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QPushButton#clearBtn { background: #3a3d46; color: #e6e6e6; border: none;
    border-radius: 6px; font-size: 12px; }
QPushButton#clearBtn:hover { background: #4a4d58; }
QPushButton#clearBtn:disabled { background: #2c2e35; color: #6d707a; }
QPushButton#miniBtn { background: #3a3d46; color: #e6e6e6; border: none; border-radius: 5px; padding: 0 8px;
    font-size: 11px; min-height: 22px; max-height: 22px; }
QPushButton#miniBtn:hover { background: #4a4d58; }
QPushButton#navBtn { background: transparent; color: #e6e6e6; border: none; border-radius: 4px; padding: 0;
    font-size: 13px; font-weight: 700; min-width: 16px; max-width: 16px; min-height: 18px; max-height: 18px; }
QPushButton#navBtn:hover { background: #3a3d46; }
QPushButton#navBtn:disabled { color: #4a4d58; }
QPushButton#modeLeft, QPushButton#modeRight { background: #3a3d46; color: #b5b8c2; border: none; padding: 0;
    font-size: 11px; min-height: 24px; max-height: 24px; }
QPushButton#modeLeft { border-top-left-radius: 6px; border-bottom-left-radius: 6px; }
QPushButton#modeRight { border-top-right-radius: 6px; border-bottom-right-radius: 6px; }
QPushButton#modeLeft:hover, QPushButton#modeRight:hover { background: #4a4d58; }
QPushButton#modeLeft:checked, QPushButton#modeRight:checked { background: #ff9a1f; color: #1e1f24; font-weight: 600; }
QPushButton#modeLeft:disabled, QPushButton#modeRight:disabled { background: #2c2e35; color: #6d707a; }
QPushButton#modeLeft:checked:disabled, QPushButton#modeRight:checked:disabled { background: #6b4a1a; color: #a08a66; }
#outPath { font-size: 12px; color: #8b8f9a; }
QCheckBox { color: #d0d0d0; font-size: 12px; spacing: 6px; }
QCheckBox::indicator { width: 14px; height: 14px; border-radius: 3px; border: 1px solid #6a6e7b;
    background: #26282e; }
QCheckBox::indicator:checked { background: #ff9a1f; border-color: #9a9da8; }
QCheckBox:disabled { color: #6d707a; }
QCheckBox#stepCheck { color: #f4f4f6; }
QPushButton#convertBtn { background: #ff9a1f; color: #1e1f24; border: none;
    border-radius: 8px; font-size: 13px; font-weight: 600; }
QPushButton#convertBtn:hover { background: #ffae45; }
QPushButton#convertBtn:disabled { background: #34363e; color: #6d707a; }
QProgressBar { background: #26282e; border: none; border-radius: 6px; height: 14px;
    text-align: center; color: white; font-size: 10px; }
QProgressBar::chunk { background: #ff9a1f; border-radius: 6px; }
"""

TONES = {
    "idle": ("#3a3c44", "#b5b8c2"),
    "busy": ("#3a3c44", "#e6e6e6"),
    "ok": ("#ff9a1f", "#ffc477"),
    "error": ("#e5534b", "#ff8b84"),
}


class StatusPill(QWidget):
    """Pill-shaped status badge; a light segment circles the border while busy."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(COL_W, 50)
        self._text = "Idle"
        self._tone = "idle"
        self._offset = 0.0
        self._timer = QTimer(self, interval=16)
        self._timer.timeout.connect(self._tick)

    def set_status(self, text: str, tone: str = "idle", busy: bool = False):
        self._text = text
        self._tone = "busy" if busy else tone
        if busy:
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _tick(self):
        self._offset += 0.35
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        ring, text_color = TONES[self._tone]
        pen_w = 3
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(rect, rect.height() / 2, rect.height() / 2)

        p.setPen(QPen(QColor(ring), pen_w))
        p.drawPath(path)

        if self._timer.isActive():
            length = path.length() / pen_w
            seg = length * 0.28
            pen = QPen(QColor("#ff9a1f"), pen_w)
            pen.setCapStyle(Qt.FlatCap)
            pen.setDashPattern([seg, length - seg])
            pen.setDashOffset(-self._offset)
            p.setPen(pen)
            p.drawPath(path)

        font = QFont(self.font())
        font.setBold(True)
        size = 14
        avail = rect.width() - 20
        while size > 6:
            font.setPixelSize(size)
            if QFontMetrics(font).horizontalAdvance(self._text) <= avail:
                break
            size -= 1
        font.setPixelSize(size)
        p.setFont(font)
        p.setPen(QColor(text_color))
        p.drawText(rect, Qt.AlignCenter, self._text)


class StepList(QWidget):
    """Greyed-out conversion steps, one per line; the running step is highlighted and 'Done' stays lit."""

    GREY, ACTIVE, DONE, FAIL = "#6d707a", "#ff9a1f", "#ff9a1f", "#ff8b84"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._names = []
        self._labels = []
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 4, 0, 0)
        self._lay.setSpacing(3)
        self._lay.addStretch(1)

    def set_steps(self, names):
        while self._labels:
            label = self._labels.pop()
            self._lay.removeWidget(label)
            label.deleteLater()
        self._names = list(names)
        for name in self._names:
            label = QLabel(name)
            label.setAlignment(Qt.AlignHCenter)
            self._style(label, self.GREY, False)
            self._lay.insertWidget(len(self._labels), label)
            self._labels.append(label)
        self.setToolTip("")

    @staticmethod
    def _style(label, color, bold):
        label.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: {600 if bold else 400};")

    def set_active(self, name):
        for i, label in enumerate(self._labels):
            if self._names[i] == name:
                self._style(label, self.DONE if name == "Done" else self.ACTIVE, True)
            else:
                self._style(label, self.GREY, False)

    def set_failed(self, tip=""):
        for label in self._labels:
            if "font-weight: 600" in label.styleSheet():
                self._style(label, self.FAIL, True)
        self.setToolTip(tip)


class DropZone(QFrame):
    filesChosen = Signal(list)
    TITLE = "Drop file(s) here / click to navigate to file(s)"
    GREY_TEXT = "color: #5a5d68;"
    IDLE_TITLE = "color: #b0b3bd;"  # the 'Drop file(s) here' message, a little greyer than a file name
    IDLE_TITLE = "color: #b0b3bd;"  # the 'Drop file(s) here' message, a little greyer than a file name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(96)
        self._hover = False
        self._error = False
        self._greyed = False
        self._idle = True  # no file shown: the invitation text is on

        self._title_text = ""
        self.title = QLabel()
        self.title.setObjectName("dropTitle")
        self._set_title(self.TITLE)
        self.sub = QLabel()
        self.sub.setObjectName("dropSub")
        sub_font = self.sub.font()
        sub_font.setPixelSize(12)
        self.sub.setFont(sub_font)
        self._sub_plain = ""
        self.sub.setWordWrap(False)  # the note must stay on one line; the font shrinks instead
        self.sub.setAlignment(Qt.AlignCenter)
        self.sub.hide()

        self.hint = QWidget()  # shown while no file is loaded: Check [file icon] for supported file formats
        hint_lay = QHBoxLayout(self.hint)
        hint_lay.setContentsMargins(0, 0, 0, 0)
        hint_lay.setSpacing(5)
        hint_lay.setAlignment(Qt.AlignCenter)
        self.hint_labels = [QLabel("Check"), QLabel("for supported file formats")]
        self.hint_icon = QLabel()
        self.hint_icon.setFixedSize(14, 14)
        for w in (self.hint_labels[0], self.hint_icon, self.hint_labels[1]):
            hint_lay.addWidget(w)
        for label in self.hint_labels:
            label.setObjectName("dropHint")
        self._paint_hint_icon(False)
        self.title.setStyleSheet(self._title_style())
        for w in (self.title, self.sub, self.hint, self.hint_icon, *self.hint_labels):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setContentsMargins(14, 6, 14, 6)
        lay.addWidget(self.title, alignment=Qt.AlignCenter)
        lay.addWidget(self.sub)
        lay.addWidget(self.hint)

    def _title_style(self):
        if self._greyed:
            return self.GREY_TEXT
        return self.IDLE_TITLE if self._idle else ""

    def _paint_hint_icon(self, greyed: bool):
        self.hint_icon.setPixmap(file_pixmap(14, "#4a4d58" if greyed else "#6d707a"))

    def set_greyed(self, greyed: bool):
        """Greyed out while Blender 5.2 is missing (the widget is disabled by the caller)."""
        if greyed == self._greyed:
            return
        self._greyed = greyed
        self.setCursor(Qt.ArrowCursor if greyed else Qt.PointingHandCursor)
        self.title.setStyleSheet(self._title_style())
        self.sub.setStyleSheet(self.GREY_TEXT if greyed else "")
        for label in self.hint_labels:
            label.setStyleSheet(self.GREY_TEXT if greyed else "")
        self._paint_hint_icon(greyed)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._greyed:
            color, fill = "#44464f", "#222429"
        elif self._error:
            color, fill = "#e5534b", "#26282e"
        elif self._hover:
            color, fill = "#ff9a1f", "#33291d"
        else:
            color, fill = "#7a7e8c", "#26282e"
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setBrush(QColor(fill))
        pen = QPen(QColor(color), 3)
        pen.setDashPattern([4, 2.5])
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        p.drawRoundedRect(rect, 8, 8)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._hover = True
            self.update()

    def dragLeaveEvent(self, _):
        self._hover = False
        self.update()

    def dropEvent(self, e):
        self._hover = False
        self.update()
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.filesChosen.emit(paths)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            exts = " ".join(f"*{x}" for x in SUPPORTED)
            paths, _ = QFileDialog.getOpenFileNames(
                self, "Select 3D file(s)", "", f"3D files ({exts});;All files (*.*)"
            )
            if paths:
                self.filesChosen.emit(paths)

    def _set_title(self, text):
        self._title_text = text
        self._fit_title()

    def _fit_title(self):
        """Largest font (18 px down to 9 px) that fits the drop zone; elided only below that."""
        font = QFont(self.title.font())
        font.setWeight(QFont.DemiBold)
        avail = max(self.width() - 28, 120)
        px = 18
        while px > 9:
            font.setPixelSize(px)
            if QFontMetrics(font).horizontalAdvance(self._title_text) <= avail:
                break
            px -= 1
        font.setPixelSize(px)
        self.title.setFont(font)
        self.title.setText(QFontMetrics(font).elidedText(self._title_text, Qt.ElideMiddle, avail))

    def _set_sub(self, plain, html=None):
        self._sub_plain = plain
        self.sub.setTextFormat(Qt.RichText if html else Qt.PlainText)
        self.sub.setText(html or plain)
        self._fit_sub()

    def _fit_sub(self):
        """Largest font (12 px down to 9 px) at which every line fits the drop zone."""
        font = QFont(self.sub.font())
        lines = self._sub_plain.split("\n")
        avail = max(self.width() - 28, 120)
        for px in range(12, 8, -1):
            font.setPixelSize(px)
            fm = QFontMetrics(font)
            if max(fm.horizontalAdvance(line) for line in lines) <= avail:
                break
        self.sub.setFont(font)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_title()
        self._fit_sub()

    def show_file(self, name: str, ext: str, kind: str):
        self._set_title(name)
        gap = "&nbsp;" * 5
        self._set_sub(f"{ext}     {kind}", f'<span style="color:#ff9a1f">{escape(ext)}</span>{gap}{escape(kind)}')
        self.sub.show()
        self.hint.hide()
        self._idle = False
        self.title.setStyleSheet(self._title_style())
        self._error = False
        self.update()

    def show_error(self):
        self._error = True
        self.update()

    def reset(self):
        self._set_title(self.TITLE)
        self.sub.hide()
        self.hint.show()
        self._idle = True
        self.title.setStyleSheet(self._title_style())
        self._error = False
        self.update()


class ElidedLabel(QLabel):
    """Single-line label that shortens the middle of long text to fit its width."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = ""
        self.setMinimumWidth(0)

    def set_full_text(self, text: str):
        self._full = text
        self._elide()

    def _elide(self):
        self.setText(QFontMetrics(self.font()).elidedText(self._full, Qt.ElideMiddle, self.width()))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()


def folder_icon() -> QIcon:
    pm = QPixmap(36, 36)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#e6e6e6"))
    p.drawRoundedRect(QRectF(3, 7, 14, 8), 2, 2)
    p.drawRoundedRect(QRectF(3, 11, 30, 19), 3, 3)
    p.end()
    return QIcon(pm)


class FileNav(QWidget):
    """'File < 3 / 8 >' with two small clickable arrows; hidden while no file is loaded."""

    step = Signal(int)  # -1 = previous file, +1 = next file

    def __init__(self, parent=None):
        super().__init__(parent)
        caption = QLabel("File")
        caption.setObjectName("infoKey")
        self.count = QLabel()
        self.count.setObjectName("infoVal")
        self.prev, self.next = QPushButton("<"), QPushButton(">")
        for btn, d, tip in ((self.prev, -1, "Previous file"), (self.next, 1, "Next file")):
            btn.setObjectName("navBtn")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _=False, d=d: self.step.emit(d))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        for w in (caption, self.prev, self.count, self.next):
            lay.addWidget(w)
        self.hide()

    def set_state(self, index: int, total: int, enabled=True):
        self.setVisible(total > 0)
        self.count.setText(f"{index} / {total}")
        for btn in (self.prev, self.next):
            btn.setEnabled(enabled and total > 1)


class InfoPanel(QFrame):
    """Two-column (label / value) list inside a scroll area."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("infoBox")

        header = QLabel("Imported file information")
        header.setObjectName("infoHeader")
        self.nav = FileNav()
        self.trash = QPushButton()
        self.trash.setObjectName("navBtn")
        self.trash.setCursor(Qt.PointingHandCursor)
        self.trash.setToolTip("Remove this file from the list")
        trash_icon = QIcon()
        trash_icon.addPixmap(trash_pixmap(14, "#e6e6e6"), QIcon.Normal)
        trash_icon.addPixmap(trash_pixmap(14, "#4a4d58"), QIcon.Disabled)
        self.trash.setIcon(trash_icon)
        self.trash.setIconSize(QSize(14, 14))
        self.trash.hide()
        head_row = QHBoxLayout()
        head_row.addWidget(header)
        head_row.addStretch(2)  # free space between the title and the trash icon: 2/3 left of the file switch, 1/3 right
        head_row.addWidget(self.nav)
        head_row.addStretch(1)
        head_row.addWidget(self.trash)
        self.inner = QWidget()
        self.inner.setObjectName("infoInner")
        self.grid = QGridLayout(self.inner)
        self.grid.setContentsMargins(0, 0, 6, 0)
        self.grid.setHorizontalSpacing(18)
        self.grid.setVerticalSpacing(6)
        self.grid.setColumnMinimumWidth(0, 130)
        self.grid.setColumnStretch(1, 1)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("infoScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.scroll.setWidget(self.inner)
        self.scroll.viewport().setStyleSheet("background: transparent;")

        bar = self.scroll.verticalScrollBar()
        bar.rangeChanged.connect(lambda lo, hi: bar.setEnabled(hi > lo))
        bar.setEnabled(False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 10, 12)
        lay.setSpacing(8)
        lay.addLayout(head_row)
        lay.addWidget(self.scroll, 1)
        self.set_message("No file loaded.")

    def set_nav(self, index: int, total: int, enabled=True):
        self.nav.set_state(index, total, enabled)
        self.trash.setVisible(total > 0)
        self.trash.setEnabled(enabled)

    def _clear(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.grid.setRowStretch(self.grid.rowCount(), 0)

    def set_message(self, text: str):
        self._clear()
        msg = QLabel(text)
        msg.setObjectName("infoMsg")
        msg.setWordWrap(True)
        self.grid.addWidget(msg, 0, 0, 1, 2, Qt.AlignTop)
        self.grid.setRowStretch(1, 1)

    def set_rows(self, rows):
        self._clear()
        for r, (key, val) in enumerate(rows):
            k = QLabel(key)
            k.setObjectName("infoKey")
            v = QLabel(val)
            v.setObjectName("infoVal")
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.grid.addWidget(k, r, 0, Qt.AlignTop)
            self.grid.addWidget(v, r, 1, Qt.AlignTop)
        self.grid.setRowStretch(len(rows), 1)
        self.scroll.verticalScrollBar().setValue(0)


class _Interrupted(Exception):
    """Raised from the progress callback to stop a file read that is no longer wanted."""


class ReadWorker(QThread):
    progress = Signal(int)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, path):
        super().__init__()
        self.path = path

    def _report(self, pct):
        if self.isInterruptionRequested():
            raise _Interrupted
        self.progress.emit(pct)

    def run(self):
        try:
            self.done.emit(describe_file(self.path, self._report))
        except _Interrupted:
            pass
        except Exception as ex:  # noqa: BLE001 - surface any read error to the UI
            self.failed.emit(str(ex))


class ConvertWorker(QThread):
    progress = Signal(int, str)
    step = Signal(str)
    file_started = Signal(int, int, str)  # index, total, source path
    done = Signal(list, list)  # saved .blend paths, [(file name, message)] of the files that failed

    def __init__(self, sources, folders, combined, cleanup_off=()):
        super().__init__()
        self.sources = list(sources)
        self.folders = list(folders)  # output folder per source (combined: the first one is used)
        self.combined = combined
        self.converter = Converter(self.progress.emit, self.step.emit, cleanup_off)

    def run(self):
        total = len(self.sources)
        saved, failures = [], []
        try:
            if self.combined:
                stem = os.path.splitext(os.path.basename(self.sources[0]))[0]
                target = unique_path(self.folders[0], stem if total == 1 else f"{stem} + {total - 1} more")
                failures = self.converter.convert_combined(
                    self.sources, target, lambda i, n, src: self.file_started.emit(i, n, src))
                saved.append(target)
            else:
                for i, src in enumerate(self.sources):
                    name = os.path.basename(src)
                    self.file_started.emit(i, total, src)
                    self.converter.set_slot(i, total)
                    try:
                        target = unique_path(self.folders[i], os.path.splitext(name)[0])
                        self.converter.convert(src, target)
                        saved.append(target)
                    except ConvertError as ex:
                        failures.append((name, str(ex)))
                        if self.converter.cancelled:
                            break
                    except Exception as ex:  # noqa: BLE001 - one bad file must not stop the others
                        failures.append((name, f"Unexpected error: {ex}"))
        except ConvertError as ex:
            failures.append(("", str(ex)))
        except Exception as ex:  # noqa: BLE001 - never let the thread die silently
            failures.append(("", f"Unexpected error: {ex}"))
        self.done.emit(saved, failures)

    def cancel(self):
        self.converter.cancel()


class SizePage(QWidget):
    """What the program's file size is made of (measured on the built exe, MB)."""

    ROWS = [
        ("IfcOpenShell (IFC)", 36.8),
        ("Qt / PySide6 (the window)", 19.8),
        ("cascadio / OpenCASCADE (STEP, IGES)", 13.0),
        ("Python runtime, splash, app code", 10.3),
        ("SketchUp API (SKP)", 4.8),
        ("ezdxf (DXF / DWG)", 2.5),
        ("fontTools (needed by ezdxf)", 2.1),
        ("rhino3dm (3DM)", 1.9),
        ("shapely (geometry helper)", 1.4),
        ("Other small libraries & packaging", 1.1),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        note = QLabel("Most of the size is conversion libraries.\nThey are unpacked to "
                      "%LOCALAPPDATA%\\3DConvert\\libs the first time a format needs them.")
        note.setObjectName("infoMsg")
        note.setWordWrap(True)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(7)
        grid.setColumnStretch(0, 1)
        top = max(mb for _, mb in self.ROWS)
        for r, (name, mb) in enumerate(self.ROWS):
            key = QLabel(name)
            key.setObjectName("infoVal")
            bar = QProgressBar()
            bar.setObjectName("sizeBar")
            bar.setTextVisible(False)
            bar.setRange(0, 1000)
            bar.setValue(round(1000 * mb / top))
            bar.setFixedWidth(150)
            size = QLabel(f"{mb:.1f} MB")
            size.setObjectName("infoKey")
            size.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(key, r, 0)
            grid.addWidget(bar, r, 1, Qt.AlignVCenter)
            grid.addWidget(size, r, 2)
        total = QLabel(f"Total: {sum(mb for _, mb in self.ROWS):.1f} MB")
        total.setObjectName("infoHeader")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(note)
        lay.addSpacing(16)  # one text line
        lay.addLayout(grid)
        lay.addStretch(1)
        lay.addWidget(total, alignment=Qt.AlignRight)


# (key used by engine/in_blender/cleanup.py, name, one-line explanation); shown sorted by name
CLEANUP_STEPS = [
    ("merge", "Duplicate vertices merged", "Welds vertices closer than 0.1 mm."),
    ("degenerate", "Degenerate geometry dissolved", "Removes zero-area faces and zero-length edges."),
    ("loose", "Loose edges and points removed", "Deletes stray edges and vertices without faces."),
    ("normals", "Normals recalculated", "Points every face outward, consistently."),
    ("quads", "Clean face layout", "Triangulates, then rejoins nearly flat faces into quads."),
    ("flat", "Flat shading, no marks", "Clears smooth shading, sharp edges and UV seams."),
    ("leftovers", "Importer leftovers removed", "Drops ios_* data, paint masks, skin and custom normals."),
    ("slots", "Unused material slots dropped", "Removes material slots that no face uses."),
    ("materials", "Simple materials", "Plain base colour only: no textures or transparency."),
    ("hidden", "Hidden things sorted", "Moves hidden objects to the !HIDDEN collection."),
    ("purge", "Orphan data purged", "Clears unused meshes, materials and images, repeated until none are left."),
    ("orphans", "Stray objects removed", "Deletes objects that sit in no scene or collection."),
    ("animation", "Animation removed", "Clears keyframes, drivers and actions; objects keep their current pose."),
    ("armatures", "Bones and armatures removed", "Deletes armatures, bone weight groups and armature modifiers."),
    ("modifiers", "Modifiers removed", "Deletes all modifiers without applying them; the name gets a note."),
    ("physics", "Physics removed", "Clears cloth, soft body, rigid body, particles, fluid and force fields."),
    ("unparent", "Parents cleared, position kept", "Clear Parent + Keep Transform on every object."),
    ("empties", "Empty objects removed", "Deletes empties (the tilted-Y icons); children keep their place."),
    ("duplicates", "Duplicate faces and objects removed", "Deletes faces on the same vertices and identical objects in place."),
    ("mergemats", "Materials merged by colour", "Combines materials with the same colour into one."),
    ("uvcolor", "UV maps and colours removed", "Deletes UV maps and vertex colour attributes."),
    ("extras", "Extra mesh data removed", "Clears shape keys, weight groups, bevel/crease weights, custom properties."),
    ("camlight", "Cameras, lights and world removed", "Deletes cameras, lights and the world from the file."),
    ("emptymesh", "Empty meshes removed", "Deletes mesh objects without any faces or edges."),
    ("applyts", "Scale and rotation applied", "Bakes scale and rotation into the mesh; positions stay the same."),
    ("names", "Names tidied", "Strips .001 suffixes when free and deletes empty collections."),
    ("tomesh", "Curves and text turned into meshes", "Converts curves, surfaces, text and metaballs to mesh objects."),
    ("unhide", "Hidden geometry unhidden", "Unhides faces, edges and vertices hidden inside meshes."),
    ("scene", "Scene tidied", "One scene, metric units at scale 1, timeline reset."),
    ("viewport", "Stuck-invisible objects fixed", "Turns Show in Viewport back on; hiding stays on the eye icon."),
]


def _icon_canvas(size):
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    return pm, p


def trash_pixmap(size=14, color="#e6e6e6"):
    pm, p = _icon_canvas(size)
    s = size / 14
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(5 * s, 0.5 * s, 4 * s, 2 * s), 0.8 * s, 0.8 * s)  # handle
    p.drawRoundedRect(QRectF(1.5 * s, 2.5 * s, 11 * s, 1.8 * s), 0.9 * s, 0.9 * s)  # lid
    p.drawRoundedRect(QRectF(3 * s, 5 * s, 8 * s, 8.5 * s), 1.2 * s, 1.2 * s)  # bin
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    for x in (5.1, 8.1):
        p.drawRect(QRectF(x * s, 6.5 * s, 0.8 * s, 5.5 * s))
    p.end()
    return pm


def file_pixmap(size=14, color="#e6e6e6"):
    """A page with a folded corner and three text lines."""
    pm, p = _icon_canvas(size)
    s = size / 14
    page = QPainterPath()
    page.moveTo(2.5 * s, 0.5 * s)
    page.lineTo(8.5 * s, 0.5 * s)
    page.lineTo(11.5 * s, 3.5 * s)
    page.lineTo(11.5 * s, 13.5 * s)
    page.lineTo(2.5 * s, 13.5 * s)
    page.closeSubpath()
    p.setBrush(QColor(color))
    p.drawPath(page)
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    for y in (6.0, 8.2, 10.4):
        p.drawRect(QRectF(4.3 * s, y * s, 5.4 * s, 0.9 * s))
    p.drawRect(QRectF(8.2 * s, 0.4 * s, 0.4 * s, 3.4 * s))  # gap between page and fold
    p.drawRect(QRectF(8.2 * s, 3.4 * s, 3.4 * s, 0.4 * s))
    p.end()
    return pm


# Program badges are drawn in code (letters on a coloured tile), so no logos are bundled.
BADGES = {
    "3MF": ("3MF", "#2f8f5b"), "Alembic": ("A", "#6c5bd6"), "AMF": ("AMF", "#4a7fb5"),
    "Autodesk 3ds Max": ("3ds", "#1f9ab3"), "Autodesk FBX": ("FBX", "#3a6ea5"), "AutoCAD": ("A", "#c8372d"),
    "Blender": ("B", "#ea7600"), "COLLADA": ("DAE", "#7d8290"), "glTF": ("gl", "#5aa545"),
    "IFC (BIM)": ("IFC", "#2d7d9a"), "IGES": ("IGS", "#7b6b8a"), "Rhino": ("R", "#33363d"),
    "SketchUp": ("S", "#2a6cb0"), "Stanford PLY": ("PLY", "#8c1515"), "STEP": ("STP", "#5b6b7a"),
    "STL": ("STL", "#a0522d"), "USD": ("USD", "#b8962e"), "VRML / X3D": ("X3D", "#3d7fbf"),
    "Wavefront OBJ": ("OBJ", "#6a8f3a"),
}


def badge_pixmap(name, size=32):
    text, colour = BADGES.get(name, (name[:1], "#6a6e7b"))
    pm, p = _icon_canvas(size)
    p.setBrush(QColor(colour))
    p.drawRoundedRect(QRectF(0, 0, size, size), 7, 7)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(round(size * (0.5 if len(text) == 1 else 0.38 if len(text) == 2 else 0.31)))
    p.setFont(font)
    p.setPen(QColor("#ffffff"))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, text)
    p.end()
    return pm


def cog_pixmap(size=14, color="#6d707a"):
    pm, p = _icon_canvas(size)
    c = size / 2
    p.setBrush(QColor(color))
    p.translate(c, c)
    for i in range(8):
        p.save()
        p.rotate(i * 45)
        p.drawRect(QRectF(-1, -c, 2, c))
        p.restore()
    p.drawEllipse(QPointF(0, 0), c * 0.68, c * 0.68)
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    p.drawEllipse(QPointF(0, 0), c * 0.28, c * 0.28)
    p.end()
    return pm


def bug_pixmap(size=14, color="#6d707a"):
    pm, p = _icon_canvas(size)
    c = size / 2
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(c - 2.6, 4.9, 5.2, 7.2))
    p.drawEllipse(QPointF(c, 3.8), 1.7, 1.7)
    pen = QPen(QColor(color), 1)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    for sx in (-1, 1):
        for (x1, y1, x2, y2) in ((2.4, 7.0, 5.2, 5.2), (2.6, 8.8, 5.6, 8.8), (2.4, 10.6, 5.2, 12.4), (0.8, 2.4, 2.2, 0.6)):
            p.drawLine(QPointF(c + sx * x1, y1), QPointF(c + sx * x2, y2))
    p.end()
    return pm


class CleanupScreen(QFrame):
    """Every cleaning step as a tick box (all on); unticked steps are skipped during conversion."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("infoBox")
        header = QLabel("Cleanup steps")
        header.setObjectName("infoHeader")
        note = QLabel("Untick the steps you do not want during conversion.")
        note.setObjectName("footer")

        scroll = QScrollArea()
        scroll.setObjectName("infoScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setStyleSheet("background: transparent;")
        inner = QWidget()
        inner.setObjectName("infoInner")
        items = QVBoxLayout(inner)
        items.setContentsMargins(0, 0, 8, 0)
        items.setSpacing(9)
        self.checks = {}
        for key, title, text in sorted(CLEANUP_STEPS, key=lambda s: s[1].lower()):
            box = QCheckBox(title)
            box.setObjectName("stepCheck")
            box.setChecked(True)
            box.setCursor(Qt.PointingHandCursor)
            sub = QLabel(text)
            sub.setObjectName("infoKey")
            sub.setContentsMargins(20, 0, 0, 0)  # lines up with the tick box text
            item = QVBoxLayout()
            item.setSpacing(1)
            item.addWidget(box)
            item.addWidget(sub)
            items.addLayout(item)
            self.checks[key] = box
        items.addStretch(1)
        scroll.setWidget(inner)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 10, 12)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(header)
        head.addStretch(1)
        for text, on in (("Disable all", False), ("Enable all", True)):
            btn = QPushButton(text)
            btn.setObjectName("miniBtn")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, on=on: self.set_all(on))
            head.addWidget(btn)
        lay.addLayout(head)
        lay.addWidget(scroll, 1)
        lay.addWidget(note)  # static footer, like the one on the program info page

    def set_all(self, on: bool):
        for box in self.checks.values():
            box.setChecked(on)

    def disabled_steps(self):
        return [key for key, box in self.checks.items() if not box.isChecked()]


def icons_dir():
    base = sys._MEIPASS if getattr(sys, "frozen", False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", "icons")


def program_icon(name, size=32):
    """assets/icons/<name in lower case with underscores>.png when it exists (drop a file there to replace an icon),
    else the drawn letter badge."""
    path = os.path.join(icons_dir(), re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") + ".png")
    if os.path.isfile(path):
        pm = QPixmap(path)
        if not pm.isNull():
            pm = pm.scaled(size * 2, size * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(2)
            return pm
    return badge_pixmap(name, size)


STATUS_COLOURS = {"Supported": "#8fd6a1", "Partially tested": "#f6b98a", "Not tested": "#767a86"}


class Orb(QWidget):
    """A small round status light; the status text is its tooltip."""

    def __init__(self, status, parent=None):
        super().__init__(parent)
        self._colour = QColor(STATUS_COLOURS[status])
        self.setFixedSize(12, 12)
        self.setToolTip(status)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self._colour)
        p.drawEllipse(QRectF(1, 1, 10, 10))


class FormatRow(QWidget):
    """Line 1: [*] badge (two lines high), program name left, extensions centred, status orb right.
    Line 2: description. The * marks formats that Blender imports natively."""

    def __init__(self, name, extensions, description, parent=None):
        super().__init__(parent)
        status = program_status(extensions)
        native = all(ext in NATIVE_FORMATS for ext in extensions)
        star = QLabel("*" if native else "")
        star.setFixedWidth(8)
        star.setAlignment(Qt.AlignRight | Qt.AlignTop)
        star.setStyleSheet("color: #ff9a1f; font-size: 14px; font-weight: 700;")
        icon = QLabel()
        icon.setPixmap(program_icon(name, 32))
        icon.setFixedSize(32, 32)
        title = QLabel(name)
        title.setObjectName("infoVal")
        exts = QLabel("  ".join(extensions))
        exts.setAlignment(Qt.AlignCenter)
        exts.setStyleSheet("color: #ff9a1f; font-size: 12px;")
        line = QHBoxLayout()
        line.setSpacing(6)
        line.addWidget(title, 1)  # equal thirds: name on the left, extensions in the middle, orb on the right
        line.addWidget(exts, 1)
        line.addWidget(Orb(status), 1, Qt.AlignRight | Qt.AlignVCenter)
        desc = ElidedLabel()
        desc.setObjectName("infoKey")
        desc.set_full_text(description)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(2)
        grid.addWidget(star, 0, 0, 2, 1, Qt.AlignTop)
        grid.addWidget(icon, 0, 1, 2, 1, Qt.AlignVCenter)
        grid.addLayout(line, 0, 2)
        grid.addWidget(desc, 1, 2)
        grid.setColumnStretch(2, 1)


class FormatsScreen(QFrame):
    """Every supported program with its extensions, a short description and whether it has been tested."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("infoBox")
        header = QLabel("Supported file formats")
        header.setObjectName("infoHeader")
        native_note = QLabel("* Format native to Blender import")
        native_note.setObjectName("footer")
        head = QHBoxLayout()
        head.addWidget(header)
        head.addStretch(1)
        head.addWidget(native_note)
        scroll = QScrollArea()
        scroll.setObjectName("infoScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setStyleSheet("background: transparent;")
        inner = QWidget()
        inner.setObjectName("infoInner")
        rows = QVBoxLayout(inner)
        rows.setContentsMargins(0, 0, 8, 0)
        rows.setSpacing(8)
        for i, (name, extensions, description) in enumerate(PROGRAMS):
            if i:
                line = QFrame()  # a barely visible divider between two programs
                line.setFixedHeight(1)
                line.setStyleSheet("background: #2f3239; border: none;")
                rows.addWidget(line)
            rows.addWidget(FormatRow(name, extensions, description))
        rows.addStretch(1)
        scroll.setWidget(inner)

        legend = QHBoxLayout()
        legend.setSpacing(5)
        gap = QFontMetrics(native_note.font()).horizontalAdvance("   ")  # three spaces between the entries
        for status in STATUS_COLOURS:
            text = QLabel(status)
            text.setObjectName("footer")
            legend.addWidget(Orb(status))
            legend.addWidget(text)
            legend.addSpacing(gap)
        legend.addStretch(1)
        support_note = QLabel("Extended support is being tested.")
        support_note.setObjectName("footer")
        legend.addWidget(support_note)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 10, 12)
        lay.setSpacing(8)
        lay.addLayout(head)
        lay.addWidget(scroll, 1)
        lay.addLayout(legend)


class InfoScreen(QFrame):
    """Program size breakdown; update note and AI disclaimer at the bottom."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("infoBox")
        title = QLabel("Program size breakdown")
        title.setObjectName("infoHeader")

        footer = QHBoxLayout()
        footer.setSpacing(6)
        for i, (pixmap, text) in enumerate(((cog_pixmap(), f"Last update {LAST_UPDATE}"),
                                            (bug_pixmap(), "Made with AI (Claude Sonnet 5.5), expect bugs"))):
            if i:
                footer.addStretch(1)
            icon = QLabel()
            icon.setPixmap(pixmap)
            icon.setFixedSize(14, 14)
            label = QLabel(text)
            label.setObjectName("footer")
            for w in ((label, icon) if i else (icon, label)):  # the bug icon follows its text
                footer.addWidget(w)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(10)
        lay.addWidget(title)
        lay.addWidget(SizePage(), 1)
        lay.addLayout(footer)


class TitleBar(QWidget):
    pageChanged = Signal(int)  # 0 = main, 1 = size info, 2 = cleanup steps, 3 = supported file formats

    def __init__(self, window):
        super().__init__(window)
        self.setObjectName("titleBar")
        self._win = window
        self._drag = None
        self.setFixedHeight(TITLE_H)

        logo = QLabel()
        logo.setPixmap(cube_pixmap(64).scaled(LOGO_PX, LOGO_PX, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        label = QLabel(APP_NAME)
        label.setObjectName("titleLabel")
        self.info = QPushButton("i")
        self.info.setObjectName("infoBtn")
        self.info.setToolTip("Program info")
        self.cog = QPushButton()
        self.cog.setObjectName("cogBtn")
        self.cog.setToolTip("Cleanup steps")
        cog_icon = QIcon()
        cog_icon.addPixmap(cog_pixmap(14, "#e6e6e6"), QIcon.Normal, QIcon.Off)
        cog_icon.addPixmap(cog_pixmap(14, "#1e1f24"), QIcon.Normal, QIcon.On)
        self.cog.setIcon(cog_icon)
        self.cog.setIconSize(QSize(14, 14))
        self.files = QPushButton()
        self.files.setObjectName("fileBtn")
        self.files.setToolTip("Supported file formats")
        file_icon = QIcon()
        file_icon.addPixmap(file_pixmap(14, "#e6e6e6"), QIcon.Normal, QIcon.Off)
        file_icon.addPixmap(file_pixmap(14, "#1e1f24"), QIcon.Normal, QIcon.On)
        self.files.setIcon(file_icon)
        self.files.setIconSize(QSize(14, 14))
        self._pages = {1: self.info, 2: self.cog, 3: self.files}
        for page, btn in self._pages.items():
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.toggled.connect(lambda on, p=page: self._toggled(p, on))
        minimize = QPushButton("\u2013")
        minimize.setObjectName("winBtn")
        minimize.setCursor(Qt.PointingHandCursor)
        minimize.clicked.connect(window.showMinimized)
        close = QPushButton("\u2715")
        close.setObjectName("closeBtn")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(window.close)
        tagline = QLabel("Speeding up your creative workflow")
        tagline.setObjectName("tagline")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, TITLE_RIGHT, 0)
        lay.setSpacing(4)
        lay.addWidget(logo)
        lay.addSpacing(4)
        lay.addWidget(label)
        lay.addStretch()
        lay.addWidget(tagline)
        # Tagline's right edge = drop zone right edge, inset by the gap between the drop zone's left edge and the title
        title_x = 12 + LOGO_PX + 4 + 4 + 4
        lay.addSpacing(18 + COL_W + 14 + (title_x - 18) - 8 - GROUP_W - TITLE_RIGHT)
        lay.addWidget(self.files)
        lay.addWidget(self.cog)
        lay.addWidget(self.info)
        lay.addWidget(minimize)
        lay.addWidget(close)

    def _toggled(self, page, on):
        if on:  # only one of the screens at a time
            for other_page, other in self._pages.items():
                if other_page != page:
                    other.blockSignals(True)
                    other.setChecked(False)
                    other.blockSignals(False)
        self.pageChanged.emit(page if on else 0)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self._win.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self._win.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _):
        self._drag = None


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(cube_icon())
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setFixedSize(600, 468)
        self._worker = None  # the running ReadWorker
        self._convert_worker = None
        self._threads = set()  # keeps finished-soon QThreads alive until they really stopped
        self._files = []  # one dict per added file: path, key, name, ext, state, rows, msg
        self._index = 0  # file shown in the drop zone and information panel
        self._reading = None  # entry that is being read right now
        self._converting = False
        self._hold_status = False  # keep the result of the last conversion in the status pill
        self._custom_dir = None  # folder picked with Change location; None = folder of the input file
        self._blender_note, self._blender_ok = blender_status()

        root = QFrame(self)
        root.setObjectName("root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        body = QVBoxLayout(root)
        body.setContentsMargins(1, 1, 1, 1)
        body.setSpacing(0)
        bar = TitleBar(self)
        body.addWidget(bar)

        page = QWidget()
        content = QVBoxLayout(page)
        content.setContentsMargins(18, 5, 18, 10)  # top: 5 + title bar padding = 12 px; bottom matches the x button's top gap
        content.setSpacing(12)
        info_page = QWidget()
        info_lay = QVBoxLayout(info_page)
        info_lay.setContentsMargins(18, 5, 18, 10)
        info_lay.addWidget(InfoScreen())
        cleanup_page = QWidget()
        cleanup_lay = QVBoxLayout(cleanup_page)
        cleanup_lay.setContentsMargins(18, 5, 18, 10)
        self.cleanup = CleanupScreen()
        cleanup_lay.addWidget(self.cleanup)
        self.stack = QStackedWidget()
        self.stack.addWidget(page)
        self.stack.addWidget(info_page)
        self.stack.addWidget(cleanup_page)
        formats_page = QWidget()
        formats_lay = QVBoxLayout(formats_page)
        formats_lay.setContentsMargins(18, 5, 18, 10)
        formats_lay.addWidget(FormatsScreen())
        self.stack.addWidget(formats_page)
        bar.pageChanged.connect(self.stack.setCurrentIndex)
        body.addWidget(self.stack, 1)

        top = QHBoxLayout()
        top.setSpacing(14)
        self.drop = DropZone()
        self.drop.filesChosen.connect(self.load_files)
        self.pill = StatusPill()
        self.clear_btn = QPushButton("Clear data")
        self.clear_btn.setObjectName("clearBtn")
        self.clear_btn.setFixedSize(COL_W, 30)
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self.clear_data)
        top_right = QVBoxLayout()
        top_right.setSpacing(8)
        top_right.addWidget(self.clear_btn)
        top_right.addWidget(self.pill)
        top.addWidget(self.drop, 1)
        top.addLayout(top_right)
        content.addLayout(top)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        content.addWidget(self.progress)

        mid = QHBoxLayout()
        mid.setSpacing(14)

        self.info = InfoPanel()
        self.info.nav.step.connect(self.step_file)
        self.info.trash.clicked.connect(self.remove_current)
        mid.addWidget(self.info, 1)

        right = QVBoxLayout()
        right.setSpacing(8)
        self.out_btn = QPushButton("Change location")
        self.out_btn.setObjectName("clearBtn")
        self.out_btn.setFixedSize(COL_W, 30)
        self.out_btn.setCursor(Qt.PointingHandCursor)
        self.out_btn.clicked.connect(self.on_output_clicked)
        self.open_btn = QPushButton()
        self.open_btn.setObjectName("clearBtn")
        self.open_btn.setIcon(folder_icon())
        self.open_btn.setIconSize(QSize(18, 18))
        self.open_btn.setFixedSize(30, 30)
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.setToolTip("Open output folder")
        self.open_btn.clicked.connect(self.open_output)
        self.out_path = ElidedLabel()
        self.out_path.setObjectName("outPath")
        self.out_path.setFixedHeight(30)

        mode = QHBoxLayout()
        mode.setSpacing(0)
        self.mode_group = QButtonGroup(self)
        self.separate_btn, self.combined_btn = QPushButton("Separate"), QPushButton("Combined")
        for btn, name, tip in ((self.separate_btn, "modeLeft", "One .blend per file"),
                               (self.combined_btn, "modeRight",
                                "All files in one .blend, each in its own collection")):
            btn.setObjectName(name)
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.setCursor(Qt.PointingHandCursor)
            self.mode_group.addButton(btn)
            mode.addWidget(btn)
        self.separate_btn.setChecked(True)
        self.convert_btn = QPushButton("Create .blend")
        self.convert_btn.setObjectName("convertBtn")
        self.convert_btn.setEnabled(False)
        self.convert_btn.setFixedSize(COL_W, 50)
        self.convert_btn.clicked.connect(self.on_convert)
        self.steps = StepList()
        self.steps.setFixedWidth(COL_W)
        right.addLayout(mode)
        right.addWidget(self.convert_btn)
        right.addWidget(self.steps, 1)
        right.addWidget(self.out_btn)
        mid.addLayout(right)
        content.addLayout(mid, 1)

        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        out_row.addWidget(self.out_path, 1)
        out_row.addWidget(self.open_btn)
        self.version = QLabel(APP_VERSION)
        self.version.setObjectName("version")
        self.version.setFixedWidth(COL_W - 30 - 8)
        self.version.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.version.setContentsMargins(0, 0, TITLE_RIGHT - 18, 0)  # right edge in line with the close button
        font = self.version.font()
        for px in range(14, 8, -1):  # largest size that fits the free space next to the folder button
            font.setPixelSize(px)
            if QFontMetrics(font).horizontalAdvance(APP_VERSION) <= self.version.width() - (TITLE_RIGHT - 18) - 2:
                break
        self.version.setFont(font)
        out_row.addWidget(self.version)
        keep = self.open_btn.sizePolicy()
        keep.setRetainSizeWhenHidden(True)
        self.open_btn.setSizePolicy(keep)
        content.addLayout(out_row)
        self.mode_group.buttonToggled.connect(self._mode_changed)
        self.refresh_output()
        self._update_controls()

    def set_status(self, text, tone="idle", busy=False, tip=""):
        self.pill.set_status(text, tone, busy)
        self.pill.setToolTip(tip or text)

    def _current(self):
        return self._files[self._index] if self._files else None

    def _folder_for(self, entry):
        return self._custom_dir or os.path.dirname(entry["path"])

    def output_dir(self):
        """Folder the .blend goes to; combined mode uses the folder of the first file."""
        if not self._files:
            return self._custom_dir
        return self._folder_for(self._files[0] if self.combined_btn.isChecked() else self._current())

    def refresh_output(self):
        folder = self.output_dir()
        self.open_btn.setVisible(bool(folder))
        if folder:
            self.out_path.setStyleSheet("")
            self.out_path.set_full_text(folder)
        else:  # nothing loaded yet: this line carries the Blender check result
            self.out_path.setStyleSheet(f"color: {'#6d707a' if self._blender_ok else '#ff8b84'}; font-size: 12px;")
            self.out_path.set_full_text(self._blender_note)
        self.out_path.setToolTip(folder or "")
        self.out_btn.setText("Clear location" if self._custom_dir else "Change location")

    def open_output(self):
        folder = self.output_dir()
        if folder and os.path.isdir(folder):
            os.startfile(folder)

    def on_output_clicked(self):
        if self._custom_dir:
            self._custom_dir = None
        else:
            folder = QFileDialog.getExistingDirectory(self, "Select output folder", self.output_dir() or "")
            if not folder:
                return
            self._custom_dir = os.path.normpath(folder)
        self.refresh_output()

    def _track(self, worker):
        """Keeps a started thread referenced until it has really stopped (a QThread must not be freed while running)."""
        self._threads = {t for t in self._threads if t.isRunning()}
        self._threads.add(worker)

    def _update_controls(self):
        n = len(self._files)
        busy = self._converting
        reading = any(e["state"] in ("pending", "reading") for e in self._files)
        ready = any(e["state"] == "ok" for e in self._files)
        self.drop.setEnabled(self._blender_ok and not busy)
        self.drop.set_greyed(not self._blender_ok)
        self.clear_btn.setEnabled(n > 0 and not busy)
        self.clear_btn.setText(f"Clear data ({n})" if n > 1 else "Clear data")
        for w in (self.out_btn, self.open_btn, self.separate_btn, self.combined_btn):
            w.setEnabled(not busy)
        self.convert_btn.setEnabled(self._blender_ok and ready and not reading and not busy)
        self.info.set_nav(self._index + 1, n, not busy)

    def _show_steps(self):
        entry = self._current()
        if entry and entry["state"] in ("pending", "reading", "ok"):
            self.steps.set_steps(steps_for(entry["ext"], self.combined_btn.isChecked()))
        else:
            self.steps.set_steps([])

    def _show_current(self):
        """Drop zone, information panel and step list follow the selected file."""
        entry = self._current()
        self.info.set_nav(self._index + 1, len(self._files), not self._converting)
        if entry is None:
            self.drop.reset()
            self.info.set_message("No file loaded.")
        else:
            self.drop.show_file(entry["name"], *format_note(entry["ext"]))
            state = entry["state"]
            if state == "unsupported":
                self.drop.show_error()
                self.info.set_message(EXPORT_HINTS.get(entry["ext"], "This file format is not supported."))
            elif state == "failed":
                self.drop.show_error()
                self.info.set_message(f"Could not read file: {entry['msg']}")
            elif state == "ok":
                self.info.set_rows(entry["rows"])
            else:
                self.info.set_message("Reading..." if state == "reading" else "Waiting to be read...")
        self._show_steps()
        self.refresh_output()

    def _refresh_status(self):
        if self._hold_status:
            return
        total = len(self._files)
        waiting = [e for e in self._files if e["state"] in ("pending", "reading")]
        entry = self._current()
        if not total:
            self.set_status("Idle")
        elif waiting:
            label = "Reading" if total == 1 else f"Reading {total - len(waiting) + 1}/{total}"
            self.set_status(label, busy=True, tip="Reading file(s)...")
        elif entry["state"] == "unsupported":
            self.set_status("Unsupported", "error", tip=f"Unsupported format: {entry['ext'] or 'unknown'}")
        elif entry["state"] == "failed":
            self.set_status("Failed", "error", tip=f"Could not read file: {entry['msg']}")
        else:
            self.set_status("Done", "ok", tip="File(s) read successfully")

    def _mode_changed(self, *_):
        self._show_steps()
        self.refresh_output()

    def step_file(self, direction: int):
        if len(self._files) < 2 or self._converting:
            return
        self._index = (self._index + direction) % len(self._files)
        self._show_current()
        self._refresh_status()

    def remove_current(self):
        """Removes only the selected file from the list."""
        entry = self._current()
        if entry is None or self._converting:
            return
        if entry is self._reading:
            self._stop_reading()
        self._files.pop(self._index)
        self._index = min(self._index, len(self._files) - 1) if self._files else 0
        self._hold_status = False
        self.progress.setValue(0)
        self._show_current()
        self._pump_reads()
        self._refresh_status()
        self._update_controls()

    def clear_data(self):
        if self._converting:
            return
        self._stop_reading()
        self._files.clear()
        self._index = 0
        self._hold_status = False
        self.progress.setValue(0)
        self._show_current()
        self._refresh_status()
        self._update_controls()

    def _stop_reading(self):
        worker, self._worker = self._worker, None
        self._reading = None
        if worker and worker.isRunning():
            for signal in (worker.progress, worker.done, worker.failed):
                try:
                    signal.disconnect()
                except (RuntimeError, TypeError):
                    pass
            worker.requestInterruption()
            cancel_scan()
            if not worker.wait(3000):
                worker.terminate()
                worker.wait(1000)

    def load_files(self, paths):
        """Adds files to the list (never replaces); a file that is already in the list is skipped."""
        if self._converting or not self._blender_ok:
            return
        known = {e["key"]: i for i, e in enumerate(self._files)}
        added, duplicate = [], None
        for path in paths:
            if not os.path.isfile(path):
                continue
            key = os.path.normcase(os.path.abspath(path))
            if key in known:
                duplicate = known[key] if duplicate is None else duplicate
                continue
            ext = os.path.splitext(path)[1].lower()
            entry = {"path": path, "key": key, "name": os.path.basename(path), "ext": ext, "rows": None, "msg": "",
                     "state": "pending" if ext in SUPPORTED else "unsupported"}
            known[key] = len(self._files)
            self._files.append(entry)
            added.append(entry)
        if not added:
            if duplicate is not None:  # nothing new: just show the file that was dropped again
                self._index = duplicate
                self._show_current()
                self._refresh_status()
            return
        if len(added) == len(self._files):
            self._index = 0
        self._hold_status = False
        self.progress.setValue(0)
        self._show_current()
        self._refresh_status()
        self._pump_reads()
        self._update_controls()

    def _pump_reads(self):
        """Reads the files one after another (the scans share one Blender process slot)."""
        if self._reading is not None or self._converting:
            return
        for entry in self._files:
            if entry["state"] != "pending":
                continue
            entry["state"] = "reading"
            self._reading = entry
            worker = ReadWorker(entry["path"])
            worker.progress.connect(self.progress.setValue)
            worker.done.connect(lambda rows, e=entry: self._read_finished(e, "ok", rows=rows))
            worker.failed.connect(lambda msg, e=entry: self._read_finished(e, "failed", msg=msg))
            self._worker = worker
            self._track(worker)
            worker.start()
            if entry is self._current():
                self._show_current()
            self._refresh_status()
            return

    def _read_finished(self, entry, state, rows=None, msg=""):
        if not any(e is entry for e in self._files):
            return  # the list was cleared meanwhile
        entry.update(state=state, rows=rows, msg=msg)
        self._reading = None
        self.progress.setValue(0)
        if entry is self._current():
            self._show_current()
        self._pump_reads()
        self._refresh_status()
        self._update_controls()

    def on_convert(self):
        files = [e for e in self._files if e["state"] == "ok"]
        if not files or self._converting or self._reading is not None or not self._blender_ok:
            return
        combined = self.combined_btn.isChecked()
        folders = [self._folder_for(e) for e in files]
        if any(not os.path.isdir(f) for f in (folders[:1] if combined else folders)):
            self.set_status("Failed", "error", tip="Output folder not found")
            return
        self._converting = True
        self._hold_status = False
        self.progress.setValue(0)
        self.set_status("Converting", busy=True, tip="Converting...")
        self._update_controls()

        worker = ConvertWorker([e["path"] for e in files], folders, combined, self.cleanup.disabled_steps())
        worker.progress.connect(self.progress.setValue)
        worker.step.connect(self.steps.set_active)
        worker.file_started.connect(self._on_file_started)
        worker.done.connect(self._on_convert_done)
        self._convert_worker = worker
        self._track(worker)
        worker.start()

    def _on_file_started(self, index, total, src):
        """Shows the file that is being converted, with its own step list."""
        for i, entry in enumerate(self._files):
            if entry["path"] == src:
                self._index = i
                break
        self._show_current()
        self.set_status("Converting" if total == 1 else f"Converting {index + 1}/{total}", busy=True,
                        tip="Converting...")

    def _on_convert_done(self, saved, failures):
        self._converting = False
        self._hold_status = True
        problems = "\n".join(f"{name}: {msg}" if name else msg for name, msg in failures)
        if saved and not failures:
            self.progress.setValue(100)
            self.set_status("Converted", "ok", tip="Saved:\n" + "\n".join(saved))
        elif saved:
            self.progress.setValue(100)
            self.set_status(f"{len(failures)} failed", "error", tip="Saved:\n" + "\n".join(saved) + "\n\nFailed:\n" + problems)
        else:
            self.progress.setValue(0)
            self.set_status("Failed", "error", tip=problems)
            self.steps.set_failed(problems + "\n\nLog: %LOCALAPPDATA%\\3DConvert\\last_convert.log")
        self._update_controls()

    def closeEvent(self, e):
        if self._convert_worker and self._convert_worker.isRunning():
            self._convert_worker.cancel()
            self._convert_worker.wait(5000)
        self._stop_reading()
        super().closeEvent(e)


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--convert":  # headless: 3DConvert.exe --convert <source> <target.blend>
        close_splash()
        try:
            Converter().convert(sys.argv[2], sys.argv[3])
        except Exception:  # noqa: BLE001 - details are in last_convert.log
            sys.exit(1)
        sys.exit(0)
    app = QApplication(sys.argv)
    app.setWindowIcon(cube_icon())
    app.setStyleSheet(STYLE)
    win = MainWindow()
    win.show()
    app.processEvents()
    close_splash()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
