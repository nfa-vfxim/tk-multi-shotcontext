"""
Turns every slot into frames the viewer can play frame accurately.

Movies are decoded once with QtMultimedia (ships with Maya 2025/2026) into a
local JPG cache: %TEMP%/nfa_shot_context/cache/<hash>/. Decoding runs faster
than real time without audio; if frames go missing it retries at 1x.
The cache is keyed on path + size + modification time, so a newer playblast
is decoded again and an unchanged one opens instantly next time.
"""

import hashlib
import json
import os
import shutil
import tempfile

try:
    from PySide6 import QtCore, QtMultimedia
except ImportError:  # Maya 2024 and older
    from PySide2 import QtCore, QtMultimedia

CACHE_ROOT = os.path.join(tempfile.gettempdir(), "nfa_shot_context", "cache")
CACHE_MAX_BYTES = 3 * 1024 ** 3   # trimmed oldest-first above this
DECODE_WIDTH = 1280
FAST_RATE = 4.0
QT6 = hasattr(QtMultimedia, "QVideoSink")


def cache_key(path):
    try:
        st = os.stat(path)
        raw = "%s|%d|%d" % (os.path.normcase(os.path.abspath(path)), st.st_size, int(st.st_mtime))
    except OSError:
        raw = path
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def cached_frames(path):
    """(frames, fps) if this movie was decoded before, else (None, None)."""
    folder = os.path.join(CACHE_ROOT, cache_key(path))
    info = os.path.join(folder, "done.json")
    if not os.path.isfile(info):
        return None, None
    try:
        with open(info) as f:
            data = json.load(f)
        frames = [os.path.join(folder, n) for n in data["frames"]]
        if frames and all(os.path.isfile(p) for p in (frames[0], frames[-1])):
            try:
                os.utime(info, None)  # mark as recently used
            except OSError:
                pass
            return frames, data.get("fps")
    except Exception:
        pass
    return None, None


def trim_cache(keep=()):
    """Delete least recently used decodes when the cache grows too big."""
    try:
        entries = []
        total = 0
        for name in os.listdir(CACHE_ROOT):
            d = os.path.join(CACHE_ROOT, name)
            if not os.path.isdir(d):
                continue
            size = sum(os.path.getsize(os.path.join(d, f)) for f in os.listdir(d))
            used = os.path.getmtime(os.path.join(d, "done.json")) if os.path.isfile(
                os.path.join(d, "done.json")) else 0
            entries.append((used, size, d))
            total += size
        for used, size, d in sorted(entries):
            if total <= CACHE_MAX_BYTES:
                break
            if os.path.basename(d) in keep:
                continue
            shutil.rmtree(d, ignore_errors=True)
            total -= size
    except OSError:
        pass


class _Saver(QtCore.QRunnable):
    def __init__(self, image, path):
        super(_Saver, self).__init__()
        self.image, self.path = image, path

    def run(self):
        self.image.save(self.path, "JPG", 88)


