#!/usr/bin/env python3
"""
MdAPlayer - cross-platform video player based on libmpv (FFmpeg).

Runs on Windows / macOS / Linux. Because libmpv does the decoding, it plays
almost every format like VLC, and HEVC / AV1 / VP9 / HDR / 10-bit content
with hardware acceleration like mpv.

Installation:
    pip install PySide6 python-mpv
    Windows : put libmpv-2.dll (or mpv-2.dll) next to this file
    macOS   : brew install mpv
    Linux   : sudo apt install libmpv2   (or your distribution's equivalent)
"""
import ctypes
import ctypes.util
import hashlib
import json
import locale
import math
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent)


def _prepare_libmpv():
    """Makes libmpv discoverable per platform (before importing mpv)."""
    if sys.platform == "win32":
        os.environ["PATH"] = str(HERE) + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(str(HERE))
            except OSError:
                pass
    elif sys.platform.startswith("linux"):
        # libmpv's OpenGL context can misbehave on Wayland; use XWayland when available.
        # If the xcb plugin cannot load (e.g. libxcb-cursor0 is missing), Qt falls back to "wayland".
        if "QT_QPA_PLATFORM" not in os.environ and os.environ.get("DISPLAY"):
            os.environ["QT_QPA_PLATFORM"] = "xcb;wayland"

    original = ctypes.util.find_library

    def patched(name):
        if sys.platform == "win32" and name in ("mpv", "mpv-1", "mpv-2", "libmpv", "libmpv-2"):
            for cand in ("libmpv-2.dll", "mpv-2.dll", "libmpv.dll", "mpv-1.dll"):
                f = HERE / cand
                if f.exists():
                    return str(f)
        found = original(name)
        if found or name != "mpv":
            return found
        if sys.platform == "darwin":
            for cand in ("/opt/homebrew/lib/libmpv.dylib", "/opt/homebrew/opt/mpv/lib/libmpv.dylib",
                         "/usr/local/lib/libmpv.dylib", "/usr/local/opt/mpv/lib/libmpv.dylib"):
                if os.path.exists(cand):
                    return cand
        return None

    ctypes.util.find_library = patched


_prepare_libmpv()

from PySide6.QtCore import (  # noqa: E402
    QEasingCurve, QEvent, QPoint, QPointF, QRectF, QSettings, QSize, Qt, QTimer,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import (  # noqa: E402
    QAction, QActionGroup, QBrush, QColor, QCursor, QFont, QFontDatabase, QFontMetrics,
    QIcon, QKeySequence, QLinearGradient, QOpenGLContext, QPainter, QPalette, QPen,
    QPixmap, QPolygonF, QRadialGradient, QShortcut, QSurfaceFormat,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton, QApplication, QDialog, QFileDialog, QGraphicsOpacityEffect,
    QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

try:
    import mpv
    from mpv import MpvGlGetProcAddressFn, MpvRenderContext
except Exception as exc:  # libmpv or python-mpv not found
    _app = QApplication(sys.argv)
    QMessageBox.critical(
        None, "libmpv not found",
        "Could not load libmpv.\n\n"
        "First:    pip install python-mpv\n"
        "Windows:  put libmpv-2.dll (or mpv-2.dll) next to this file.\n"
        "macOS:    brew install mpv\n"
        "Linux:    sudo apt install libmpv2 (or your distribution's equivalent)\n\n"
        f"Details: {exc}",
    )
    sys.exit(1)

APP_NAME = "MdAPlayer"
APP_VERSION = "2.0"

ACCENT = "#8b6cff"    # purple
ACCENT2 = "#22d3ee"   # cyan
WARM = "#fb923c"      # volume above 100%

# GL interop does not work on every Linux setup; the "copy" modes work everywhere.
if sys.platform in ("win32", "darwin"):
    HWDEC_CANDIDATES = ("auto-safe",)
else:
    HWDEC_CANDIDATES = ("auto-copy-safe", "auto-safe")

SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx"}

APP_QSS = """
QWidget#stage { background: #000000; }
QLabel { background: transparent; }
QLabel#timeLabel { color: rgba(255, 255, 255, 235); font-size: 13px; font-weight: 600; }
QLabel#drawerTitle { color: #ffffff; font-size: 16px; font-weight: 700; }
QLabel#drawerCount { color: rgba(255, 255, 255, 120); font-size: 12px; }

QListWidget#playlist { background: transparent; border: none; outline: 0; color: #e8e8ee; }
QListWidget#playlist::item { padding: 10px 12px; border-radius: 10px; margin: 2px 0; }
QListWidget#playlist::item:hover { background: rgba(255, 255, 255, 18); }
QListWidget#playlist::item:selected { background: rgba(139, 108, 255, 80); color: #ffffff; }

QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: rgba(255, 255, 255, 50); border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

QMenu { background: #16171d; color: #e8e8ee; border: 1px solid #2b2d38; padding: 6px; }
QMenu::item { padding: 7px 28px 7px 26px; border-radius: 6px; }
QMenu::item:selected { background: rgba(139, 108, 255, 170); color: #ffffff; }
QMenu::item:disabled { color: #70727e; }
QMenu::separator { height: 1px; background: #2b2d38; margin: 6px 8px; }
QToolTip { background: #16171d; color: #e8e8ee; border: 1px solid #2b2d38; padding: 4px 8px; }

QDialog { background: #121319; }
QPlainTextEdit { background: #0e0f14; color: #e8e8ee; border: 1px solid #2b2d38;
                 border-radius: 8px; padding: 8px; selection-background-color: #8b6cff; }
QPushButton { background: #22232c; color: #e8e8ee; border: 1px solid #2b2d38;
              border-radius: 8px; padding: 7px 18px; }
QPushButton:hover { background: rgba(139, 108, 255, 140); }
"""

MEDIA_EXTS = {
    ".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".flv", ".webm", ".ts", ".m2ts", ".mts",
    ".mpg", ".mpeg", ".vob", ".ogv", ".3gp", ".divx", ".rm", ".rmvb", ".asf", ".mxf", ".iso",
    ".mp3", ".flac", ".wav", ".ogg", ".opus", ".m4a", ".aac", ".wma", ".ape", ".alac", ".aiff",
    ".m3u", ".m3u8", ".pls",
}


# ======================================================================
#  Helpers
# ======================================================================
def lerp(a, b, t):
    return a + (b - a) * t


def qcolor(value, alpha=None):
    c = QColor(value)
    if alpha is not None:
        c.setAlpha(int(max(0, min(255, alpha))))
    return c


def fmt_time(seconds):
    if seconds is None or seconds != seconds or seconds < 0:
        return "--:--"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_size(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return None
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024


def fmt_bitrate(bps):
    try:
        bps = float(bps)
    except (TypeError, ValueError):
        return None
    if bps <= 0:
        return None
    return (f"{bps / 1e6:.2f} {"Mb/s"}" if bps >= 1e6
            else f"{bps / 1e3:.0f} {"kb/s"}")


def fmt_fps(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return f"{v:.2f}".rstrip("0").rstrip(".")


def natural_key(text):
    return [int(t) if t.isdecimal() else t.lower() for t in re.split(r"(\d+)", text)]


def ui_font(px, bold=False):
    f = QFont()
    f.setPixelSize(px)
    f.setBold(bold)
    return f


class WheelStepper:
    """Converts wheel deltas into whole notches (120).

    Does nothing on horizontal scrolling (y == 0); accumulates the small deltas
    of precision touchpads so the volume doesn't jump 5 points on every event.
    """

    def __init__(self):
        self._acc = 0

    def feed(self, event):
        dy = event.angleDelta().y()
        if dy == 0:
            return 0
        if self._acc and (dy > 0) != (self._acc > 0):
            self._acc = 0
        self._acc += dy
        steps = int(self._acc / 120)
        self._acc -= steps * 120
        return steps


class Tween(QVariantAnimation):
    """Small animation helper that smoothly changes a single number."""

    def __init__(self, parent, setter, ms=200, curve=QEasingCurve.OutCubic):
        super().__init__(parent)
        self._base_ms = ms
        self.setDuration(ms)
        self.setEasingCurve(curve)
        self.valueChanged.connect(setter)

    def run(self, start, end, ms=None):
        self.stop()
        self.setDuration(self._base_ms if ms is None else ms)
        self.setStartValue(float(start))
        self.setEndValue(float(end))
        self.start()


def draw_icon(p, kind, rect, color, morph=0.0):
    """Vector icons drawn on a 24x24 grid. Morphs between play<->pause (0..1)."""
    p.save()
    p.translate(rect.topLeft())
    p.scale(rect.width() / 24.0, rect.height() / 24.0)
    col = QColor(color)
    stroke_pen = QPen(col, 1.9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    soft_pen = QPen(col, 1.3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)  # soft corners for filled shapes
    p.setPen(soft_pen)
    p.setBrush(col)

    def poly(pts):
        p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))

    def line(pts):
        p.setBrush(Qt.NoBrush)
        p.setPen(stroke_pen)
        p.drawPolyline(QPolygonF([QPointF(x, y) for x, y in pts]))
        p.setPen(soft_pen)
        p.setBrush(col)

    def speaker():
        poly([(4, 9.5), (8, 9.5), (13, 5.5), (13, 18.5), (8, 14.5), (4, 14.5)])

    def arcs(n):
        p.setBrush(Qt.NoBrush)
        p.setPen(stroke_pen)
        if n >= 1:
            p.drawArc(QRectF(9.5, 8.5, 7, 7), -45 * 16, 90 * 16)
        if n >= 2:
            p.drawArc(QRectF(6.0, 5.0, 14, 14), -45 * 16, 90 * 16)
        p.setPen(soft_pen)
        p.setBrush(col)

    if kind in ("play", "pause"):
        play_l = [(7, 4.5), (12.5, 7.75), (12.5, 16.25), (7, 19.5)]
        play_r = [(12.5, 7.75), (19, 12), (19, 12), (12.5, 16.25)]
        pause_l = [(7, 5), (10.5, 5), (10.5, 19), (7, 19)]
        pause_r = [(13.5, 5), (17, 5), (17, 19), (13.5, 19)]
        for a, b in ((play_l, pause_l), (play_r, pause_r)):
            poly([(lerp(x0, x1, morph), lerp(y0, y1, morph)) for (x0, y0), (x1, y1) in zip(a, b)])
    elif kind == "next":
        poly([(6, 5), (15, 12), (6, 19)])
        p.drawRoundedRect(QRectF(16.5, 5, 2.6, 14), 1.2, 1.2)
    elif kind == "prev":
        poly([(18, 5), (9, 12), (18, 19)])
        p.drawRoundedRect(QRectF(4.9, 5, 2.6, 14), 1.2, 1.2)
    elif kind == "vol":
        speaker()
        arcs(2)
    elif kind == "vol_low":
        speaker()
        arcs(1)
    elif kind == "mute":
        speaker()
        p.setBrush(Qt.NoBrush)
        p.setPen(stroke_pen)
        p.drawLine(QPointF(16, 9.5), QPointF(21, 14.5))
        p.drawLine(QPointF(21, 9.5), QPointF(16, 14.5))
    elif kind == "full":
        line([(4, 9), (4, 4), (9, 4)])
        line([(15, 4), (20, 4), (20, 9)])
        line([(20, 15), (20, 20), (15, 20)])
        line([(9, 20), (4, 20), (4, 15)])
    elif kind == "unfull":
        line([(9, 4), (9, 9), (4, 9)])
        line([(15, 4), (15, 9), (20, 9)])
        line([(20, 15), (15, 15), (15, 20)])
        line([(4, 15), (9, 15), (9, 20)])
    elif kind == "playlist":
        for y in (6.5, 12, 17.5):
            p.drawEllipse(QPointF(4.5, y), 1.4, 1.4)
            p.drawRoundedRect(QRectF(8, y - 1.1, 12.5, 2.2), 1.1, 1.1)
    elif kind in ("loop", "loop_one"):
        line([(4.5, 12), (4.5, 9), (18, 9)])
        poly([(17, 5.5), (21.5, 9), (17, 12.5)])
        line([(19.5, 12), (19.5, 15), (6, 15)])
        poly([(7, 11.5), (2.5, 15), (7, 18.5)])
        if kind == "loop_one":
            p.setFont(ui_font(7, True))
            p.setPen(col)
            p.drawText(QRectF(8.5, 8.5, 7, 7), Qt.AlignCenter, "1")
    elif kind == "more":
        for cx in (5, 12, 19):
            p.drawEllipse(QPointF(cx, 12), 1.9, 1.9)
    elif kind == "info":
        p.setBrush(Qt.NoBrush)
        p.setPen(stroke_pen)
        p.drawEllipse(QPointF(12, 12), 9, 9)
        p.drawLine(QPointF(12, 11), QPointF(12, 17))
        p.setPen(soft_pen)
        p.setBrush(col)
        p.drawEllipse(QPointF(12, 7.6), 1.3, 1.3)
    elif kind == "close":
        line([(6, 6), (18, 18)])
        line([(18, 6), (6, 18)])
    p.restore()


def make_app_icon(size=128):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor(ACCENT))
    g.setColorAt(1, QColor(ACCENT2))
    p.setBrush(QBrush(g))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(4, 4, size - 8, size - 8), size * 0.26, size * 0.26)
    s = size / 100.0
    p.setBrush(QColor("white"))
    p.setPen(QPen(QColor("white"), 3 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPolygon(QPolygonF([QPointF(40 * s, 30 * s), QPointF(72 * s, 50 * s), QPointF(40 * s, 70 * s)]))
    p.end()
    return QIcon(pm)


# ======================================================================
#  Animated button
# ======================================================================
class IconButton(QAbstractButton):
    """Button that grows and glows on hover, shrinks when pressed; morphs between play<->pause."""

    def __init__(self, kind=None, size=40, icon_px=20, text=None, primary=False,
                 backdrop=False, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._size = size
        self._icon_px = icon_px
        self._text = text
        self.primary = primary
        self.backdrop = backdrop
        self.active = False
        self._hover = 0.0
        self._press = 0.0
        self._morph = 1.0 if kind == "pause" else 0.0
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._hover_anim = Tween(self, self._set_hover, 170)
        self._press_anim = Tween(self, self._set_press, 120)
        self._morph_anim = Tween(self, self._set_morph, 280, QEasingCurve.InOutCubic)

    # --- properties ---
    def _set_hover(self, v):
        self._hover = float(v)
        self.update()

    def _set_press(self, v):
        self._press = float(v)
        self.update()

    def _set_morph(self, v):
        self._morph = float(v)
        self.update()

    def setIconKind(self, kind):
        if kind == self._kind:
            return
        old, self._kind = self._kind, kind
        if {old, kind} == {"play", "pause"}:
            self._morph_anim.run(self._morph, 1.0 if kind == "pause" else 0.0)
        else:
            self._morph = 1.0 if kind == "pause" else 0.0
            self.update()

    def setActive(self, on):
        if self.active != bool(on):
            self.active = bool(on)
            self.update()

    def setText(self, text):
        if text == self._text:
            return
        self._text = text
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        if self._text is not None:
            w = QFontMetrics(ui_font(12, True)).horizontalAdvance(self._text) + 22
            return QSize(max(w, self._size), self._size)
        return QSize(self._size, self._size)

    # --- events ---
    def enterEvent(self, e):
        self._hover_anim.run(self._hover, 1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover_anim.run(self._hover, 0.0)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._press_anim.run(self._press, 1.0, 90)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._press_anim.run(self._press, 0.0, 200)
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        c = r.center()
        h, pr = self._hover, self._press

        if self.primary:
            scale = 1 + 0.08 * h - 0.07 * pr
            d = min(r.width(), r.height()) - 4
            if h > 0.01:   # glow
                glow = QRadialGradient(c, d * 0.95)
                glow.setColorAt(0.55, qcolor(ACCENT, 90 * h))
                glow.setColorAt(1.0, qcolor(ACCENT, 0))
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(glow))
                p.drawEllipse(c, d * 0.95, d * 0.95)
            g = QLinearGradient(c.x() - d / 2, c.y() - d / 2, c.x() + d / 2, c.y() + d / 2)
            g.setColorAt(0, QColor(ACCENT))
            g.setColorAt(1, QColor(ACCENT2))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(c, d / 2 * scale, d / 2 * scale)
            col = QColor("white")
        else:
            if self.backdrop:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, 105))
                p.drawEllipse(c, min(r.width(), r.height()) / 2 - 1, min(r.width(), r.height()) / 2 - 1)
            alpha = 30 * h + 24 * pr
            if alpha > 0.5:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, int(alpha)))
                rad = min(r.width(), r.height()) / 2
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), rad, rad)
            col = QColor(ACCENT2) if self.active else QColor(255, 255, 255, 235)
            scale = 1 + 0.10 * h - 0.10 * pr

        if self._text is not None:
            p.setFont(ui_font(12, True))
            p.setPen(col)
            p.drawText(r, Qt.AlignCenter, self._text)
        elif self._kind:
            sz = self._icon_px * scale
            rect = QRectF(c.x() - sz / 2, c.y() - sz / 2, sz, sz)
            kind = "play" if self._kind in ("play", "pause") else self._kind
            draw_icon(p, kind, rect, col, self._morph)


