"""
The Shot Context viewer: previous / current / next shot played as one cut.

Every slot is a list of JPG frames (the fresh playblast, a decoded movie from
the frame cache, a plate sequence) or a slate. Playback runs on its own clock
at the scene frame rate, so cuts land on exact frames.
"""

import collections
import os
import time
import webbrowser

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    try:
        from PySide6 import QtMultimedia
    except ImportError:
        QtMultimedia = None
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets
    QtMultimedia = None

from . import media

__all__ = ["Slot", "ShotContextWindow"]

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = "1.1.0"
AUTHOR = "Luuk Kamphuis"

# --- Theme: same black look as the Review tool's playblast preview
BG = "#1b1c1f"
CARD = "#25272b"          # surface
PANEL = "#2f3237"         # surface hover
BORDER = "#34373d"
TEXT = "#e6e7ea"
DIM = "#8b8f98"
ACCENT = "#3b82f6"
ACCENT_HOVER = "#5a97f8"
ACCENT_PRESSED = "#2f6ad0"
GREEN, TEAL, AMBER, RED = "#4ade80", "#2dd4bf", "#fbbf24", "#f87171"

KIND_COLOURS = {"playblast": GREEN, "footage": TEAL, "slate": AMBER, "error": RED}
KIND_LABELS = {"playblast": "PLAYBLAST", "footage": "FOOTAGE", "slate": "NO FOOTAGE", "error": "ERROR"}

STYLE = """
QDialog#ShotContext {{
    background-color: {bg};
}}
QDialog#ShotContext QLabel {{
    color: {text};
    background: transparent;
}}
QDialog#ShotContext QLabel#Title {{
    font-size: 15px;
    font-weight: 600;
}}
QDialog#ShotContext QLabel#Muted, QDialog#ShotContext QLabel#FrameTotal, QDialog#ShotContext QLabel#Foot {{
    color: {muted};
}}
QDialog#ShotContext QLabel#Foot {{
    font-size: 11px;
}}
QDialog#ShotContext QLabel#FrameNumber {{
    font-size: 15px;
    font-weight: 600;
}}
QDialog#ShotContext QLabel#Chip {{
    color: {muted};
    background-color: {surface};
    border: 1px solid {border};
    border-radius: 10px;
    padding: 2px 9px;
    font-size: 11px;
}}
QFrame#Transport, QFrame#Footer {{
    background-color: {surface};
    border: 1px solid {border};
    border-radius: 10px;
}}
QToolButton#IconButton {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 4px;
}}
QToolButton#IconButton:hover {{
    background-color: {surface_hover};
}}
QToolButton#IconButton:checked {{
    background-color: rgba(59, 130, 246, 0.22);
}}
QToolButton#PlayButton {{
    background-color: {text};
    border: none;
    border-radius: 18px;
}}
QToolButton#PlayButton:hover {{
    background-color: #ffffff;
}}
QPushButton#SecondaryButton, QPushButton#GhostButton {{
    color: {text};
    background-color: transparent;
    border: 1px solid {border};
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 500;
}}
QPushButton#SecondaryButton:hover, QPushButton#GhostButton:hover {{
    background-color: {surface_hover};
}}
QPushButton#GhostButton {{
    padding: 4px 10px;
}}
QPushButton#PrimaryButton {{
    color: #ffffff;
    background-color: {accent};
    border: none;
    border-radius: 8px;
    padding: 9px 18px;
    font-weight: 600;
}}
QPushButton#PrimaryButton:hover {{
    background-color: {accent_hover};
}}
QPushButton#PrimaryButton:pressed {{
    background-color: {accent_pressed};
}}
QPushButton#PrimaryButton:disabled {{
    color: {muted};
    background-color: {border};
}}
QToolTip {{
    color: {text};
    background-color: {surface};
    border: 1px solid {border};
    padding: 4px;
}}
""".format(bg=BG, surface=CARD, surface_hover=PANEL, border=BORDER, text=TEXT, muted=DIM,
           accent=ACCENT, accent_hover=ACCENT_HOVER, accent_pressed=ACCENT_PRESSED)