class MovieDecoder(QtCore.QObject):
    """Decode one movie to JPG frames. Emits finished(frames, fps, error)."""

    finished = QtCore.Signal(list, float, str)
    progress = QtCore.Signal(float)

    def __init__(self, path, parent=None):
        super(MovieDecoder, self).__init__(parent)
        self.path = path
        self.key = cache_key(path)
        self.folder = os.path.join(CACHE_ROOT, self.key)
        self.fps = 0.0
        self._images = {}
        self._rate = FAST_RATE
        self._expected = 0
        self._done = False
        self._pool = QtCore.QThreadPool(self)
        self._pool.setMaxThreadCount(max(2, QtCore.QThread.idealThreadCount() // 2))
        self._player = None
        self._sink = None
        self._watchdog = QtCore.QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_stalled)

    def start(self):
        frames, fps = cached_frames(self.path)
        if frames:
            QtCore.QTimer.singleShot(0, lambda: self._emit(frames, fps or 0.0, ""))
            return
        if not QT6:
            QtCore.QTimer.singleShot(0, lambda: self._emit([], 0.0, "QtMultimedia video decoding needs Maya 2025 or newer"))
            return
        shutil.rmtree(self.folder, ignore_errors=True)
        try:
            os.makedirs(self.folder)
        except OSError as e:
            message = "Can't write the frame cache: %s" % e
            QtCore.QTimer.singleShot(0, lambda: self._emit([], 0.0, message))
            return
        self._run()

    def _run(self):
        self._images.clear()
        self._player = QtMultimedia.QMediaPlayer(self)
        self._sink = QtMultimedia.QVideoSink(self)
        self._player.setVideoSink(self._sink)
        self._sink.videoFrameChanged.connect(self._on_frame)
        self._player.mediaStatusChanged.connect(self._on_status)
        self._player.errorOccurred.connect(self._on_error)
        self._player.setSource(QtCore.QUrl.fromLocalFile(self.path))
        self._watchdog.start(15000)

    def _on_status(self, status):
        S = QtMultimedia.QMediaPlayer.MediaStatus
        if status == S.LoadedMedia and self._player.playbackState() != QtMultimedia.QMediaPlayer.PlaybackState.PlayingState:
            fps = self._player.metaData().value(QtMultimedia.QMediaMetaData.Key.VideoFrameRate)
            try:
                self.fps = float(fps) if fps else 0.0
            except (TypeError, ValueError):
                self.fps = 0.0
            if self.fps <= 0:
                self.fps = 25.0
            self._expected = int(round(self._player.duration() * self.fps / 1000.0))
            self._player.setPlaybackRate(self._rate)
            self._player.play()
        elif status == S.EndOfMedia:
            QtCore.QTimer.singleShot(150, self._finish_pass)
        elif status == S.InvalidMedia:
            self._fail("Can't read this movie")

    def _on_error(self, *args):
        if not self._done:
            self._fail(self._player.errorString() or "Can't read this movie")

    def _on_frame(self, frame):
        if self._done or not frame.isValid():
            return
        self._watchdog.start(15000)
        index = int(round(frame.startTime() * self.fps / 1000000.0))
        if index in self._images or index < 0:
            return
        image = frame.toImage()
        if image.isNull():
            return
        if image.width() > DECODE_WIDTH:
            image = image.scaledToWidth(DECODE_WIDTH, QtCore.Qt.SmoothTransformation)
        name = "f%05d.jpg" % index
        self._images[index] = name
        self._pool.start(_Saver(image, os.path.join(self.folder, name)))
        if self._expected:
            self.progress.emit(min(1.0, len(self._images) / float(self._expected)))

    def _finish_pass(self):
        if self._done:
            return
        got = len(self._images)
        if self._rate > 1.0 and self._expected and got < self._expected * 0.97:
            # Frames were dropped at speed: decode again in real time.
            self._teardown()
            self._rate = 1.0
            self._run()
            return
        self._pool.waitForDone()
        self._teardown()
        if not self._images:
            self._fail("No frames could be decoded from this movie")
            return
        names = []
        last = None
        for i in range(0, max(self._images) + 1):
            last = self._images.get(i, last)
            if last:
                names.append(last)  # fill gaps with the previous frame
        with open(os.path.join(self.folder, "done.json"), "w") as f:
            json.dump({"source": self.path, "fps": self.fps, "frames": names}, f)
        trim_cache(keep=(self.key,))
        self._emit([os.path.join(self.folder, n) for n in names], self.fps, "")

    def _on_stalled(self):
        if self._done:
            return
        if self._images:
            self._finish_pass_forced()
        else:
            self._fail("Timed out while reading this movie")

    def _finish_pass_forced(self):
        self._rate = 1.0  # no retry
        self._finish_pass()

    def _fail(self, message):
        self._teardown()
        shutil.rmtree(self.folder, ignore_errors=True)
        self._emit([], 0.0, message)

    def _teardown(self):
        self._watchdog.stop()
        if self._player is not None:
            try:
                self._sink.videoFrameChanged.disconnect(self._on_frame)
            except Exception:
                pass
            self._player.stop()
            self._player.deleteLater()
            self._sink.deleteLater()
            self._player = self._sink = None

    def _emit(self, frames, fps, error):
        if self._done:
            return
        self._done = True
        self.finished.emit(frames, float(fps or 0.0), error)

    def cancel(self):
        self._done = True
        self._teardown()