# ======================================================================
#  Seek bar (time bubble while hovering)
# ======================================================================
class SeekBar(QWidget):
    scrubbed = Signal(float)   # while dragging
    sought = Signal(float)     # on release / click
    PAD = 9

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFixedHeight(54)   # room above for the time bubble (was 38; the bubble got clipped)
        self.setCursor(Qt.PointingHandCursor)
        self.duration = 0.0
        self.marks = []          # [(fraction, note)] bookmarks
        self.dragging = False
        self._frac = 0.0
        self._hover_x = None
        self._grow = 0.0
        self._hold_until = 0.0
        self._grow_anim = Tween(self, self._set_grow, 160)
        self._smooth = Tween(self, self._set_frac, 240, QEasingCurve.Linear)

    def _set_grow(self, v):
        self._grow = float(v)
        self.update()

    def _set_frac(self, v):
        self._frac = float(v)
        self.update()

    def set_duration(self, d):
        self.duration = float(d or 0.0)

    def set_marks(self, marks):
        self.marks = list(marks)
        self.update()

    def set_fraction(self, f):
        # While dragging and right after release (until the seek completes), ignore mpv's stale position.
        if self.dragging or time.monotonic() < self._hold_until:
            return
        f = max(0.0, min(1.0, f))
        if abs(f - self._frac) > 0.12 or f == 0.0:
            self._smooth.stop()
            self._set_frac(f)
        elif abs(f - self._frac) > 1e-5:
            self._smooth.run(self._frac, f)

    def _x_to_frac(self, x):
        w = self.width() - 2 * self.PAD
        return max(0.0, min(1.0, (x - self.PAD) / w)) if w > 0 else 0.0

    def enterEvent(self, e):
        self._grow_anim.run(self._grow, 1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover_x = None
        self._grow_anim.run(self._grow, 0.0)
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.dragging = True
            self._smooth.stop()
            self._frac = self._x_to_frac(e.position().x())
            self.scrubbed.emit(self._frac)
            self.update()

    def mouseMoveEvent(self, e):
        self._hover_x = e.position().x()
        if self.dragging:
            self._frac = self._x_to_frac(self._hover_x)
            self.scrubbed.emit(self._frac)
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.dragging:
            self.dragging = False
            self._hold_until = time.monotonic() + 0.45
            self.sought.emit(self._frac)
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        x0, x1 = self.PAD, self.width() - self.PAD
        cy = self.height() - 11
        g = self._grow
        th = 3 + 3 * g
        fx = x0 + self._frac * (x1 - x0)

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 55))
        p.drawRoundedRect(QRectF(x0, cy - th / 2, x1 - x0, th), th / 2, th / 2)

        if self._hover_x is not None and not self.dragging and self._hover_x > fx:
            hx = max(x0, min(x1, self._hover_x))
            p.setBrush(QColor(255, 255, 255, 50))
            p.drawRoundedRect(QRectF(x0, cy - th / 2, hx - x0, th), th / 2, th / 2)

        if fx > x0:
            grad = QLinearGradient(x0, 0, max(x0 + 1, fx), 0)
            grad.setColorAt(0, QColor(ACCENT))
            grad.setColorAt(1, QColor(ACCENT2))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(QRectF(x0, cy - th / 2, fx - x0, th), th / 2, th / 2)

        if self.marks:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(WARM))
            for mf, _note in self.marks:
                mx = x0 + max(0.0, min(1.0, mf)) * (x1 - x0)
                p.drawRoundedRect(QRectF(mx - 1.5, cy - th / 2 - 4, 3, th + 8), 1.5, 1.5)

        r = 3 + 4.5 * g + (1.5 if self.dragging else 0)
        glow = QRadialGradient(QPointF(fx, cy), r * 2.6)
        glow.setColorAt(0.4, qcolor(ACCENT2, 90 * g))
        glow.setColorAt(1.0, qcolor(ACCENT2, 0))
        p.setBrush(QBrush(glow))
        p.drawEllipse(QPointF(fx, cy), r * 2.6, r * 2.6)
        p.setBrush(QColor("white"))
        p.drawEllipse(QPointF(fx, cy), r, r)

        if self._hover_x is not None and self.duration > 0 and g > 0.05:
            frac = self._frac if self.dragging else self._x_to_frac(self._hover_x)
            text = fmt_time(frac * self.duration)
            ref_x = fx if self.dragging else self._hover_x
            for mf, note in self.marks:
                if note and abs(x0 + mf * (x1 - x0) - ref_x) < 7:
                    text += "  ·  " + note
                    break
            p.setFont(ui_font(11, True))
            tw = QFontMetrics(p.font()).horizontalAdvance(text) + 18
            bx = max(0, min(self.width() - tw, (self._hover_x if not self.dragging else fx) - tw / 2))
            by = cy - 13 - 22 - 4 * (1 - g)
            p.setOpacity(g)
            p.setBrush(QColor(12, 13, 18, 235))
            p.setPen(QPen(QColor(255, 255, 255, 40), 1))
            p.drawRoundedRect(QRectF(bx, by, tw, 22), 8, 8)
            p.setPen(QColor("white"))
            p.drawText(QRectF(bx, by, tw, 22), Qt.AlignCenter, text)


# ======================================================================
#  Volume slider + group that expands on hover
# ======================================================================
class VolumeSlider(QWidget):
    valueChanged = Signal(int)
    pressed = Signal()
    released = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setCursor(Qt.PointingHandCursor)
        self._v = 100
        self._drag = False
        self._wheel = WheelStepper()

    def value(self):
        return self._v

    def setValue(self, v):
        v = max(0, min(130, int(v)))
        if v != self._v:
            self._v = v
            self.update()
            self.valueChanged.emit(v)

    def _from_x(self, x):
        w = self.width() - 16
        if w > 0:
            self.setValue(round((x - 8) / w * 130))

    def mousePressEvent(self, e):
        self._drag = True
        self.pressed.emit()
        self._from_x(e.position().x())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._from_x(e.position().x())

    def mouseReleaseEvent(self, e):
        self._drag = False
        self.released.emit()

    def wheelEvent(self, e):
        steps = self._wheel.feed(e)
        if steps:
            self.setValue(self._v + 5 * steps)
        e.accept()

    def paintEvent(self, _e):
        if self.width() < 14:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        x0, x1 = 8, self.width() - 8
        cy = self.height() / 2
        span = x1 - x0
        fx = x0 + self._v / 130.0 * span
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 55))
        p.drawRoundedRect(QRectF(x0, cy - 2, span, 4), 2, 2)
        x100 = x0 + 100 / 130.0 * span
        grad = QLinearGradient(x0, 0, x100, 0)
        grad.setColorAt(0, QColor(ACCENT))
        grad.setColorAt(1, QColor(ACCENT2))
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(QRectF(x0, cy - 2, min(fx, x100) - x0, 4), 2, 2)
        if fx > x100:
            p.setBrush(QColor(WARM))
            p.drawRoundedRect(QRectF(x100, cy - 2, fx - x100, 4), 2, 2)
        p.setBrush(QColor("white"))
        p.drawEllipse(QPointF(fx, cy), 6, 6)


class VolumeGroup(QWidget):
    """Hovering over the icon slides the volume bar open sideways."""
    FULL = 98

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.btn = IconButton("vol", 40, 20)
        self.slider = VolumeSlider()
        self.slider.setFixedWidth(0)
        lay.addWidget(self.btn)
        lay.addWidget(self.slider)
        self._anim = Tween(self, lambda v: self.slider.setFixedWidth(int(v)), 240)
        self.slider.released.connect(self._maybe_collapse)

    def enterEvent(self, e):
        self._anim.run(self.slider.width(), self.FULL)
        super().enterEvent(e)

    def leaveEvent(self, e):
        if not self.slider._drag:
            self._anim.run(self.slider.width(), 0)
        super().leaveEvent(e)

    def _maybe_collapse(self):
        if not self.underMouse():
            self._anim.run(self.slider.width(), 0)