def _age(ts):
    if not ts:
        return ""
    s = max(0, time.time() - ts)
    if s < 90:
        return "just now"
    if s < 3600:
        return "%d min ago" % (s // 60)
    if s < 86400 * 2:
        return "%d h ago" % (s // 3600)
    return "%d days ago" % (s // 86400)


# --------------------------------------------------------------------------- icons
def _icon(kind, color=TEXT, size=20):
    """Vector icons drawn like the Review tool's preview (24x24 design grid)."""
    scale = 3
    pm = QtGui.QPixmap(size * scale, size * scale)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.scale(scale * size / 24.0, scale * size / 24.0)
    c = QtGui.QColor(color)
    pen = QtGui.QPen(c, 2.0)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)

    def tri(x0, x1, top, bottom):
        path = QtGui.QPainterPath()
        path.moveTo(x0, top)
        path.lineTo(x1, (top + bottom) / 2.0)
        path.lineTo(x0, bottom)
        path.closeSubpath()
        return path

    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(c)
    if kind == "play":
        p.drawPath(tri(8.0, 19.0, 5.0, 19.0))
    elif kind == "pause":
        p.drawRoundedRect(QtCore.QRectF(6.5, 5.5, 4, 13), 1.2, 1.2)
        p.drawRoundedRect(QtCore.QRectF(13.5, 5.5, 4, 13), 1.2, 1.2)
    elif kind == "step_fwd":
        p.drawPath(tri(6.0, 15.0, 6.0, 18.0))
        p.drawRoundedRect(QtCore.QRectF(15.5, 6, 2.5, 12), 1, 1)
    elif kind == "step_back":
        p.drawPath(tri(18.0, 9.0, 6.0, 18.0))
        p.drawRoundedRect(QtCore.QRectF(6.0, 6, 2.5, 12), 1, 1)
    elif kind == "next_shot":
        p.drawPath(tri(3.5, 11.5, 6.5, 17.5))
        p.drawPath(tri(11.5, 19.5, 6.5, 17.5))
        p.drawRoundedRect(QtCore.QRectF(19.5, 6.5, 2.2, 11), 1, 1)
    elif kind == "prev_shot":
        p.drawPath(tri(20.5, 12.5, 6.5, 17.5))
        p.drawPath(tri(12.5, 4.5, 6.5, 17.5))
        p.drawRoundedRect(QtCore.QRectF(2.3, 6.5, 2.2, 11), 1, 1)
    elif kind == "loop":
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        path = QtGui.QPainterPath()
        path.moveTo(17, 5)
        path.lineTo(20, 8)
        path.lineTo(17, 11)
        path.moveTo(4, 12)
        path.lineTo(4, 11)
        path.quadTo(4, 8, 7, 8)
        path.lineTo(20, 8)
        path.moveTo(7, 19)
        path.lineTo(4, 16)
        path.lineTo(7, 13)
        path.moveTo(20, 12)
        path.lineTo(20, 13)
        path.quadTo(20, 16, 17, 16)
        path.lineTo(4, 16)
        p.drawPath(path)
    elif kind in ("sound", "mute"):
        speaker = QtGui.QPainterPath()
        speaker.moveTo(3, 9.5)
        speaker.lineTo(7, 9.5)
        speaker.lineTo(12, 5)
        speaker.lineTo(12, 19)
        speaker.lineTo(7, 14.5)
        speaker.lineTo(3, 14.5)
        speaker.closeSubpath()
        p.drawPath(speaker)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        if kind == "sound":
            p.drawArc(QtCore.QRectF(10, 8, 8, 8), -60 * 16, 120 * 16)
            p.drawArc(QtCore.QRectF(9, 4.5, 13, 15), -60 * 16, 120 * 16)
        else:
            p.drawLine(QtCore.QPointF(15.5, 9), QtCore.QPointF(21, 15))
            p.drawLine(QtCore.QPointF(21, 9), QtCore.QPointF(15.5, 15))
    p.end()
    pm.setDevicePixelRatio(scale)
    return QtGui.QIcon(pm)


# --------------------------------------------------------------------------- data
class Slot(object):
    """One shot in the viewer."""

    def __init__(self, info, fps):
        self.info = info
        self.code = info.code
        self.role = info.role
        self.kind = info.kind
        self.frames = []
        self.first_frame = 1          # number shown for frame 0
        self.loading = False
        self.progress = 0.0
        self.error = ""
        self.audio_path = None
        self.audio_start_frame = 0.0  # local frame where the sound starts
        self.audio_fps = fps
        self.description = ""
        self.placeholder = info.duration or int(round(2 * fps))

    @property
    def length(self):
        if self.frames:
            return len(self.frames)
        return max(1, int(self.placeholder))


class _Clip(object):
    """A sound that plays along with one slot."""

    def __init__(self, path, parent):
        self.path = path
        self.ok = (QtMultimedia is not None and bool(path) and os.path.isfile(path)
                   and os.environ.get("NFA_SHOT_CONTEXT_NO_AUDIO", "") not in ("1", "true", "yes"))
        self.player = None
        if self.ok:
            try:
                # No sound device (e.g. remote session): play silently
                self.ok = bool(QtMultimedia.QMediaDevices.audioOutputs())
            except Exception:
                self.ok = False
        if not self.ok:
            return
        try:
            self.player = QtMultimedia.QMediaPlayer(parent)
            self.output = QtMultimedia.QAudioOutput(parent)
            self.player.setAudioOutput(self.output)
            self.player.setSource(QtCore.QUrl.fromLocalFile(path))
        except Exception:
            self.ok = False
            self.player = None

    def set_muted(self, muted):
        if self.player is not None:
            self.output.setMuted(muted)

    def sync(self, ms, playing):
        if self.player is None:
            return
        P = QtMultimedia.QMediaPlayer.PlaybackState
        dur = self.player.duration()
        if not playing or ms < 0 or (dur > 0 and ms >= dur):
            if self.player.playbackState() == P.PlayingState:
                self.player.pause()
            return
        if self.player.playbackState() != P.PlayingState:
            self.player.setPosition(int(ms))
            self.player.play()
        elif abs(self.player.position() - ms) > 120:
            self.player.setPosition(int(ms))

    def stop(self):
        if self.player is not None:
            self.player.stop()


# --------------------------------------------------------------------------- widgets
class FrameView(QtWidgets.QWidget):
    clicked = QtCore.Signal()

    def __init__(self, parent=None):
        super(FrameView, self).__init__(parent)
        self.setMinimumSize(480, 270)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.image = None
        self.slot = None
        self.local = 0
        self.message = ""
        self.paused = False
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._play_icon = _icon("play", "#ffffff", 32)

    def set_paused(self, paused):
        self.paused = paused
        self.update()

    def show_frame(self, slot, local, image):
        self.slot, self.local, self.image = slot, local, image
        self.update()

    def _fit(self, w, h):
        if w <= 0 or h <= 0:
            w, h = 16, 9
        r = self.rect()
        scale = min(r.width() / float(w), r.height() / float(h))
        sw, sh = w * scale, h * scale
        return QtCore.QRectF(r.x() + (r.width() - sw) / 2, r.y() + (r.height() - sh) / 2, sw, sh)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        clip = QtGui.QPainterPath()
        clip.addRoundedRect(QtCore.QRectF(self.rect()), 10, 10)
        p.setClipPath(clip)
        p.fillRect(self.rect(), QtGui.QColor("#000000"))
        slot = self.slot
        if slot is None:
            return
        if self.image is not None and not self.image.isNull():
            target = self._fit(self.image.width(), self.image.height())
            p.drawImage(target, self.image)
        else:
            ref = getattr(self.parent(), "aspect", (16, 9))
            target = self._fit(*ref)
            p.fillRect(target, QtGui.QColor("#111214"))
            self._paint_card(p, target, slot)

        # Overlay: shot + what it is
        colour = QtGui.QColor(KIND_COLOURS.get(slot.kind, DIM))
        font = QtGui.QFont("Segoe UI", 10, QtGui.QFont.DemiBold)
        p.setFont(font)
        tag = "%s  ·  %s" % (slot.code, KIND_LABELS.get(slot.kind, ""))
        if slot.role == "current":
            tag += "  ·  YOUR SHOT"
        fm = QtGui.QFontMetrics(font)
        box = QtCore.QRectF(target.x() + 10, target.y() + 10, fm.horizontalAdvance(tag) + 22, fm.height() + 10)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(0, 0, 0, 170))
        p.drawRoundedRect(box, 5, 5)
        p.setBrush(colour)
        p.drawEllipse(QtCore.QPointF(box.x() + 9, box.center().y()), 3.5, 3.5)
        p.setPen(QtGui.QColor(TEXT))
        p.drawText(box.adjusted(16, 0, 0, 0), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, tag)
        if slot.description:
            small = QtGui.QFont("Segoe UI", 8)
            p.setFont(small)
            sfm = QtGui.QFontMetrics(small)
            text = sfm.elidedText(slot.description, QtCore.Qt.ElideMiddle, int(max(80, target.width() - 40)))
            sub = QtCore.QRectF(box.x(), box.bottom() + 4, sfm.horizontalAdvance(text) + 14, sfm.height() + 6)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(0, 0, 0, 140))
            p.drawRoundedRect(sub, 4, 4)
            p.setPen(QtGui.QColor(DIM))
            p.drawText(sub.adjusted(7, 0, 0, 0), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, text)

        if slot.role == "current":
            pen = QtGui.QPen(QtGui.QColor(ACCENT), 2)
            p.setPen(pen)
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawRect(target.adjusted(1, 1, -1, -1))

        if self.paused and self.image is not None and not self.image.isNull():
            # subtle play badge in the middle while paused (as in the Review preview)
            badge = QtCore.QRectF(0, 0, 76, 76)
            badge.moveCenter(QtCore.QRectF(self.rect()).center())
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(0, 0, 0, 140))
            p.drawEllipse(badge)
            icon_rect = QtCore.QRect(0, 0, 32, 32)
            icon_rect.moveCenter(badge.center().toPoint() + QtCore.QPoint(2, 0))
            self._play_icon.paint(p, icon_rect)

    def _paint_card(self, p, rect, slot):
        if slot.loading:
            title = "LOADING"
            sub = "Reading %s…  %d%%" % (slot.code, int(slot.progress * 100))
            colour = QtGui.QColor(DIM)
        elif slot.error:
            title = "CAN'T PLAY THIS SHOT"
            sub = slot.error
            colour = QtGui.QColor(RED)
        else:
            title = slot.info.slate_text or "NO FOOTAGE"
            sub = "  ·  ".join(slot.info.notes)
            colour = QtGui.QColor(AMBER)
        p.setPen(QtGui.QColor(TEXT))
        big = QtGui.QFont("Segoe UI", max(14, int(rect.height() / 9)), QtGui.QFont.Bold)
        p.setFont(big)
        p.drawText(rect.adjusted(0, -rect.height() * 0.08, 0, 0), QtCore.Qt.AlignCenter, slot.code)
        p.setPen(colour)
        mid = QtGui.QFont("Segoe UI", max(9, int(rect.height() / 20)), QtGui.QFont.DemiBold)
        p.setFont(mid)
        p.drawText(rect.adjusted(0, rect.height() * 0.16, 0, 0), QtCore.Qt.AlignCenter, title)
        p.setPen(QtGui.QColor(DIM))
        p.setFont(QtGui.QFont("Segoe UI", max(8, int(rect.height() / 34))))
        p.drawText(rect.adjusted(20, rect.height() * 0.32, -20, 0), QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap, sub)
        if slot.loading:
            bar = QtCore.QRectF(rect.center().x() - 80, rect.bottom() - rect.height() * 0.18, 160, 4)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(BORDER))
            p.drawRoundedRect(bar, 2, 2)
            p.setBrush(QtGui.QColor(ACCENT))
            p.drawRoundedRect(QtCore.QRectF(bar.x(), bar.y(), bar.width() * slot.progress, 4), 2, 2)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()