# ======================================================================
#  Glass surfaces
# ======================================================================
class GlassBar(QWidget):
    SHADOW = 16

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self.SHADOW
        body = QRectF(self.rect()).adjusted(s + 0.5, s + 0.5, -s - 0.5, -s - 0.5)
        p.setPen(Qt.NoPen)
        for i in range(12):
            p.setBrush(QColor(0, 0, 0, 9))
            p.drawRoundedRect(body.adjusted(-i, -i + 5, i, i + 5), 22 + i, 22 + i)
        p.setBrush(QColor(17, 18, 24, 214))
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawRoundedRect(body, 22, 22)
        hl = QLinearGradient(body.topLeft(), body.topRight())   # thin highlight on the top edge
        hl.setColorAt(0, QColor(255, 255, 255, 0))
        hl.setColorAt(0.5, QColor(255, 255, 255, 38))
        hl.setColorAt(1, QColor(255, 255, 255, 0))
        p.setPen(QPen(QBrush(hl), 1))
        p.drawLine(QPointF(body.left() + 24, body.top() + 0.5), QPointF(body.right() - 24, body.top() + 0.5))


class TopScrim(QWidget):
    """Dark overlay at the top + file name and technical info (transparent to mouse events)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.title = ""
        self.info = ""
        self.right_inset = 0

    def set_right_inset(self, px):
        if px != self.right_inset:
            self.right_inset = px
            self.update()

    def set_texts(self, title, info):
        if (title, info) == (self.title, self.info):
            return
        self.title, self.info = title, info
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor(0, 0, 0, 185))
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), QBrush(g))
        p.setRenderHint(QPainter.TextAntialiasing)
        avail = max(50, self.width() - self.right_inset - 220)
        p.setFont(ui_font(16, True))
        p.setPen(QColor(255, 255, 255, 240))
        p.drawText(QPointF(28, 38), QFontMetrics(p.font()).elidedText(self.title, Qt.ElideRight, avail))
        if self.info:
            p.setFont(ui_font(12))
            p.setPen(QColor(255, 255, 255, 170))
            p.drawText(QPointF(28, 58), QFontMetrics(p.font()).elidedText(self.info, Qt.ElideRight, avail))


class Toast(QWidget):
    """Pill notification that slides in and fades out (instead of the mpv OSD)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.text = ""
        self._op = 0.0
        self._tween = Tween(self, self._set_op, 180)
        self._hold = QTimer(self, singleShot=True)
        self._hold.timeout.connect(lambda: self._tween.run(self._op, 0.0, 380))
        self.hide()

    def _set_op(self, v):
        self._op = float(v)
        self.setVisible(self._op > 0.01)
        self.reposition()
        self.update()

    def reposition(self):
        pw = self.parentWidget().width() if self.parentWidget() else 800
        self.move(int((pw - self.width()) / 2), int(70 - (1 - self._op) * 16))

    def show_text(self, text):
        self.text = text
        w = QFontMetrics(ui_font(13, True)).horizontalAdvance(text) + 44
        self.resize(w, 40)
        self.reposition()
        self.update()
        self._hold.stop()
        self._tween.run(self._op, 1.0, 180)
        self._hold.start(1700)
        self.raise_()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(self._op)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(QColor(17, 18, 24, 225))
        p.drawRoundedRect(r, 20, 20)
        p.setPen(QColor("white"))
        p.setFont(ui_font(13, True))
        p.drawText(r, Qt.AlignCenter, self.text)


class VolumeHud(QWidget):
    """Volume level indicator that appears over the video while the volume changes."""
    W, H = 250, 48

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.resize(self.W, self.H)
        self.value = 100
        self.muted = False
        self._op = 0.0
        self._avail = 800
        self._tween = Tween(self, self._set_op, 140)
        self._hold = QTimer(self, singleShot=True)
        self._hold.timeout.connect(lambda: self._tween.run(self._op, 0.0, 380))
        self.hide()

    def _set_op(self, v):
        self._op = float(v)
        self.setVisible(self._op > 0.01)
        self.reposition()
        self.update()

    def reposition(self, avail=None):
        if avail is not None:
            self._avail = avail
        self.move(int((self._avail - self.W) / 2), int(70 - (1 - self._op) * 16))

    def show_value(self, value, muted=False):
        self.value, self.muted = int(value), bool(muted)
        self._hold.stop()
        self._tween.run(self._op, 1.0, 120)
        self._hold.start(1400)
        self.raise_()
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setOpacity(self._op)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(QColor(17, 18, 24, 225))
        p.drawRoundedRect(r, 24, 24)

        muted = self.muted or self.value == 0
        kind = "mute" if muted else ("vol_low" if self.value < 45 else "vol")
        draw_icon(p, kind, QRectF(16, 12, 24, 24), QColor("white"))

        x0, x1 = 52.0, self.width() - 72.0
        cy = self.height() / 2
        span = x1 - x0
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 55))
        p.drawRoundedRect(QRectF(x0, cy - 2.5, span, 5), 2.5, 2.5)
        fx = x0 + (0 if muted else self.value / 130.0) * span
        x100 = x0 + 100 / 130.0 * span
        if fx > x0 + 0.5:
            grad = QLinearGradient(x0, 0, x100, 0)
            grad.setColorAt(0, QColor(ACCENT))
            grad.setColorAt(1, QColor(ACCENT2))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(QRectF(x0, cy - 2.5, min(fx, x100) - x0, 5), 2.5, 2.5)
            if fx > x100:
                p.setBrush(QColor(WARM))
                p.drawRoundedRect(QRectF(x100, cy - 2.5, fx - x100, 5), 2.5, 2.5)
        p.setBrush(QColor("white"))
        p.drawEllipse(QPointF(fx, cy), 6, 6)

        p.setPen(QColor(WARM) if (not muted and self.value > 100) else QColor("white"))
        p.setFont(ui_font(13, True))
        p.drawText(QRectF(self.width() - 66, 0, 54, self.height()),
                   Qt.AlignRight | Qt.AlignVCenter, "Muted" if muted else f"{self.value}%")


class Pulse(QWidget):
    """Ring that grows and fades in the center on play/pause and ±10 s skips."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.resize(140, 140)
        self.kind = "play"
        self.text = None
        self._t = 1.0
        self._tween = Tween(self, self._set_t, 560, QEasingCurve.OutCubic)
        self.hide()

    def _set_t(self, v):
        self._t = float(v)
        if self._t >= 0.999:
            self.hide()
        self.update()

    def trigger(self, kind, center, text=None):
        self.kind, self.text = kind, text
        self.move(int(center.x() - 70), int(center.y() - 70))
        self.show()
        self.raise_()
        self._tween.run(0.0, 1.0)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self._t
        op = max(0.0, 1.0 - t) ** 1.2
        scale = 0.72 + 0.5 * t
        c = QPointF(self.width() / 2, self.height() / 2)
        rad = 50 * scale
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, int(150 * op)))
        p.drawEllipse(c, rad, rad)
        p.setPen(QPen(QColor(255, 255, 255, int(90 * op)), 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, rad, rad)
        col = QColor(255, 255, 255, int(255 * op))
        if self.text:
            p.setPen(col)
            p.setFont(ui_font(int(17 * scale), True))
            p.drawText(QRectF(c.x() - rad, c.y() - rad, rad * 2, rad * 2), Qt.AlignCenter, self.text)
        else:
            sz = 34 * scale
            draw_icon(p, "play", QRectF(c.x() - sz / 2, c.y() - sz / 2, sz, sz), col,
                      1.0 if self.kind == "pause" else 0.0)


class Curtain(QWidget):
    """Black curtain that gives a smooth fade-in over the video while a file opens."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._op = 0.0
        self._tween = Tween(self, self._set_op, 500, QEasingCurve.InOutQuad)
        self.hide()

    def _set_op(self, v):
        self._op = float(v)
        self.setVisible(self._op > 0.01)
        self.update()

    def cover(self):
        self._tween.stop()
        self._set_op(1.0)

    def fade_in(self, ms=120):
        self._tween.run(self._op, 1.0, ms)

    def fade_out(self, ms=500):
        if self._op > 0.01:
            self._tween.run(self._op, 0.0, ms)

    def paintEvent(self, _e):
        QPainter(self).fillRect(self.rect(), QColor(0, 0, 0, int(255 * self._op)))


# ======================================================================
#  Idle screen: animated gradient + pulsing logo
# ======================================================================
class IdleScreen(QWidget):
    open_clicked = Signal()
    clicked = Signal()
    wheel_scrolled = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self._wheel = WheelStepper()
        self.hint = "Drag a file here or press Ctrl+O to open it"
        self.heading = APP_NAME
        self._t0 = time.monotonic()
        self._hover = 0.0
        self._over = False
        self._timer = QTimer(self, interval=33)
        self._timer.timeout.connect(self._tick)

    def showEvent(self, e):
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def _tick(self):
        self._hover += ((1.0 if self._over else 0.0) - self._hover) * 0.22
        self.update()

    def _btn_rect(self):
        w, h = self.width(), self.height()
        return QRectF(w / 2 - 70, h / 2 + 112, 140, 42)

    def mouseMoveEvent(self, e):
        over = self._btn_rect().contains(e.position())
        if over != self._over:
            self._over = over
            self.setCursor(Qt.PointingHandCursor if over else Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self._btn_rect().contains(e.position()):
                self.open_clicked.emit()
            else:
                self.clicked.emit()

    def wheelEvent(self, e):
        steps = self._wheel.feed(e)
        if steps:
            self.wheel_scrolled.emit(steps)
        e.accept()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        w, h = self.width(), self.height()
        t = time.monotonic() - self._t0
        rect = self.rect()
        p.fillRect(rect, QColor(9, 10, 15))

        blobs = ((ACCENT, 0.23, 0.17, 0.0, 0.62), (ACCENT2, 0.19, 0.27, 2.1, 0.55),
                 ("#ec4899", 0.15, 0.21, 4.2, 0.50))
        for color, fx, fy, ph, rad in blobs:
            cx = w * (0.5 + 0.34 * math.sin(t * fx + ph))
            cy = h * (0.5 + 0.30 * math.cos(t * fy + ph * 1.7))
            r = max(w, h) * rad
            g = QRadialGradient(QPointF(cx, cy), r)
            g.setColorAt(0, qcolor(color, 78))
            g.setColorAt(1, qcolor(color, 0))
            p.fillRect(rect, QBrush(g))

        cx, cy = w / 2, h / 2 - 46
        pulse = 1 + 0.03 * math.sin(t * 2.0)
        glow = QRadialGradient(QPointF(cx, cy), 130)
        glow.setColorAt(0, qcolor(ACCENT, 70 + 30 * math.sin(t * 2.0)))
        glow.setColorAt(1, qcolor(ACCENT, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QPointF(cx, cy), 130, 130)

        size = 96 * pulse
        g = QLinearGradient(cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2)
        g.setColorAt(0, QColor(ACCENT))
        g.setColorAt(1, QColor(ACCENT2))
        p.setBrush(QBrush(g))
        p.drawRoundedRect(QRectF(cx - size / 2, cy - size / 2, size, size), size * 0.28, size * 0.28)
        s = size / 100.0
        p.setBrush(QColor("white"))
        p.setPen(QPen(QColor("white"), 3 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolygon(QPolygonF([QPointF(cx - 12 * s, cy - 20 * s), QPointF(cx + 22 * s, cy),
                                 QPointF(cx - 12 * s, cy + 20 * s)]))

        margin = 40
        p.setPen(QColor(255, 255, 255, 245))
        p.setFont(ui_font(28, True))
        heading = QFontMetrics(p.font()).elidedText(self.heading, Qt.ElideMiddle, max(50, w - 2 * margin))
        p.drawText(QRectF(margin, cy + 62, w - 2 * margin, 40), Qt.AlignHCenter | Qt.AlignVCenter, heading)
        p.setPen(QColor(255, 255, 255, 140))
        p.setFont(ui_font(13))
        p.drawText(QRectF(0, cy + 104, w, 24), Qt.AlignHCenter | Qt.AlignVCenter, self.hint)

        br = self._btn_rect()
        br = br.adjusted(-4 * self._hover, -2 * self._hover, 4 * self._hover, 2 * self._hover)
        bg = QLinearGradient(br.topLeft(), br.bottomRight())
        bg.setColorAt(0, qcolor(ACCENT, 70 + 120 * self._hover))
        bg.setColorAt(1, qcolor(ACCENT2, 70 + 120 * self._hover))
        p.setBrush(QBrush(bg))
        p.setPen(QPen(QColor(255, 255, 255, 60 + int(80 * self._hover)), 1))
        p.drawRoundedRect(br, 21, 21)
        p.setPen(QColor("white"))
        p.setFont(ui_font(13, True))
        p.drawText(br, Qt.AlignCenter, "Open file")


# ======================================================================
#  Playlist drawer sliding in from the right
# ======================================================================
class Drawer(QWidget):
    WIDTH = 360
    revealChanged = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.reveal = 0.0
        self.is_open = False
        self._tween = Tween(self, self._set_reveal, 380, QEasingCurve.OutCubic)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 12, 14)
        lay.setSpacing(10)
        head = QHBoxLayout()
        title = QLabel("Playlist")
        title.setObjectName("drawerTitle")
        self.count = QLabel("")
        self.count.setObjectName("drawerCount")
        self.btn_close = IconButton("close", 34, 16)
        head.addWidget(title)
        head.addWidget(self.count)
        head.addStretch(1)
        head.addWidget(self.btn_close)
        lay.addLayout(head)
        self.list = QListWidget()
        self.list.setObjectName("playlist")
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        lay.addWidget(self.list, 1)
        self.setFixedWidth(self.WIDTH)
        self.hide()

    def _set_reveal(self, v):
        self.reveal = float(v)
        self.place()
        self.revealChanged.emit()

    def set_open(self, on):
        self.is_open = bool(on)
        self._tween.run(self.reveal, 1.0 if on else 0.0, 380 if on else 300)

    def place(self, W=None, H=None):
        par = self.parentWidget()
        W = W if W is not None else par.width()
        H = H if H is not None else par.height()
        self.setGeometry(int(W - self.WIDTH * self.reveal), 0, self.WIDTH, H)
        self.setVisible(self.reveal > 0.002)
        if self.reveal > 0.002:
            self.raise_()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(14, 15, 21, 252))
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.drawLine(0, 0, 0, self.height())


class ClickLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class Stage(QWidget):
    resized = Signal()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()


# ======================================================================
#  Media info window (refreshes live)
# ======================================================================
class InfoDialog(QDialog):
    def __init__(self, parent, provider):
        super().__init__(parent)
        self.setWindowTitle("Media info")
        self.resize(580, 640)
        self._provider = provider
        self._last = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 14)
        lay.setSpacing(12)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        lay.addWidget(self.view, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_copy = QPushButton("Copy")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.view.toPlainText()))
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        row.addWidget(btn_copy)
        row.addWidget(btn_close)
        lay.addLayout(row)

        self._timer = QTimer(self, interval=1000)
        self._timer.timeout.connect(self.refresh)

    def refresh(self):
        try:
            text = self._provider()
        except Exception as exc:
            text = f"Could not read info: {exc}"
        if text == self._last:
            return
        self._last = text
        bar = self.view.verticalScrollBar()
        pos = bar.value()
        self.view.setPlainText(text)
        bar.setValue(pos)

    def showEvent(self, e):
        self._last = None
        self.refresh()
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)


# ======================================================================
#  mpv video surface
# ======================================================================
def _get_proc_address(_ctx, name):
    gl = QOpenGLContext.currentContext()
    if gl is None:
        return 0
    try:
        addr = gl.getProcAddress(name)
        return int(addr) if addr else 0
    except Exception:
        return 0


class MpvVideoWidget(QOpenGLWidget):
    """Video surface that mpv draws onto OpenGL through its render API."""

    update_requested = Signal()
    file_loaded = Signal()
    load_failed = Signal(str)
    left_clicked = Signal()
    double_clicked = Signal(int)
    wheel_scrolled = Signal(int)     # notch count (+ up / - down)
    hold_started = Signal()
    hold_ended = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 180)
        self.ctx = None
        self._swallow_release = False
        self._holding = False
        self._wheel = WheelStepper()
        self._hold_timer = QTimer(self, singleShot=True, interval=380)
        self._hold_timer.timeout.connect(self._on_hold_timeout)
        self._last_error = ""
        self._proc_fn = MpvGlGetProcAddressFn(_get_proc_address)

        def _log(level, prefix, text):
            if level in ("error", "fatal"):
                if "OpenGL error" in text:      # harmless leftover GL error queue from Qt
                    return
                self._last_error = text.strip()
                print(f"[mpv/{prefix}] {text.strip()}", file=sys.stderr)

        pictures = Path.home() / "Pictures"
        self.mpv = mpv.MPV(
            vo="libmpv",
            config=False,                # also read the user's mpv.conf
            input_default_bindings=False,
            input_vo_keyboard=False,
            osc=False,
            osd_level=0,
            osd_bar=False,
            keep_open=True,
            save_position_on_quit=True,
            volume_max=130,
            ytdl=True,                  # if yt-dlp is installed, web links open too
            screenshot_directory=str(pictures if pictures.exists() else Path.home()),
            screenshot_format="png",
            sub_auto="fuzzy",
            audio_file_auto="fuzzy",
            log_handler=_log,
            loglevel="error",
        )

        # Hardware decoding: pick the first mode that works on this platform (GPU if available: NVDEC, VAAPI, VideoToolbox, D3D11VA)
        self.hwdec_mode = "no"
        for mode in HWDEC_CANDIDATES:
            try:
                self.mpv.hwdec = mode
                self.hwdec_mode = mode
                break
            except Exception:
                continue

        @self.mpv.event_callback("file-loaded")
        def _on_file_loaded(_event):
            self.file_loaded.emit()

        @self.mpv.event_callback("end-file")
        def _on_end_file(event):
            try:
                reason = event.as_dict().get("reason")
                if isinstance(reason, bytes):
                    reason = reason.decode("utf-8", "ignore")
                if reason in ("error", 4):
                    self.load_failed.emit(self._last_error or "Could not play the file")
            except Exception:
                pass

        self.update_requested.connect(self.update)

    def set_hwdec(self, on):
        try:
            self.mpv.hwdec = self.hwdec_mode if on and self.hwdec_mode != "no" else (
                HWDEC_CANDIDATES[0] if on else "no")
        except Exception:
            pass

    # --- OpenGL ---
    def _drain_gl_errors(self):
        """Clears stale errors Qt left in the GL error queue (libmpv would report them by mistake)."""
        try:
            f = self.context().functions()
            for _ in range(32):
                if f.glGetError() == 0:
                    break
        except Exception:
            pass

    def initializeGL(self):
        self._drain_gl_errors()
        if self.ctx is not None:
            # If the GL context was recreated, release the old render context (mpv accepts only one).
            try:
                self.ctx.free()
            except Exception:
                pass
            self.ctx = None
        self.ctx = MpvRenderContext(
            self.mpv, "opengl",
            opengl_init_params={"get_proc_address": self._proc_fn},
        )
        self.ctx.update_cb = self.update_requested.emit
        self._drain_gl_errors()

    def paintGL(self):
     if not self.ctx:
        return
     ratio = self.devicePixelRatioF()
     f = self.context().functions()
     f.glClearColor(0.0, 0.0, 0.0, 1.0)
     f.glClear(0x00004000)          # GL_COLOR_BUFFER_BIT
     self.ctx.render(
        flip_y=True,
        opengl_fbo={
            "w": round(self.width() * ratio),
            "h": round(self.height() * ratio),
            "fbo": self.defaultFramebufferObject(),
        },
    )

    def shutdown(self):
        if self.ctx is not None:
            try:
                self.makeCurrent()
                self.ctx.free()
                self.doneCurrent()
            except Exception:
                pass
            self.ctx = None
        if self.mpv is not None:
            try:
                self.mpv.terminate()
            except Exception:
                pass
            self.mpv = None

    # --- mouse events ---
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._holding = False
            self._hold_timer.start()
        super().mousePressEvent(event)

    def _on_hold_timeout(self):
        self._holding = True
        self.hold_started.emit()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._hold_timer.stop()
            if self._holding:           # hold finished: don't count it as a single click
                self._holding = False
                self.hold_ended.emit()
                super().mouseReleaseEvent(event)
                return
            if self._swallow_release:       # the second release of a double click must not count as a single click
                self._swallow_release = False
            else:
                self.left_clicked.emit()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._swallow_release = True
            self.double_clicked.emit(int(event.position().x()))

    def wheelEvent(self, event):
        steps = self._wheel.feed(event)
        if steps:
            self.wheel_scrolled.emit(steps)
        event.accept()