class TimelineBar(QtWidgets.QWidget):
    """Three blocks, one per shot, sized by length; click or drag to scrub."""

    scrubbed = QtCore.Signal(int)
    scrub_state = QtCore.Signal(bool)

    def __init__(self, parent=None):
        super(TimelineBar, self).__init__(parent)
        self.setMinimumHeight(58)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.slots = []
        self.position = 0
        self.total = 1

    def set_slots(self, slots):
        self.slots = slots
        self.total = max(1, sum(s.length for s in slots))
        self.update()

    def set_position(self, frame):
        self.position = frame
        self.update()

    def _x(self, frame):
        r = self.rect().adjusted(2, 0, -2, 0)
        return r.x() + r.width() * frame / float(self.total)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        top, h = 4, self.height() - 8
        start = 0
        for slot in self.slots:
            x0, x1 = self._x(start), self._x(start + slot.length)
            rect = QtCore.QRectF(x0 + 1.5, top, x1 - x0 - 3, h)
            current = slot.role == "current"
            p.setPen(QtGui.QPen(QtGui.QColor(ACCENT if current else BORDER), 1.5 if current else 1))
            p.setBrush(QtGui.QColor("#1f2b40" if current else CARD))
            p.drawRoundedRect(rect, 8, 8)
            if slot.loading:
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(QtGui.QColor(59, 130, 246, 50))
                p.drawRoundedRect(QtCore.QRectF(rect.x(), rect.y(), rect.width() * slot.progress, rect.height()), 8, 8)
            kind = "error" if slot.error else slot.kind
            colour = QtGui.QColor(KIND_COLOURS.get(kind, DIM))
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(colour)
            p.drawRoundedRect(QtCore.QRectF(rect.x() + 8, rect.bottom() - 7, max(0.0, rect.width() - 16), 3), 1.5, 1.5)
            text_rect = rect.adjusted(10, 5, -10, -10)
            p.setPen(QtGui.QColor(TEXT))
            p.setFont(QtGui.QFont("Segoe UI", 10, QtGui.QFont.DemiBold))
            fm = p.fontMetrics()
            p.drawText(text_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop,
                       fm.elidedText(slot.code, QtCore.Qt.ElideRight, int(text_rect.width())))
            p.setPen(QtGui.QColor(DIM))
            p.setFont(QtGui.QFont("Segoe UI", 8))
            fm = p.fontMetrics()
            sub = "%s · %d f" % ("LOADING" if slot.loading else KIND_LABELS.get(kind, ""), slot.length)
            p.drawText(text_rect.adjusted(0, 18, 0, 0), QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop,
                       fm.elidedText(sub, QtCore.Qt.ElideRight, int(text_rect.width())))
            start += slot.length
        # Playhead
        x = self._x(self.position + 0.5)
        p.setPen(QtGui.QPen(QtGui.QColor(TEXT), 2))
        p.drawLine(QtCore.QPointF(x, 0), QtCore.QPointF(x, self.height()))

    def _frame_at(self, x):
        r = self.rect().adjusted(2, 0, -2, 0)
        f = int((x - r.x()) / float(max(1, r.width())) * self.total)
        return max(0, min(self.total - 1, f))

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.scrub_state.emit(True)
            self.scrubbed.emit(self._frame_at(event.position().x() if hasattr(event, "position") else event.x()))

    def mouseMoveEvent(self, event):
        if event.buttons() & QtCore.Qt.LeftButton:
            self.scrubbed.emit(self._frame_at(event.position().x() if hasattr(event, "position") else event.x()))

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.scrub_state.emit(False)


# --------------------------------------------------------------------------- window
class ShotContextWindow(QtWidgets.QDialog):
    """
    slots:        [Slot] in cut order
    replayblast:  callable() -> (frames, first_frame, audio_path, audio_start_frame, status)
                  or None; used by the Re-playblast button
    """

    def __init__(self, slots, fps, context_text="", status="", replayblast=None, parent=None):
        super(ShotContextWindow, self).__init__(parent)
        self.setObjectName("ShotContext")
        self.setWindowTitle("NFA Shot Context")
        self.setStyleSheet(STYLE)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowMaximizeButtonHint)
        self.resize(1180, 820)
        self.fps = float(fps or 25.0)
        self.slots = slots
        self.replayblast = replayblast
        self.aspect = (16, 9)
        self._decoders = []
        self._cache = collections.OrderedDict()
        self._frame = 0
        self._playing = False
        self._scrubbing = False
        self._resume_after_scrub = False
        self._clock = QtCore.QElapsedTimer()
        self._clock_base = 0
        self._clips = {}
        self._muted = False
        self._autoplay = True

        self._build(context_text, status)
        self._timer = QtCore.QTimer(self)
        self._timer.setTimerType(QtCore.Qt.PreciseTimer)
        self._timer.setInterval(max(4, int(1000.0 / self.fps / 3)))
        self._timer.timeout.connect(self._tick)

        self._relayout()
        for slot in self.slots:
            self._setup_audio(slot)
        self._start_decoders()
        self._seek(0)
        self._maybe_autoplay()

    # ---- layout
    def _build(self, context_text, status):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 14)
        root.setSpacing(14)

        # --- header ---
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(8)
        titles = QtWidgets.QVBoxLayout()
        titles.setSpacing(2)
        t = QtWidgets.QLabel("Shot Context")
        t.setObjectName("Title")
        s = QtWidgets.QLabel("Your shot in the cut, between the previous and the next shot")
        s.setObjectName("Muted")
        titles.addWidget(t)
        titles.addWidget(s)
        head.addLayout(titles, 1)
        for part in [x for x in context_text.split("  ·  ") if x]:
            head.addWidget(self._chip(part))
        head.addWidget(self._chip(("%.3f" % self.fps).rstrip("0").rstrip(".") + " fps"))
        help_btn = QtWidgets.QPushButton("Help")
        help_btn.setObjectName("GhostButton")
        help_btn.setCursor(QtCore.Qt.PointingHandCursor)
        help_btn.setToolTip("Open the help page")
        help_btn.clicked.connect(self._open_help)
        head.addSpacing(6)
        head.addWidget(help_btn)
        root.addLayout(head)

        # --- viewport + shot bar ---
        self.view = FrameView(self)
        self.view.clicked.connect(self.toggle_play)
        root.addWidget(self.view, 1)

        self.timeline = TimelineBar(self)
        self.timeline.scrubbed.connect(self._on_scrub)
        self.timeline.scrub_state.connect(self._on_scrub_state)
        root.addWidget(self.timeline)

        # --- transport ---
        transport = QtWidgets.QFrame()
        transport.setObjectName("Transport")
        row = QtWidgets.QHBoxLayout(transport)
        row.setContentsMargins(12, 8, 16, 8)
        row.setSpacing(6)

        def icon_button(kind, tip, slot_fn, checkable=False):
            b = QtWidgets.QToolButton()
            b.setObjectName("IconButton")
            b.setIcon(_icon(kind))
            b.setIconSize(QtCore.QSize(20, 20))
            b.setFixedSize(32, 32)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setFocusPolicy(QtCore.Qt.NoFocus)
            b.clicked.connect(slot_fn)
            row.addWidget(b)
            return b

        icon_button("prev_shot", "Previous shot  (PgUp)", lambda: self.jump_shot(-1))
        icon_button("step_back", "Previous frame  (Left)", lambda: self.step(-1))
        self.play_btn = QtWidgets.QToolButton()
        self.play_btn.setObjectName("PlayButton")
        self.play_btn.setFixedSize(36, 36)
        self.play_btn.setIconSize(QtCore.QSize(18, 18))
        self.play_btn.setToolTip("Play / pause  (Space)")
        self.play_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.play_btn.setFocusPolicy(QtCore.Qt.NoFocus)
        self.play_btn.clicked.connect(self.toggle_play)
        row.addWidget(self.play_btn)
        icon_button("step_fwd", "Next frame  (Right)", lambda: self.step(1))
        icon_button("next_shot", "Next shot  (PgDn)", lambda: self.jump_shot(1))
        row.addSpacing(10)
        self.frame_label = QtWidgets.QLabel("")
        self.frame_label.setObjectName("FrameNumber")
        row.addWidget(self.frame_label)
        self.frame_total = QtWidgets.QLabel("")
        self.frame_total.setObjectName("FrameTotal")
        self.frame_total.setMinimumWidth(70)
        row.addWidget(self.frame_total)
        row.addStretch(1)
        self.mute_btn = icon_button("sound", "Mute / unmute sound  (M)", self._on_mute, checkable=True)
        self.loop_btn = icon_button("loop", "Loop the three shots  (L)", lambda: None, checkable=True)
        self.loop_btn.setChecked(True)
        root.addWidget(transport)

        # --- footer ---
        footer = QtWidgets.QFrame()
        footer.setObjectName("Footer")
        foot = QtWidgets.QHBoxLayout(footer)
        foot.setContentsMargins(18, 12, 12, 12)
        foot.setSpacing(10)
        info = QtWidgets.QVBoxLayout()
        info.setSpacing(2)
        self.status = QtWidgets.QLabel(status)
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        credit = QtWidgets.QLabel("v%s · %s" % (VERSION, AUTHOR))
        credit.setObjectName("Foot")
        info.addWidget(self.status)
        info.addWidget(credit)
        foot.addLayout(info, 1)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.setObjectName("SecondaryButton")
        close_btn.setAutoDefault(False)
        close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        foot.addWidget(close_btn)
        self.replay_btn = QtWidgets.QPushButton("Re-playblast")
        self.replay_btn.setObjectName("PrimaryButton")
        self.replay_btn.setAutoDefault(False)
        self.replay_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.replay_btn.setToolTip("Playblast your shot again and keep the neighbours")
        self.replay_btn.clicked.connect(self._on_replayblast)
        self.replay_btn.setVisible(self.replayblast is not None)
        foot.addWidget(self.replay_btn)
        root.addWidget(footer)

        # Keyboard goes to the player; Enter never triggers a button
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        for w in (help_btn, close_btn, self.replay_btn):
            w.setFocusPolicy(QtCore.Qt.NoFocus)
        self._update_play_icon()

    def _chip(self, text):
        chip = QtWidgets.QLabel(text)
        chip.setObjectName("Chip")
        return chip

    # ---- slots / frames
    def _current_index(self):
        for i, s in enumerate(self.slots):
            if s.role == "current":
                return i
        return 0

    def _relayout(self):
        self.total = max(1, sum(s.length for s in self.slots))
        self.timeline.set_slots(self.slots)
        for s in self.slots:
            if s.frames:
                img = QtGui.QImageReader(s.frames[0]).size()
                if img.isValid() and s.role == "current":
                    self.aspect = (img.width(), img.height())

    def _slot_start(self, index):
        return sum(s.length for s in self.slots[:index])

    def _locate(self, frame):
        start = 0
        for i, s in enumerate(self.slots):
            if frame < start + s.length:
                return i, frame - start
            start += s.length
        last = len(self.slots) - 1
        return last, self.slots[last].length - 1

    def _image(self, path):
        img = self._cache.get(path)
        if img is not None:
            self._cache.move_to_end(path)
            return img
        img = QtGui.QImage(path)
        self._cache[path] = img
        while len(self._cache) > 240:
            self._cache.popitem(last=False)
        return img

    def _any_loading(self):
        return any(s.loading for s in self.slots)

    def _start_decoders(self):
        for slot in self.slots:
            m = slot.info.media
            if slot.frames or m is None or m.kind != "movie":
                continue
            slot.loading = True
            dec = media.MovieDecoder(m.path, self)
            dec.progress.connect(lambda v, s=slot: self._on_decode_progress(s, v))
            dec.finished.connect(lambda frames, fps, err, s=slot: self._on_decoded(s, frames, fps, err))
            self._decoders.append(dec)
            dec.start()
        self.timeline.update()

    def _on_decode_progress(self, slot, value):
        slot.progress = value
        self.timeline.update()
        if self._locate(self._frame)[0] == self.slots.index(slot):
            self.view.update()

    def _on_decoded(self, slot, frames, fps, error):
        index, local = self._locate(self._frame)
        slot.loading = False
        if frames:
            slot.frames = frames
            if fps:
                slot.audio_fps = fps
        else:
            slot.error = error or "Unknown error"
        self._relayout()
        # Keep the playhead on the same shot/frame now that lengths changed
        self._frame = min(self._slot_start(index) + local, self.total - 1)
        if self._playing:
            self._rebase()
        if not self._any_loading() and not self._playing and self._autoplay:
            self._seek(0)
        self._show()
        self._maybe_autoplay()

    def _setup_audio(self, slot):
        path = slot.audio_path
        if path is None and slot.info.media is not None and slot.info.media.kind == "movie":
            path = slot.info.media.path  # the movie's own sound, if it has any
        if path and path not in self._clips:
            self._clips[path] = _Clip(path, self)
        slot._clip_path = path

    # ---- transport
    def _maybe_autoplay(self):
        if self._autoplay and not self._any_loading() and not self._playing:
            self._autoplay = False
            self.play()

    def _rebase(self):
        self._clock_base = self._frame
        self._clock.restart()

    def play(self):
        self._autoplay = False
        if self._frame >= self.total - 1:
            self._frame = 0
        self._playing = True
        self._rebase()
        self._timer.start()
        self._update_play_icon()

    def pause(self):
        self._playing = False
        self._timer.stop()
        self._sync_audio()
        self._update_play_icon()

    def toggle_play(self):
        self.pause() if self._playing else self.play()

    def step(self, delta):
        self.pause()
        self._seek(max(0, min(self.total - 1, self._frame + delta)))

    def jump_shot(self, delta):
        index, local = self._locate(self._frame)
        if delta < 0 and local > 2:
            target = index           # back to the start of this shot first
        else:
            target = max(0, min(len(self.slots) - 1, index + delta))
        self._seek(self._slot_start(target))
        if self._playing:
            self._rebase()

    def _seek(self, frame):
        self._frame = int(frame)
        self._show()

    def _tick(self):
        frame = self._clock_base + int(self._clock.elapsed() * self.fps / 1000.0)
        if frame >= self.total:
            if self.loop_btn.isChecked():
                self._frame = 0
                self._rebase()
                frame = 0
            else:
                self._frame = self.total - 1
                self.pause()
                self._show()
                return
        if frame != self._frame:
            self._frame = frame
            self._show()
        self._sync_audio()

    def _on_scrub_state(self, down):
        self._scrubbing = down
        if down:
            self._resume_after_scrub = self._playing
            if self._playing:
                self.pause()
        elif self._resume_after_scrub:
            self.play()

    def _on_scrub(self, frame):
        self._seek(frame)

    def _on_mute(self):
        self._muted = self.mute_btn.isChecked()
        self.mute_btn.setIcon(_icon("mute" if self._muted else "sound"))
        for clip in self._clips.values():
            clip.set_muted(self._muted)

    def _sync_audio(self):
        index, local = self._locate(self._frame)
        slot = self.slots[index]
        active = getattr(slot, "_clip_path", None) if not slot.loading else None
        for path, clip in self._clips.items():
            if path == active and self._playing:
                if slot.audio_path:  # scene sound of the current shot
                    ms = (local - slot.audio_start_frame) * 1000.0 / self.fps
                else:                # the movie's own sound
                    ms = local * 1000.0 / (slot.audio_fps or self.fps)
                clip.sync(ms, True)
            else:
                clip.sync(0, False)

    # ---- drawing
    def _show(self):
        index, local = self._locate(self._frame)
        slot = self.slots[index]
        image = self._image(slot.frames[local]) if slot.frames else None
        self.view.show_frame(slot, local, image)
        self.timeline.set_position(self._frame)
        if slot.role == "current":
            self.frame_label.setText("%s  %d" % (slot.code, slot.first_frame + local))
        else:
            self.frame_label.setText(slot.code)
        self.frame_total.setText("%d / %d" % (local + 1, slot.length))

    def _update_play_icon(self):
        self.play_btn.setIcon(_icon("pause" if self._playing else "play", color=BG, size=18))
        self.view.set_paused(not self._playing)

    # ---- actions
    def _open_help(self):
        path = os.path.join(HERE, "HELP.html")
        if os.path.isfile(path):
            webbrowser.open("file:///" + path.replace("\\", "/"))

    def _on_replayblast(self):
        if not self.replayblast:
            return
        self.pause()
        self.replay_btn.setEnabled(False)
        self.replay_btn.setText("Playblasting…")
        QtWidgets.QApplication.processEvents()
        try:
            result = self.replayblast()
        except Exception as e:
            self.status.setText("Playblast failed: %s" % e)
            result = None
        finally:
            self.replay_btn.setEnabled(True)
            self.replay_btn.setText("Re-playblast")
        if not result:
            return
        frames, first_frame, audio_path, audio_start, status = result
        i = self._current_index()
        slot = self.slots[i]
        for p in slot.frames:
            self._cache.pop(p, None)
        slot.frames, slot.first_frame = frames, first_frame
        slot.audio_path, slot.audio_start_frame = audio_path, audio_start
        self._setup_audio(slot)
        self.status.setText(status)
        self._relayout()
        self._seek(self._slot_start(max(0, i - 1)))
        self.play()

    def keyPressEvent(self, event):
        k = event.key()
        if k == QtCore.Qt.Key_Space:
            self.toggle_play()
        elif k == QtCore.Qt.Key_Left:
            self.step(-1)
        elif k == QtCore.Qt.Key_Right:
            self.step(1)
        elif k in (QtCore.Qt.Key_PageUp, QtCore.Qt.Key_Up):
            self.jump_shot(-1)
        elif k in (QtCore.Qt.Key_PageDown, QtCore.Qt.Key_Down):
            self.jump_shot(1)
        elif k == QtCore.Qt.Key_Home:
            self._seek(0)
        elif k == QtCore.Qt.Key_End:
            self._seek(self.total - 1)
        elif k == QtCore.Qt.Key_L:
            self.loop_btn.toggle()
        elif k == QtCore.Qt.Key_M:
            self.mute_btn.toggle()
            self._on_mute()
        elif k == QtCore.Qt.Key_Escape:
            self.close()
        else:
            super(ShotContextWindow, self).keyPressEvent(event)

    def closeEvent(self, event):
        self._timer.stop()
        for d in self._decoders:
            d.cancel()
        for c in self._clips.values():
            c.stop()
        super(ShotContextWindow, self).closeEvent(event)