# ======================================================================
#  Main window
# ======================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1180, 700)
        self.setMinimumSize(640, 400)
        self.setAcceptDrops(True)
        self.settings = QSettings("MdAPlayer", "MdAPlayer")
        self.show_remaining = False
        self.loop_state = 0
        self.playlist_on = False
        self._pl_sig = None
        self._ctrl_target = True     # are the controls visible (target)
        self._reveal = 1.0           # 0..1 animation value
        self._last_mouse = None
        self._effects = []
        self._media_title = ""
        self._info_done = True
        self._info_tries = 0
        self._no_video_polls = 0
        self._last_scrub = 0.0
        self._speed_label = "1×"
        self._info_dlg = None
        self._was_maximized = False
        self._fs_busy = False
        self._marks_sig = None
        self._sleep_deadline = None
        self._sleep_eof = False
        self._sleep_fading = False
        self._sleep_actions = []
        self._night = False
        self._auto_paused = False
        self._hold_active = False
        self._hold_prev_speed = 1.0
        self.auto_pause = str(self.settings.value("auto_pause", "false")).lower() in ("true", "1")

        self.stage = Stage()
        self.stage.setObjectName("stage")
        self.stage.setAttribute(Qt.WA_StyledBackground, True)
        self.setCentralWidget(self.stage)

        self.video = MpvVideoWidget(self.stage)
        self.mpv = self.video.mpv

        self._build_overlays()
        self._build_menus()
        self._build_shortcuts()

        self.video.setContextMenuPolicy(Qt.CustomContextMenu)
        self.video.customContextMenuRequested.connect(
            lambda pos: self.main_menu.exec(self.video.mapToGlobal(pos)))
        self.idle.setContextMenuPolicy(Qt.CustomContextMenu)
        self.idle.customContextMenuRequested.connect(
            lambda pos: self.main_menu.exec(self.idle.mapToGlobal(pos)))

        self.video.file_loaded.connect(self._on_file_loaded)
        self.video.load_failed.connect(self._on_load_failed)
        self.video.left_clicked.connect(self._on_left_click)
        self.video.double_clicked.connect(self._on_double_click)
        self.video.hold_started.connect(self._on_hold_start)
        self.video.hold_ended.connect(self._on_hold_end)
        self.video.wheel_scrolled.connect(lambda steps: self.change_volume(5 * steps))
        self.idle.wheel_scrolled.connect(lambda steps: self.change_volume(5 * steps))
        self.idle.clicked.connect(self._on_idle_click)
        self.volume.slider.pressed.connect(self._unmute)
        self.stage.resized.connect(self._relayout)
        self.drawer.revealChanged.connect(self._place_overlays)

        self.click_timer = QTimer(self, singleShot=True)
        self.click_timer.timeout.connect(self.toggle_pause)

        self.hide_timer = QTimer(self, singleShot=True, interval=3000)
        self.hide_timer.timeout.connect(self._maybe_hide)

        self.curtain_timer = QTimer(self, singleShot=True, interval=6000)
        self.curtain_timer.timeout.connect(self.curtain.fade_out)

        self.reveal_anim = Tween(self, self._apply_reveal, 240)

        self.poll = QTimer(self, interval=200)
        self.poll.timeout.connect(self._poll_state)
        self.poll.start()

        QApplication.instance().installEventFilter(self)
        QApplication.instance().applicationStateChanged.connect(self._on_app_state)

        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        try:
            volume = max(0, min(130, int(self.settings.value("volume", 100))))
        except (TypeError, ValueError):
            volume = 100
        self.volume.slider.setValue(volume)
        self.mpv.volume = volume
        self._relayout()

    # ------------------------------------------------------------------
    #  Safe property reading
    # ------------------------------------------------------------------
    def p(self, name, default=None):
        """Reads an mpv property; returns `default` if it isn't ready or there is an error."""
        if self.mpv is None:
            return default
        # Depending on the python-mpv version, mpv["property"] may not work; attribute access (mpv.media_title) is tried first,
        # nested names like "video-params/w" use dictionary keys.
        head, *rest = name.split("/")
        try:
            value = getattr(self.mpv, head.replace("-", "_"))
        except Exception:
            try:
                value = self.mpv[head]
            except Exception:
                return default
        for key in rest:
            if isinstance(value, dict):
                value = value.get(key)
            elif isinstance(value, (list, tuple)):
                try:
                    value = value[int(key)]
                except (ValueError, IndexError):
                    value = None
            else:
                value = None
            if value is None:
                break
        return default if value is None or value == "" else value

    # ------------------------------------------------------------------
    #  UI
    # ------------------------------------------------------------------
    def _fader(self, widget):
        eff = QGraphicsOpacityEffect(widget)
        eff.setOpacity(1.0)
        widget.setGraphicsEffect(eff)
        self._effects.append((widget, eff))

    def _build_overlays(self):
        st = self.stage

        self.idle = IdleScreen(st)
        self.idle.open_clicked.connect(self.open_files_dialog)
        self.curtain = Curtain(st)
        self.pulse = Pulse(st)
        self.scrim = TopScrim(st)

        self.btn_playlist = IconButton("playlist", 42, 20, backdrop=True, parent=st)
        self.btn_playlist.setToolTip("Playlist (Ctrl+L)")
        self.btn_playlist.clicked.connect(lambda: self.set_playlist_visible(not self.playlist_on))
        self.btn_info = IconButton("info", 42, 20, backdrop=True, parent=st)
        self.btn_info.setToolTip("Media info (Ctrl+I)")
        self.btn_info.clicked.connect(lambda: self.toggle_media_info())
        self.btn_more = IconButton("more", 42, 20, backdrop=True, parent=st)
        self.btn_more.setToolTip("Menu")
        self.btn_more.clicked.connect(lambda: self._show_main_menu())
        for b in (self.btn_playlist, self.btn_info, self.btn_more):
            b.setFixedSize(b.sizeHint())

        # --- floating control bar at the bottom ---
        self.bar = GlassBar(st)
        s = GlassBar.SHADOW
        v = QVBoxLayout(self.bar)
        v.setContentsMargins(s + 18, s + 8, s + 18, s + 10)
        v.setSpacing(0)

        self.seekbar = SeekBar()
        self.seekbar.scrubbed.connect(self._on_scrub)
        self.seekbar.sought.connect(self._on_sought)
        v.addWidget(self.seekbar)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.btn_prev = IconButton("prev", 42, 20)
        self.btn_prev.setToolTip("Previous (P)")
        self.btn_prev.clicked.connect(lambda: self.prev_item())
        self.btn_play = IconButton("pause", 54, 24, primary=True)
        self.btn_play.setToolTip("Play / Pause (Space)")
        self.btn_play.clicked.connect(lambda: self.toggle_pause())
        self.btn_next = IconButton("next", 42, 20)
        self.btn_next.setToolTip("Next (N)")
        self.btn_next.clicked.connect(lambda: self.next_item())
        for b in (self.btn_prev, self.btn_play, self.btn_next):
            row.addWidget(b)
        row.addSpacing(12)

        self.time_label = ClickLabel("00:00  /  00:00")
        self.time_label.setObjectName("timeLabel")
        self.time_label.setMinimumWidth(170)
        self.time_label.setCursor(Qt.PointingHandCursor)
        self.time_label.setToolTip("Toggle remaining / total time")
        self.time_label.clicked.connect(self._toggle_remaining)
        row.addWidget(self.time_label)
        row.addStretch(1)

        self.volume = VolumeGroup()
        self.volume.btn.setToolTip("Mute (M)")
        self.volume.btn.clicked.connect(lambda: self.toggle_mute())
        self.volume.slider.valueChanged.connect(self._on_volume_changed)
        row.addWidget(self.volume)

        self.btn_loop = IconButton("loop", 40, 20)
        self.btn_loop.setToolTip("Repeat: off / playlist / single file (L)")
        self.btn_loop.clicked.connect(lambda: self.cycle_loop())
        self.btn_speed = IconButton(text="1×", size=40)
        self.btn_speed.setToolTip("Playback speed")
        self.btn_speed.clicked.connect(lambda: self._show_speed_menu())
        self.btn_full = IconButton("full", 40, 20)
        self.btn_full.setToolTip("Fullscreen (F)")
        self.btn_full.clicked.connect(lambda: self.toggle_fullscreen())
        for b in (self.btn_loop, self.btn_speed, self.btn_full):
            row.addWidget(b)
        v.addLayout(row)

        # --- playlist drawer + toast ---
        self.drawer = Drawer(st)
        self.drawer.btn_close.clicked.connect(lambda: self.set_playlist_visible(False))
        self.drawer.list.itemDoubleClicked.connect(self._play_playlist_item)
        self.drawer.list.customContextMenuRequested.connect(self._playlist_context)
        self.toast = Toast(st)
        self.vol_hud = VolumeHud(st)

        for w in (self.scrim, self.btn_playlist, self.btn_info, self.btn_more, self.bar):
            self._fader(w)

        # layer order
        for w in (self.video, self.idle, self.curtain, self.pulse, self.scrim,
                  self.btn_playlist, self.btn_info, self.btn_more, self.bar, self.drawer,
                  self.toast, self.vol_hud):
            w.raise_()

    # ------------------------------------------------------------------
    #  Layout and control hide/show animation
    # ------------------------------------------------------------------
    def _relayout(self):
        W, H = self.stage.width(), self.stage.height()
        for w in (self.video, self.idle, self.curtain):
            w.setGeometry(0, 0, W, H)
        self._place_overlays()

    def _place_overlays(self):
        W, H = self.stage.width(), self.stage.height()
        v = self._reveal
        avail = W - int(self.drawer.WIDTH * self.drawer.reveal)
        bw = max(320, min(avail - 16, 960))
        bh = self.bar.sizeHint().height()
        self.bar.setGeometry((avail - bw) // 2, H - bh + 2 + int((1 - v) * 34), bw, bh)
        self.scrim.setGeometry(0, -int((1 - v) * 18), W, 120)
        self.scrim.set_right_inset(int(self.drawer.WIDTH * self.drawer.reveal))
        dy = 14 - int((1 - v) * 12)
        x = avail - 14 - self.btn_more.width()
        self.btn_more.move(x, dy)
        x -= 6 + self.btn_playlist.width()
        self.btn_playlist.move(x, dy)
        x -= 6 + self.btn_info.width()
        self.btn_info.move(x, dy)
        self.drawer.place(W, H)
        self.toast.reposition()
        self.vol_hud.reposition(avail)

    def _apply_reveal(self, v):
        self._reveal = float(v)
        vis = self._reveal > 0.01
        for w, eff in self._effects:
            eff.setOpacity(self._reveal)
            if w.isVisible() != vis:
                w.setVisible(vis)
        self._place_overlays()

    def set_controls_visible(self, on):
        if on == self._ctrl_target:
            return
        self._ctrl_target = on
        if on:
            self.video.unsetCursor()
        self.reveal_anim.run(self._reveal, 1.0 if on else 0.0, 220 if on else 400)

    def _over_ui(self):
        pos = self.stage.mapFromGlobal(QCursor.pos())
        widgets = [self.bar, self.btn_more, self.btn_playlist, self.btn_info]
        if self.drawer.isVisible():
            widgets.append(self.drawer)
        return any(w.isVisible() and w.geometry().contains(pos) for w in widgets)

    def _maybe_hide(self):
        if self.mpv is None:
            return
        try:
            paused = bool(self.mpv.pause)
        except Exception:
            paused = True
        if (paused or self._no_video() or self._over_ui() or self.seekbar.dragging
                or QApplication.activePopupWidget() is not None):
            self.hide_timer.start()
            return
        self.set_controls_visible(False)
        self.video.setCursor(Qt.BlankCursor)

    def _activity(self):
        self.video.unsetCursor()
        if not self._ctrl_target:
            self.set_controls_visible(True)
        self.hide_timer.start()

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.MouseMove and isinstance(obj, QWidget) and obj.window() is self:
            pos = ev.globalPosition().toPoint()
            if pos != self._last_mouse:
                self._last_mouse = pos
                self._activity()
        elif t == QEvent.FileOpen:       # macOS: "Open with" from Finder
            try:
                self.open_paths([ev.file()])
                return True
            except Exception:
                pass
        return False

    # ------------------------------------------------------------------
    #  Menus
    # ------------------------------------------------------------------
    def _act(self, menu, text, slot, shortcut=None, checkable=False, register=True):
        action = QAction(text, self)
        action.setCheckable(checkable)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if checkable:
            action.toggled.connect(slot)
        else:
            action.triggered.connect(lambda _checked=False: slot())
        menu.addAction(action)
        if register:
            self.addAction(action)   # shortcuts work even when the menu is not open
        return action

    def _build_menus(self):
        mb = self.main_menu = QMenu(self)

        m = mb.addMenu("Media")
        self._act(m, "Open file…", self.open_files_dialog, "Ctrl+O")
        self._act(m, "Open folder…", self.open_folder_dialog, "Ctrl+Shift+O")
        self._act(m, "Open network stream / URL…", self.open_url_dialog, "Ctrl+U")
        self._act(m, "Add to playlist…", lambda: self.open_files_dialog(append=True), "Ctrl+Shift+A")
        self.recent_menu = m.addMenu("Recent files")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        m.addSeparator()
        self._act(m, "Quit", self.close, "Ctrl+Q")

        m = mb.addMenu("Playback")
        self._act(m, "Play / Pause", self.toggle_pause, "Space")
        self._act(m, "Forward 5 s", lambda: self.seek_rel(5), "Right")
        self._act(m, "Back 5 s", lambda: self.seek_rel(-5), "Left")
        self._act(m, "Forward 30 s", lambda: self.seek_rel(30), "Shift+Right")
        self._act(m, "Back 30 s", lambda: self.seek_rel(-30), "Shift+Left")
        self._act(m, "Next frame", lambda: self._frame_step("frame-step"), ".")
        self._act(m, "Previous frame", lambda: self._frame_step("frame-back-step"), ",")
        m.addSeparator()
        self._act(m, "Next", self.next_item, "N")
        self._act(m, "Previous", self.prev_item, "P")
        m.addSeparator()
        self._act(m, "Decrease speed", lambda: self.change_speed(-0.25), "[")
        self._act(m, "Increase speed", lambda: self.change_speed(0.25), "]")
        self._act(m, "Reset speed", lambda: self.set_speed(1.0), "Backspace")
        m.addSeparator()
        loop_menu = m.addMenu("Repeat")
        loop_group = QActionGroup(self)
        loop_group.setExclusive(True)
        self._loop_actions = []
        for i, label in enumerate(("Off", "Playlist", "Single file")):
            a = QAction(label, self)
            a.setCheckable(True)
            a.setChecked(i == 0)
            a.triggered.connect(lambda _c=False, s=i: self.set_loop(s))
            loop_group.addAction(a)
            loop_menu.addAction(a)
            self._loop_actions.append(a)
        self._act(m, "Cycle repeat mode", self.cycle_loop, "L")

        self.audio_menu = mb.addMenu("Audio")
        self.audio_menu.aboutToShow.connect(lambda: self._fill_tracks(self.audio_menu, "audio", "aid"))

        m = mb.addMenu("Video")
        self._act(m, "Fullscreen", self.toggle_fullscreen, "F")
        self._act(m, "Take screenshot", self.screenshot, "S")
        m.addSeparator()
        aspect = m.addMenu("Aspect ratio")
        for label, value in (("Auto", "-1"), ("16:9", "16:9"), ("4:3", "4:3"),
                             ("21:9", "21:9"), ("1:1", "1:1")):
            self._act(aspect, label, lambda v=value, l=label: self._set_aspect(v, l), register=False)
        hw = self._act(m, "Hardware decoding (GPU)", self.video.set_hwdec, checkable=True)
        hw.blockSignals(True)
        hw.setChecked(self.video.hwdec_mode != "no")
        hw.blockSignals(False)
        self._act(m, "Deinterlace",
                  lambda on: self._set_prop("deinterlace", "yes" if on else "no"),
                  "D", checkable=True)
        self._act(m, "High-quality scaling", self.set_hq, checkable=True)

        self.sub_menu = mb.addMenu("Subtitles")
        self.sub_menu.aboutToShow.connect(lambda: self._fill_tracks(self.sub_menu, "sub", "sid"))

        m = mb.addMenu("Tools")
        self._act(m, "Media info…", self.toggle_media_info, "Ctrl+I")
        self._act(m, "mpv statistics", self.toggle_stats, "Shift+I")
        m.addSeparator()
        self._act(m, "Audio delay −0.1 s", lambda: self.adjust_delay("audio-delay", -0.1, "Audio delay"), "Ctrl+,")
        self._act(m, "Audio delay +0.1 s", lambda: self.adjust_delay("audio-delay", 0.1, "Audio delay"), "Ctrl+.")
        self._act(m, "Subtitle delay −0.1 s", lambda: self.adjust_delay("sub-delay", -0.1, "Subtitle delay"), "Z")
        self._act(m, "Subtitle delay +0.1 s", lambda: self.adjust_delay("sub-delay", 0.1, "Subtitle delay"), "X")

        m.addSeparator()
        self._act(m, "Add bookmark", self.add_bookmark, "B")
        self._act(m, "Bookmarks…", self.show_bookmarks, "Ctrl+B")
        self._act(m, "Previous bookmark", lambda: self.goto_bookmark(-1), "Alt+Left")
        self._act(m, "Next bookmark", lambda: self.goto_bookmark(1), "Alt+Right")
        m.addSeparator()
        self.act_night = self._act(m, "Night mode (audio normalization)", self.set_night_mode, "G", checkable=True)
        self.act_autopause = self._act(m, "Auto-pause when the window loses focus", self.set_auto_pause, checkable=True)
        self.act_autopause.blockSignals(True)
        self.act_autopause.setChecked(self.auto_pause)
        self.act_autopause.blockSignals(False)
        sleep_menu = m.addMenu("Sleep timer")
        sleep_group = QActionGroup(self)
        sleep_group.setExclusive(True)
        for label, minutes, at_end in (("Off", 0, False), ("15 minutes", 15, False),
                                       ("30 minutes", 30, False), ("45 minutes", 45, False),
                                       ("60 minutes", 60, False), ("90 minutes", 90, False),
                                       ("When this file ends", 0, True)):
            a = QAction(label, self)
            a.setCheckable(True)
            a.setChecked(not minutes and not at_end)
            a.triggered.connect(lambda _c=False, mi=minutes, e=at_end: self.set_sleep_timer(mi, e))
            sleep_group.addAction(a)
            sleep_menu.addAction(a)
            self._sleep_actions.append(a)

        m = mb.addMenu("View")
        self.act_playlist = self._act(m, "Playlist", self.set_playlist_visible,
                                      "Ctrl+L", checkable=True)

        m = mb.addMenu("Help")
        self._act(m, "About MdAPlayer…", self.show_about, register=False)

    def _build_shortcuts(self):
        def sc(key, slot):
            QShortcut(QKeySequence(key), self, activated=slot)

        sc("Up", lambda: self.change_volume(5))
        sc("Down", lambda: self.change_volume(-5))
        sc("M", self.toggle_mute)
        sc("Escape", self._on_escape)
        sc("V", self.toggle_sub_visibility)
        sc("J", lambda: self.cycle_track("sub"))
        sc("A", lambda: self.cycle_track("audio"))
        sc("Return", self.toggle_fullscreen)
        sc("Enter", self.toggle_fullscreen)

    def _show_main_menu(self):
        menu = self.main_menu
        size = menu.sizeHint()
        pos = self.btn_more.mapToGlobal(QPoint(self.btn_more.width() - size.width(),
                                               self.btn_more.height() + 8))
        menu.exec(pos)

    def _show_speed_menu(self):
        menu = QMenu(self)
        cur = float(self.p("speed", 1.0))
        for sp in (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0):
            a = menu.addAction(f"{sp:g}×")
            a.setCheckable(True)
            a.setChecked(abs(cur - sp) < 1e-6)
            a.triggered.connect(lambda _c=False, v=sp: self.set_speed(v))
        size = menu.sizeHint()
        pos = self.btn_speed.mapToGlobal(QPoint(self.btn_speed.width() // 2 - size.width() // 2,
                                                -size.height() - 10))
        menu.exec(pos)

    # ------------------------------------------------------------------
    #  Opening files
    # ------------------------------------------------------------------
    @staticmethod
    def _expand(paths):
        result = []
        for p in paths:
            if not p:
                continue
            if isinstance(p, str) and "://" in p:
                result.append(p)
                continue
            path = Path(p)
            if path.is_dir():
                files = [f for f in path.iterdir() if f.is_file() and f.suffix.lower() in MEDIA_EXTS]
                result.extend(str(f) for f in sorted(files, key=lambda f: natural_key(f.name)))
            else:
                result.append(str(path))
        return result

    def open_paths(self, paths, append=False):
        items = self._expand(paths)
        if self.mpv is None:
            return
        if not items:
            if any(paths):
                self.osd("No playable media found")
            return
        self.video._last_error = ""
        first_mode = "append-play" if append else "replace"
        try:
            if not append:
                self.curtain.cover()                 # curtain for a smooth transition
                self.curtain_timer.start()           # remove the curtain if loading fails
            self.mpv.command("loadfile", items[0], first_mode)
            for item in items[1:]:
                self.mpv.command("loadfile", item, "append")
            self.mpv.pause = False
            if append:
                self.osd(f"{len(items)} item{'s' if len(items) != 1 else ''} added to the playlist")
        except Exception as exc:
            self.curtain.fade_out()
            QMessageBox.warning(self, APP_NAME, f"Could not open:\n{exc}")

    def open_files_dialog(self, append=False):
        media_filter = ("Media files" + " (" + " ".join(f"*{e}" for e in sorted(MEDIA_EXTS))
                        + ");;" + "All files" + " (*)")
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select files", self.settings.value("last_dir", str(Path.home())),
            media_filter)
        if files:
            self.settings.setValue("last_dir", str(Path(files[0]).parent))
            self.open_paths(files, append=append)

    def open_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select folder", self.settings.value("last_dir", str(Path.home())))
        if folder:
            self.settings.setValue("last_dir", folder)
            self.open_paths([folder])

    def open_url_dialog(self):
        url, ok = QInputDialog.getText(self, "Open URL", "Network address (http, rtsp, YouTube…):")
        if ok and url.strip():
            self.open_paths([url.strip()])

    @staticmethod
    def _looks_openable(text):
        text = (text or "").strip()
        if not text:
            return False
        if "://" in text:
            return True
        try:
            return os.path.exists(text)
        except (OSError, ValueError):
            return False

    def dragEnterEvent(self, event):
        md = event.mimeData()
        if md.hasUrls() or (md.hasText() and self._looks_openable(md.text())):
            event.acceptProposedAction()

    def dropEvent(self, event):
        md = event.mimeData()
        if md.hasUrls():
            paths = [u.toLocalFile() if u.isLocalFile() else u.toString() for u in md.urls()]
            paths = [x for x in paths if x]
            subs = [x for x in paths if "://" not in x and Path(x).suffix.lower() in SUB_EXTS]
            media = [x for x in paths if x not in subs]
            if media:
                self.open_paths(media)
            elif subs:
                if self.p("path"):
                    for s in subs:
                        try:
                            self.mpv.command("sub-add", s, "select")
                        except Exception:
                            pass
                    self.osd("Subtitle loaded")
                else:
                    self.osd("Open a video first")
        elif md.hasText() and self._looks_openable(md.text()):
            self.open_paths([md.text().strip()])

    # ------------------------------------------------------------------
    #  Controls
    # ------------------------------------------------------------------
    def osd(self, text):
        self.toast.show_text(text)

    def _stage_center(self, fx=0.5):
        return QPoint(int(self.stage.width() * fx), int(self.stage.height() * 0.5))

    def _set_prop(self, name, value):
        try:
            self.mpv.command("set", name, value)
        except Exception:
            pass

    def toggle_pause(self):
        if self.mpv is None:
            return
        if not self.p("path"):
            self.osd("Open a file first")
            return
        try:
            if self.p("eof-reached", False) and self.mpv.pause:    # replay a finished file from the start
                self.mpv.command("seek", "0", "absolute")
                self.mpv.pause = False
                new = False
            else:
                new = not self.mpv.pause
                self.mpv.pause = new
        except Exception:
            return
        if not self._no_video():
            self.pulse.trigger("pause" if new else "play", self._stage_center())

    def seek_rel(self, secs):
        try:
            self.mpv.command("seek", str(secs), "relative")
            self.osd(f"{'+' if secs > 0 else '−'}{abs(secs):g} s")
        except Exception:
            pass

    def _frame_step(self, command):
        try:
            self.mpv.command(command)
        except Exception:
            pass

    def next_item(self):
        try:
            self.mpv.command("playlist-next")
        except Exception:
            pass

    def prev_item(self):
        try:
            pos = self.mpv.time_pos or 0
            idx = self.p("playlist-pos", 0)
            if pos > 3 or not idx:
                self.mpv.command("seek", "0", "absolute")
            else:
                self.mpv.command("playlist-prev")
        except Exception:
            pass

    def change_volume(self, delta):
        if self.mpv is None:
            return
        if delta > 0 and self.p("mute", False):
            self.mpv.mute = False
        v = max(0, min(130, self.volume.slider.value() + delta))
        self.volume.slider.setValue(v)
        self.vol_hud.show_value(v, bool(self.p("mute", False)))

    def _on_volume_changed(self, v):
        if self.mpv is not None:
            self.mpv.volume = v
            if self.isVisible():     # don't trigger the indicator while the initial value is set at startup
                self.vol_hud.show_value(v, bool(self.p("mute", False)))

    def toggle_mute(self):
        if self.mpv is None:
            return
        self.mpv.mute = not self.mpv.mute
        self.vol_hud.show_value(self.volume.slider.value(), bool(self.mpv.mute))

    def set_speed(self, value):
        if self.mpv is None:
            return
        value = max(0.25, min(4.0, value))
        self.mpv.speed = value
        self.osd(f"Speed: {value:g}×")

    def change_speed(self, delta):
        self.set_speed(round(float(self.p("speed", 1.0)) + delta, 2))

    def adjust_delay(self, prop, delta, label):
        try:
            self.mpv.command("add", prop, str(delta))
            self.osd(f"{label}: {float(self.p(prop, 0.0)):+.1f} s")
        except Exception:
            pass

    def screenshot(self):
        try:
            self.mpv.command("screenshot")
            self.osd("Screenshot saved")
        except Exception:
            pass

    def _set_aspect(self, value, label):
        self._set_prop("video-aspect-override", value)
        self.osd(f"Aspect: {label}")

    def set_hq(self, on):
        opts = ({"scale": "ewa_lanczossharp", "cscale": "ewa_lanczossharp",
                 "dscale": "mitchell", "deband": "yes"} if on else
                {"scale": "bilinear", "cscale": "bilinear", "dscale": "hermite", "deband": "no"})
        for key, val in opts.items():
            self._set_prop(key, val)
        self.osd("High-quality scaling: " + ("on" if on else "off"))

    def toggle_stats(self):
        try:
            self.mpv.command("script-binding", "stats/display-stats-toggle")
        except Exception:
            self.osd("The statistics script is not available in this libmpv version")

    def set_loop(self, state):
        self.loop_state = state
        self._set_prop("loop-file", "inf" if state == 2 else "no")
        self._set_prop("loop-playlist", "inf" if state == 1 else "no")
        self.btn_loop.setIconKind("loop_one" if state == 2 else "loop")
        self.btn_loop.setActive(state != 0)
        if hasattr(self, "_loop_actions"):
            self._loop_actions[state].setChecked(True)
        self.osd(("Repeat: off", "Repeat: playlist", "Repeat: single file")[state])

    def cycle_loop(self):
        self.set_loop((self.loop_state + 1) % 3)

    def cycle_track(self, kind):
        try:
            self.mpv.command("cycle", kind)
        except Exception:
            return
        QTimer.singleShot(90, lambda: self._announce_track(kind))

    def _announce_track(self, kind):
        name = "Audio" if kind == "audio" else "Subtitles"
        try:
            tracks = [t for t in (self.mpv.track_list or []) if t.get("type") == kind]
        except Exception:
            return
        cur = next((t for t in tracks if t.get("selected")), None)
        if cur is None:
            self.osd(f"{name}: off")
        else:
            label = cur.get("title") or cur.get("lang") or f"#{cur.get('id')}"
            self.osd(f"{name}: {label}")

    def toggle_sub_visibility(self):
        try:
            self.mpv.command("cycle", "sub-visibility")
            self.osd("Subtitles: " + ("visible" if self.p("sub-visibility", True) else "hidden"))
        except Exception:
            pass

    def _toggle_remaining(self):
        self.show_remaining = not self.show_remaining

    def _on_scrub(self, frac):
        now = time.monotonic()
        if now - self._last_scrub < 0.05:      # don't flood mpv with seek commands while dragging
            return
        self._last_scrub = now
        duration = self.p("duration")
        if duration:
            try:
                self.mpv.command("seek", str(float(duration) * frac), "absolute+keyframes")
            except Exception:
                pass

    def _on_sought(self, frac):
        duration = self.p("duration")
        if duration:
            try:
                self.mpv.command("seek", str(float(duration) * frac), "absolute")
            except Exception:
                pass

    def _video_size(self):
        """Tries several properties for the video size (one of them works depending on the libmpv version)."""
        for wn, hn in (("video-params/w", "video-params/h"), ("dwidth", "dheight"),
                       ("width", "height")):
            w, h = self.p(wn), self.p(hn)
            if w and h:
                return w, h
        return None, None

    def _has_video(self):
        w, h = self._video_size()
        if w and h:
            return True
        vid = self.p("vid")
        if isinstance(vid, bool):
            vid = None
        if isinstance(vid, int):
            return vid > 0
        if isinstance(vid, str) and vid.isdigit():
            return int(vid) > 0
        if self.p("current-tracks/video/id"):
            return True
        try:
            return any(t.get("type") == "video" and t.get("selected")
                       for t in (self.mpv.track_list or []))
        except Exception:
            return False

    def _no_video(self):
        return (not self.p("path")) or (not self._has_video())

    # ------------------------------------------------------------------
    #  Extra features: bookmarks, night mode, sleep timer, hold for 2×, recent files
    # ------------------------------------------------------------------
    def _json_setting(self, key, default="[]"):
        """Reads a QSettings value as JSON (with INI storage a comma-separated value may come back as a list)."""
        value = self.settings.value(key, default)
        if isinstance(value, (list, tuple)):
            value = ",".join(str(x) for x in value)
        elif isinstance(value, (bytes, bytearray)):
            value = bytes(value).decode("utf-8", "ignore")
        return json.loads(value)

    def _bm_key(self):
        path = self.p("path")
        if not path:
            return None
        return "bookmarks/" + hashlib.sha1(str(path).encode("utf-8", "ignore")).hexdigest()[:20]

    def _load_bookmarks(self):
        key = self._bm_key()
        if not key:
            return []
        try:
            data = self._json_setting(key)
            return sorted(({"t": float(b["t"]), "note": str(b.get("note", ""))} for b in data),
                          key=lambda b: b["t"])
        except Exception:
            return []

    def _save_bookmarks(self, items):
        key = self._bm_key()
        if key:
            if items:
                self.settings.setValue(key, json.dumps(items, ensure_ascii=False))
            else:
                self.settings.remove(key)
        self._sync_bookmark_marks()

    def _sync_bookmark_marks(self):
        try:
            dur = float(self.p("duration") or 0.0)
        except (TypeError, ValueError):
            dur = 0.0
        if dur <= 0:
            self.seekbar.set_marks([])
            return
        self.seekbar.set_marks([(b["t"] / dur, b["note"]) for b in self._load_bookmarks()
                                if b["t"] <= dur])

    def add_bookmark(self, silent=False):
        pos = self.p("time-pos")
        if pos is None or not self.p("path"):
            if not silent:
                self.osd("Open a video first")
            return
        t = float(pos)
        items = self._load_bookmarks()
        if any(abs(b["t"] - t) < 1.0 for b in items):
            if not silent:
                self.osd("There is already a bookmark here")
            return
        items.append({"t": round(t, 2), "note": ""})
        items.sort(key=lambda b: b["t"])
        self._save_bookmarks(items)
        if not silent:
            self.osd(f"Bookmark added · {fmt_time(t)}")

    def goto_bookmark(self, direction):
        pos = float(self.p("time-pos", 0) or 0)
        items = self._load_bookmarks()
        if direction > 0:
            cand = [b for b in items if b["t"] > pos + 0.5]
            target = cand[0] if cand else None
        else:
            cand = [b for b in items if b["t"] < pos - 1.5]
            target = cand[-1] if cand else None
        if target is None:
            self.osd("No bookmark")
            return
        try:
            self.mpv.command("seek", str(target["t"]), "absolute")
        except Exception:
            return
        note = target["note"]
        self.osd(f"Bookmark · {fmt_time(target['t'])}" + (f"  {note}" if note else ""))

    def show_bookmarks(self):
        if not self.p("path"):
            self.osd("Open a video first")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Bookmarks")
        dlg.resize(460, 380)
        lay = QVBoxLayout(dlg)
        lst = QListWidget()
        lay.addWidget(lst, 1)
        row = QHBoxLayout()
        btn_add = QPushButton("Add current position")
        btn_go = QPushButton("Go to")
        btn_edit = QPushButton("Edit note")
        btn_del = QPushButton("Delete")
        btn_close = QPushButton("Close")
        for b in (btn_add, btn_go, btn_edit, btn_del):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(btn_close)
        lay.addLayout(row)
        items = []

        def refill():
            nonlocal items
            items = self._load_bookmarks()
            lst.clear()
            for b in items:
                lst.addItem(f"{fmt_time(b['t'])}   {b['note']}".rstrip())

        def sel():
            r = lst.currentRow()
            return r if 0 <= r < len(items) else None

        def go(*_):
            r = sel()
            if r is not None:
                try:
                    self.mpv.command("seek", str(items[r]["t"]), "absolute")
                except Exception:
                    pass

        def edit(*_):
            r = sel()
            if r is None:
                return
            note, ok = QInputDialog.getText(dlg, "Bookmark note", "Note:", text=items[r]["note"])
            if ok:
                items[r]["note"] = note.strip()
                self._save_bookmarks(items)
                refill()
                lst.setCurrentRow(r)

        def delete(*_):
            r = sel()
            if r is None:
                return
            del items[r]
            self._save_bookmarks(items)
            refill()

        def add(*_):
            self.add_bookmark(silent=True)
            refill()

        btn_add.clicked.connect(add)
        btn_go.clicked.connect(go)
        btn_edit.clicked.connect(edit)
        btn_del.clicked.connect(delete)
        btn_close.clicked.connect(dlg.close)
        lst.itemDoubleClicked.connect(go)
        refill()
        dlg.exec()

    # --- night mode (audio normalization) ---
    def set_night_mode(self, on):
        try:
            if on:
                self.mpv.command("af", "add", "@night:lavfi=[dynaudnorm=f=150:g=15]")
            else:
                self.mpv.command("af", "remove", "@night")
            self._night = bool(on)
            self.osd("Night mode: " + ("on" if on else "off"))
        except Exception:
            self.osd("Night mode is not available in this libmpv version")
            self.act_night.blockSignals(True)
            self.act_night.setChecked(self._night)
            self.act_night.blockSignals(False)

    # --- auto-pause on focus loss ---
    def set_auto_pause(self, on):
        self.auto_pause = bool(on)
        self._auto_paused = False
        self.settings.setValue("auto_pause", self.auto_pause)
        self.osd("Pause on focus loss: " + ("on" if on else "off"))

    def _on_app_state(self, state):
        if not self.auto_pause or self.mpv is None:
            return
        try:
            if state == Qt.ApplicationInactive:
                if self.p("path") and not self._no_video() and not self.mpv.pause:
                    self.mpv.pause = True
                    self._auto_paused = True
            elif state == Qt.ApplicationActive and self._auto_paused:
                self._auto_paused = False
                self.mpv.pause = False
        except Exception:
            pass

    # --- sleep timer (fades the volume down over the last 20 s) ---
    def set_sleep_timer(self, minutes=0, at_end=False):
        self._cancel_sleep()
        if minutes:
            self._sleep_deadline = time.monotonic() + minutes * 60
            self.osd(f"Sleep timer: {minutes} min")
        elif at_end:
            self._sleep_eof = True
            self._set_prop("keep-open", "always")     # don't advance the playlist, stop when the file ends
            self.osd("Will pause when this file ends")
        else:
            self.osd("Sleep timer off")

    def _cancel_sleep(self):
        self._sleep_deadline = None
        if self._sleep_eof:
            self._sleep_eof = False
            self._set_prop("keep-open", "yes")
        if self._sleep_fading:
            self._sleep_fading = False
            try:
                self.mpv.volume = self.volume.slider.value()
            except Exception:
                pass

    def _tick_sleep(self):
        if self.mpv is None:
            return
        if self._sleep_deadline is not None:
            left = self._sleep_deadline - time.monotonic()
            if left <= 0:
                try:
                    self.mpv.pause = True
                except Exception:
                    pass
                self._cancel_sleep()
                self._sleep_actions[0].setChecked(True)
                self.osd("Sleep timer: paused")
            elif left < 20.0:
                self._sleep_fading = True
                try:
                    self.mpv.volume = self.volume.slider.value() * left / 20.0
                except Exception:
                    pass
        elif self._sleep_eof and self.p("eof-reached", False):
            self._cancel_sleep()
            self._sleep_actions[0].setChecked(True)
            self.osd("Sleep timer: file ended, paused")

    # --- hold to play at 2× speed ---
    def _on_hold_start(self):
        if self._no_video() or self.p("pause", False):
            return
        try:
            self._hold_prev_speed = float(self.p("speed", 1.0))
            self.mpv.speed = 2.0
        except Exception:
            return
        self._hold_active = True
        self.pulse.trigger("play", self._stage_center(), "2×")

    def _on_hold_end(self):
        if not self._hold_active:
            return
        self._hold_active = False
        try:
            self.mpv.speed = self._hold_prev_speed
        except Exception:
            pass

    # --- recent files ---
    def _recent_list(self):
        try:
            return [x for x in self._json_setting("recent_json") if isinstance(x, str)]
        except Exception:
            return []

    def _remember_recent(self):
        path = self.p("path")
        if not path:
            return
        path = str(path)
        items = [x for x in self._recent_list() if x != path]
        items.insert(0, path)
        self.settings.setValue("recent_json", json.dumps(items[:12], ensure_ascii=False))

    def _fill_recent(self):
        menu = self.recent_menu
        menu.clear()
        items = [x for x in self._recent_list() if "://" in x or os.path.exists(x)]
        if not items:
            menu.addAction("(empty)").setEnabled(False)
            return
        for path in items:
            name = path if "://" in path else (os.path.basename(path) or path)
            if len(name) > 60:
                name = name[:57] + "…"
            a = QAction(name.replace("&", "&&"), menu)
            a.setToolTip(path)
            a.triggered.connect(lambda _c=False, pth=path: self.open_paths([pth]))
            menu.addAction(a)
        menu.addSeparator()
        clear = QAction("Clear list", menu)
        clear.triggered.connect(lambda _c=False: self.settings.remove("recent_json"))
        menu.addAction(clear)

    # ------------------------------------------------------------------
    #  Playlist
    # ------------------------------------------------------------------
    def set_playlist_visible(self, on):
        self.playlist_on = bool(on)
        self.drawer.set_open(self.playlist_on)
        self.btn_playlist.setActive(self.playlist_on)
        self.act_playlist.blockSignals(True)
        self.act_playlist.setChecked(self.playlist_on)
        self.act_playlist.blockSignals(False)
        self._pl_sig = None
        if self.playlist_on:
            self._refresh_playlist()

    def _refresh_playlist(self):
        try:
            items = self.mpv.playlist or []
        except Exception:
            return
        sig = tuple((e.get("filename"), bool(e.get("current") or e.get("playing")))
                    for e in items)
        if sig == self._pl_sig:
            return
        self._pl_sig = sig
        lst = self.drawer.list
        lst.clear()
        current_row = -1
        for i, e in enumerate(items, 1):
            name = e.get("title") or e.get("filename") or ""
            if "://" not in name:
                name = os.path.basename(name) or name
            current = bool(e.get("current") or e.get("playing"))
            item = QListWidgetItem(f"{'▶' if current else f'{i:02d}'}   {name}")
            if current:
                current_row = i - 1
                item.setForeground(QColor(ACCENT2))
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            lst.addItem(item)
        if current_row >= 0:
            lst.scrollToItem(lst.item(current_row), QListWidget.PositionAtCenter)
        self.drawer.count.setText(f"{len(items)} item{'s' if len(items) != 1 else ''}" if items else "")

    def _play_playlist_item(self, item):
        try:
            self.mpv.command("playlist-play-index", str(self.drawer.list.row(item)))
            self.mpv.pause = False
        except Exception:
            pass

    def _playlist_context(self, pos):
        menu = QMenu(self)
        item = self.drawer.list.itemAt(pos)
        if item is not None:
            idx = self.drawer.list.row(item)
            menu.addAction("Remove from list").triggered.connect(
                lambda: self.mpv.command("playlist-remove", str(idx)))
        menu.addAction("Clear list").triggered.connect(
            lambda: self.mpv.command("playlist-clear"))
        menu.exec(self.drawer.list.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    #  Media info (top line + details window)
    # ------------------------------------------------------------------
    def _build_info(self, final=False):
        """Returns (text, complete). Skips missing parts until the decoder is ready."""
        parts = []
        done = False
        w, h = self._video_size()
        has_v = self._has_video()
        acodec = self.p("audio-codec-name")

        def audio_part():
            if not acodec:
                return None
            text = str(acodec).upper()
            ch = self.p("audio-params/hr-channels")
            if ch:
                text += f" {ch}"
            return text

        if has_v:
            fmt = self.p("video-format") or str(self.p("video-codec", "")).split(" ")[0]
            if w and h:
                parts.append(f"{w}×{h}")
            if fmt:
                parts.append(str(fmt).upper())
            fps = fmt_fps(self.p("container-fps") or self.p("estimated-vf-fps"))
            if fps:
                parts.append(f"{fps} fps")
            if str(self.p("video-params/gamma", "")) in ("pq", "hlg"):
                parts.append("HDR")
            hw = self.p("hwdec-current")
            if hw is None:                      # decoder hasn't started yet
                if final:
                    parts.append("software")
            elif hw == "no":
                parts.append("software")
                done = True
            else:
                parts.append(f"GPU: {hw}")
                done = True
            a = audio_part()
            if a:
                parts.append(a)
        elif acodec:
            parts.append("Audio")
            parts.append(audio_part())
            rate = self.p("audio-params/samplerate")
            if rate:
                parts.append(f"{float(rate) / 1000:g} kHz")
            done = True
        return "  ·  ".join(x for x in parts if x), done

    def _tick_info(self):
        if self._info_done or self.mpv is None:
            return
        self._info_tries += 1
        final = self._info_tries >= 25          # give up after ~5 s
        title = self.p("media-title", "") or ""
        if title != self._media_title:
            self._media_title = title
            self.setWindowTitle(f"{title} - {APP_NAME}" if title else APP_NAME)
        text, done = self._build_info(final)
        self.scrim.set_texts(self._media_title, text)
        self._info_done = done or final

    def _info_text(self):
        if not self.p("path"):
            return "No media is open."
        L = []

        def sec(title):
            if L:
                L.append("")
            L.append(title)
            L.append("─" * len(title))

        def row(label, value):
            if value is None or value == "":
                return
            L.append(f"{label:<24}{value}")

        def num(name, fn):
            v = self.p(name)
            if v is None:
                return None
            try:
                return fn(float(v))
            except (TypeError, ValueError):
                return None

        sec("GENERAL")
        row("Title", self.p("media-title"))
        row("Location", self.p("path"))
        row("Format", self.p("file-format"))
        row("Size", fmt_size(self.p("file-size")))
        row("Duration", fmt_time(self.p("duration")) if self.p("duration") else None)
        chapters = self.p("chapter-list")
        row("Chapters", len(chapters) if chapters else None)

        vw, vh = self._video_size()
        if self._has_video():
            hw = self.p("hwdec-current")
            sec("VIDEO")
            row("Codec", self.p("video-codec"))
            row("Resolution", f"{vw} × {vh}" if vw and vh else None)
            dw, dh = self.p("video-params/dw"), self.p("video-params/dh")
            row("Display size", f"{dw} × {dh}" if dw and dh else None)
            row("Aspect ratio", num("video-params/aspect", lambda v: f"{v:.3f}"))
            row("Frame rate", fmt_fps(self.p("container-fps")))
            row("Pixel format", self.p("video-params/pixelformat"))
            row("Hardware pixel format", self.p("video-params/hw-pixelformat"))
            row("Color matrix", self.p("video-params/colormatrix"))
            row("Color primaries", self.p("video-params/primaries"))
            row("Gamma / transfer", self.p("video-params/gamma"))
            row("Color range", self.p("video-params/colorlevels"))
            row("Peak brightness", num("video-params/sig-peak", lambda v: f"{v:.2f}"))
            row("Rotation", self.p("video-params/rotate"))
            row("Bitrate", fmt_bitrate(self.p("video-bitrate")))
            row("Hardware decoding", "software" if hw == "no" else (hw or "starting…"))
            dropped = (self.p("frame-drop-count", 0) or 0) + (self.p("decoder-frame-drop-count", 0) or 0)
            row("Dropped frames", dropped)

        if self.p("audio-codec-name") or self.p("audio-codec"):
            sec("AUDIO")
            row("Codec", self.p("audio-codec"))
            row("Channels", self.p("audio-params/hr-channels"))
            rate = self.p("audio-params/samplerate")
            row("Sample rate", f"{rate} Hz" if rate else None)
            row("Format", self.p("audio-params/format"))
            row("Bitrate", fmt_bitrate(self.p("audio-bitrate")))

        try:
            tracks = self.mpv.track_list or []
        except Exception:
            tracks = []
        if tracks:
            sec("TRACKS")
            names = {"video": "Video", "audio": "Audio", "sub": "Subtitles"}
            for t in tracks:
                extra = " ".join(str(x) for x in (t.get("lang"), t.get("title"), t.get("codec")) if x)
                mark = "  ✓" if t.get("selected") else ""
                L.append(f"{names.get(t.get('type'), t.get('type')):<9}#{t.get('id')}  {extra}{mark}")
        return "\n".join(L)

    def toggle_media_info(self):
        if self._info_dlg is None:
            self._info_dlg = InfoDialog(self, self._info_text)
        if self._info_dlg.isVisible():
            self._info_dlg.close()
        else:
            self._info_dlg.show()
            self._info_dlg.raise_()
            self._info_dlg.activateWindow()

    def show_media_info(self):
        self.toggle_media_info()

    # ------------------------------------------------------------------
    #  State updates
    # ------------------------------------------------------------------
    def _poll_state(self):
        if self.mpv is None:
            return
        try:
            pos = self.mpv.time_pos
            dur = self.mpv.duration
            paused = bool(self.mpv.pause)
            muted = bool(self.mpv.mute)
        except Exception:
            return

        path = self.p("path")
        has_path = bool(path)
        no_video = (not has_path) or (not self._has_video())
        self.seekbar.set_duration(dur or 0)
        sig = (path, int(dur or 0))
        if sig != self._marks_sig:
            self._marks_sig = sig
            self._sync_bookmark_marks()
        if dur and pos is not None:
            self.seekbar.set_fraction(pos / dur)
        elif not dur:
            self.seekbar.set_fraction(0.0)

        if self.show_remaining and dur and pos is not None:
            right = "-" + fmt_time(max(dur - pos, 0))
        else:
            right = fmt_time(dur)
        self.time_label.setText(f"{fmt_time(pos)}  /  {right}")

        self.btn_play.setIconKind("play" if (paused or not has_path) else "pause")
        vol = self.volume.slider.value()
        self.volume.btn.setIconKind("mute" if (muted or vol == 0) else ("vol_low" if vol < 45 else "vol"))
        self.btn_full.setIconKind("unfull" if self.isFullScreen() else "full")

        try:
            label = f"{float(self.mpv.speed or 1.0):g}×"
        except Exception:
            label = self._speed_label
        if label != self._speed_label:
            self._speed_label = label
            self.btn_speed.setText(label)

        # While the file changes the video is briefly missing; wait a moment instead of showing the idle screen right away.
        self._no_video_polls = self._no_video_polls + 1 if no_video else 0
        show_idle = no_video and (self._no_video_polls >= 3 or not has_path)
        if show_idle != self.idle.isVisible():
            self.idle.setVisible(show_idle)
        if show_idle:
            title = self.p("media-title", "") if has_path else ""
            self.idle.heading = title or APP_NAME
            self.idle.hint = ("Playing — audio file" if has_path
                              else "Drag a file here or press Ctrl+O to open it")

        if (paused or no_video) and not self._ctrl_target:
            self.set_controls_visible(True)
        self._tick_info()
        self._tick_sleep()
        if self.drawer.is_open:
            self._refresh_playlist()

    def _on_file_loaded(self):
        self._media_title = ""
        self._info_done = False
        self._info_tries = 0
        self._tick_info()
        self._remember_recent()
        self.curtain_timer.stop()
        self.curtain.fade_out()
        self._activity()

    def _on_load_failed(self, message):
        self.curtain_timer.stop()
        self.curtain.fade_out()
        message = (message or "").strip()
        self.osd("Playback failed" + (f": {message[:70]}" if message else ""))

    # ------------------------------------------------------------------
    #  Track menus / info
    # ------------------------------------------------------------------
    def _fill_tracks(self, menu, kind, prop):
        menu.clear()
        old_group = getattr(menu, "_track_group", None)
        if old_group is not None:
            old_group.deleteLater()
        try:
            tracks = [t for t in (self.mpv.track_list or []) if t.get("type") == kind]
        except Exception:
            tracks = []
        group = QActionGroup(menu)
        menu._track_group = group
        group.setExclusive(True)

        def add(label, value, checked):
            a = QAction(label.replace("&", "&&"), menu)
            a.setCheckable(True)
            a.setChecked(checked)
            a.triggered.connect(lambda _c=False, v=value: self._set_prop(prop, str(v)))
            group.addAction(a)
            menu.addAction(a)

        add("Off", "no", not any(t.get("selected") for t in tracks))
        for t in tracks:
            label = f"{t['id']}: {t.get('title') or ''} [{t.get('lang') or '?'}] {t.get('codec') or ''}"
            add(label.strip(), t["id"], bool(t.get("selected")))
        if kind == "sub":
            menu.addSeparator()
            load = QAction("Load subtitle file…", menu)   # menu owns it: deleted by clear()
            load.triggered.connect(lambda _c=False: self._load_subtitle())
            menu.addAction(load)
        if not tracks and kind == "audio":
            menu.addAction("(no audio track)").setEnabled(False)

    def _load_subtitle(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select subtitle", "",
            "Subtitles (*.srt *.ass *.ssa *.vtt *.sub *.idx);;All (*)")
        if path:
            try:
                self.mpv.command("sub-add", path, "select")
            except Exception:
                pass

    def show_about(self):
        QMessageBox.about(
            self, APP_NAME,
            f"{APP_NAME} {APP_VERSION}\n\n" + "Cross-platform video player based on libmpv (FFmpeg).")

    # ------------------------------------------------------------------
    #  Fullscreen and mouse
    # ------------------------------------------------------------------
    def toggle_fullscreen(self):
        """Covers the flicker/black frame during window state changes with the curtain."""
        if self._fs_busy:
            return
        self._fs_busy = True
        self.curtain.fade_in(120)
        QTimer.singleShot(140, self._do_fullscreen_switch)

    def _do_fullscreen_switch(self):
        if self.isFullScreen():
            if self._was_maximized:
                self.showMaximized()
            else:
                self.showNormal()
        else:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
            QTimer.singleShot(0, self._cover_screen)   # yeni
        self._activity()
        # Remove the curtain once the window size and GL surface have settled.
        QTimer.singleShot(280, self._finish_fullscreen_switch)

    def _cover_screen(self):                            # yeni
        if self.isFullScreen():
            g = self.screen().geometry()
            self.setGeometry(g.x(), g.y(), g.width(), g.height() + 1)

    def _finish_fullscreen_switch(self):
        self.curtain.fade_out(280)
        self._fs_busy = False

    def _exit_fullscreen(self):
        if self.isFullScreen():
            self.toggle_fullscreen()

    def _on_escape(self):
        if self.isFullScreen():
            self._exit_fullscreen()
        elif self.playlist_on:
            self.set_playlist_visible(False)

    def _on_left_click(self):
        self.click_timer.start(QApplication.doubleClickInterval())

    def _on_idle_click(self):
        """Click toggles play/pause only while audio is playing (idle screen visible)."""
        if self.p("path"):
            self.toggle_pause()

    def _unmute(self):
        """Leave mute mode when the volume bar is touched."""
        if self.mpv is not None and self.p("mute", False):
            self.mpv.mute = False

    def _on_double_click(self, x):
        """Center: fullscreen · left/right edge: skip ±10 s."""
        self.click_timer.stop()
        w = max(1, self.video.width())
        if x < w * 0.3:
            self._skip_pulse(-10, 0.16)
        elif x > w * 0.7:
            self._skip_pulse(10, 0.84)
        else:
            self.toggle_fullscreen()

    def _skip_pulse(self, secs, fx):
        try:
            self.mpv.command("seek", str(secs), "relative")
        except Exception:
            return
        self.pulse.trigger("play", self._stage_center(fx),
                           f"{'+' if secs > 0 else '−'}{abs(secs)} s")

    def closeEvent(self, event):
        for t in (self.poll, self.hide_timer, self.click_timer, self.curtain_timer):
            t.stop()
        QApplication.instance().removeEventFilter(self)
        if self._info_dlg is not None:
            self._info_dlg.close()
        if not self.isFullScreen():
            self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("volume", self.volume.slider.value())
        self.settings.sync()
        self.video.shutdown()
        self.mpv = None
        super().closeEvent(event)


def _modern_palette():
    pal = QPalette()
    window, base, text = QColor(18, 19, 25), QColor(14, 15, 20), QColor(232, 232, 238)
    button, disabled = QColor(34, 35, 44), QColor(110, 112, 124)
    pal.setColor(QPalette.Window, window)
    pal.setColor(QPalette.WindowText, text)
    pal.setColor(QPalette.Base, base)
    pal.setColor(QPalette.AlternateBase, window)
    pal.setColor(QPalette.Text, text)
    pal.setColor(QPalette.Button, button)
    pal.setColor(QPalette.ButtonText, text)
    pal.setColor(QPalette.ToolTipBase, QColor(22, 23, 29))
    pal.setColor(QPalette.ToolTipText, text)
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("white"))
    pal.setColor(QPalette.Link, QColor(ACCENT2))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, disabled)
    return pal


def apply_modern_theme(app):
    app.setStyle("Fusion")
    app.setPalette(_modern_palette())
    font = app.font()
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "SF Pro Text", "Inter",
                      "Helvetica Neue", "Noto Sans", "Sans Serif"])
    font.setPointSize(10)
    app.setFont(font)
    app.setStyleSheet(APP_QSS)


def main():
    if sys.platform == "darwin":
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 2)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        QSurfaceFormat.setDefaultFormat(fmt)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    apply_modern_theme(app)
    app.setWindowIcon(make_app_icon(128))
    # libmpv wants the "C" locale for LC_NUMERIC (Qt may have changed it).
    locale.setlocale(locale.LC_NUMERIC, "C")

    window = MainWindow()
    window.show()
    if len(sys.argv) > 1:
        args = sys.argv[1:]
        # Open after the GL context exists: libmpv doesn't have to wait for the render context.
        QTimer.singleShot(250, lambda: window.open_paths(args))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
