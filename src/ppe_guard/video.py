"""Video writing with a browser-friendly codec, and violation clip recording."""

from __future__ import annotations

import contextlib
import queue
import tempfile
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

# (extension, fourcc, mime type, plays in browsers). H.264 needs an OpenCV build with an
# H.264 encoder (the Windows wheels have one); VP8 WebM works with the stock Linux wheels.
_CANDIDATES = [
    (".mp4", "avc1", "video/mp4", True),
    (".webm", "VP80", "video/webm", True),
    (".mp4", "mp4v", "video/mp4", False),
]
_codec_cache: tuple[str, str, str, bool] | None = None
_codec_lock = threading.Lock()


@dataclass(frozen=True)
class VideoFormat:
    ext: str
    fourcc: str
    mime: str
    playable: bool


def _probe() -> tuple[str, str, str, bool]:
    with tempfile.TemporaryDirectory() as d:
        for ext, cc, mime, playable in _CANDIDATES:
            p = Path(d) / f"probe{ext}"
            with _quiet_opencv():
                w = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*cc), 5, (320, 240))
            ok = w.isOpened()
            if ok:
                for _ in range(3):
                    w.write(np.zeros((240, 320, 3), np.uint8))
            w.release()
            if ok and p.exists() and p.stat().st_size > 0:
                return ext, cc, mime, playable
    return _CANDIDATES[-1]


def video_format() -> VideoFormat:
    """The best codec this OpenCV build can write (probed once)."""
    global _codec_cache
    with _codec_lock:
        if _codec_cache is None:
            _codec_cache = _probe()
    return VideoFormat(*_codec_cache)


@contextlib.contextmanager
def _quiet_opencv():
    """OpenCV logs an error for every H.264 encoder it tries before one works."""
    try:
        old = cv2.utils.logging.getLogLevel()
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_SILENT)
    except AttributeError:  # pragma: no cover - very old OpenCV
        yield
        return
    try:
        yield
    finally:
        cv2.utils.logging.setLogLevel(old)


def open_writer(stem: Path, fps: float, size: tuple[int, int]) -> tuple[cv2.VideoWriter, Path, VideoFormat]:
    """Open a writer for ``stem`` + the best extension. ``size`` is (width, height).

    Falls back to the next codec if the preferred one refuses this frame size.
    """
    first = video_format()
    order = [first] + [VideoFormat(*c) for c in _CANDIDATES if c[1] != first.fourcc]
    for fmt in order:
        path = stem.with_suffix(fmt.ext)
        with _quiet_opencv():
            w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fmt.fourcc), max(fps, 1.0), size)
        if w.isOpened():
            return w, path, fmt
        w.release()
        path.unlink(missing_ok=True)
    raise RuntimeError("OpenCV could not open a video writer for any codec")


def fit_width(frame: np.ndarray, max_width: int) -> np.ndarray:
    h, w = frame.shape[:2]
    if w <= max_width:
        return frame
    scale = max_width / w
    # Even dimensions keep every encoder happy.
    size = (int(w * scale) // 2 * 2, int(h * scale) // 2 * 2)
    return cv2.resize(frame, size, interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------------------- clips


@dataclass
class _Clip:
    violation_id: str
    frames: list[tuple[float, bytes]]
    deadline: float  # stop recording at this time
    closing: bool = False


@dataclass
class ClipRecorder:
    """Keeps a rolling pre-roll of JPEG frames and records clips for open violations.

    Frames are kept JPEG-encoded (the camera already encodes them for the live view), so a
    20 second clip at 8 fps costs a few megabytes of memory until it is written out.
    """

    fps: float = 8.0
    pre_seconds: float = 4.0
    max_seconds: float = 20.0
    post_seconds: float = 1.5
    _pre: deque = field(default_factory=deque)
    _active: dict[str, _Clip] = field(default_factory=dict)
    _last_t: float = -1e9

    def configure(self, fps: float, pre_seconds: float, max_seconds: float) -> None:
        self.fps, self.pre_seconds, self.max_seconds = fps, pre_seconds, max_seconds

    def push(self, t: float, jpeg: bytes) -> list[_Clip]:
        """Add a frame; returns clips that are complete and should be written."""
        if t - self._last_t >= 1.0 / self.fps:
            self._last_t = t
            self._pre.append((t, jpeg))
            while self._pre and self._pre[0][0] < t - self.pre_seconds:
                self._pre.popleft()
            for c in self._active.values():
                c.frames.append((t, jpeg))
        done = [c for c in self._active.values() if t >= c.deadline]
        for c in done:
            del self._active[c.violation_id]
        return done

    def start(self, violation_id: str, t: float) -> None:
        if violation_id in self._active:
            return
        start = t - self.pre_seconds
        frames = [f for f in self._pre if f[0] >= start]
        self._active[violation_id] = _Clip(violation_id, frames, deadline=t + self.max_seconds - self.pre_seconds)

    def stop(self, violation_id: str, t: float) -> None:
        c = self._active.get(violation_id)
        if c is not None and not c.closing:
            c.closing = True
            c.deadline = min(c.deadline, t + self.post_seconds)

    def flush(self) -> list[_Clip]:
        """Finish every clip now (camera stopping)."""
        done = list(self._active.values())
        self._active.clear()
        return done

    @property
    def recording(self) -> int:
        return len(self._active)


class ClipWriter:
    """Encodes finished clips on a background thread."""

    def __init__(self, out_dir: Path, on_saved: Callable[[str, str], None]) -> None:
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._on_saved = on_saved
        self._q: queue.Queue = queue.Queue(maxsize=50)
        self._thread = threading.Thread(target=self._run, name="clip-writer", daemon=True)
        self._thread.start()

    def submit(self, clip: _Clip, fps: float, max_width: int) -> bool:
        if len(clip.frames) < 2:
            return False
        try:
            self._q.put_nowait((clip, fps, max_width))
        except queue.Full:
            return False
        return True

    def close(self, timeout: float = 30.0) -> None:
        self._q.put(None)
        self._thread.join(timeout)

    def _run(self) -> None:
        while True:
            job = self._q.get()
            if job is None:
                return
            clip, fps, max_width = job
            try:
                name = self.write(clip, fps, max_width)
                if name:
                    self._on_saved(clip.violation_id, name)
            except Exception as e:  # never let one bad clip kill the writer
                print(f"[ppe-guard] could not write clip for {clip.violation_id}: {e}")

    def write(self, clip: _Clip, fps: float, max_width: int) -> str | None:
        writer = path = None
        size = None
        try:
            for _, jpeg in clip.frames:
                frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    continue
                frame = fit_width(frame, max_width)
                if writer is None:
                    size = (frame.shape[1] // 2 * 2, frame.shape[0] // 2 * 2)
                    writer, path, _ = open_writer(self.out_dir / clip.violation_id, fps, size)
                if (frame.shape[1], frame.shape[0]) != size:
                    frame = cv2.resize(frame, size)
                writer.write(frame)
        finally:
            if writer is not None:
                writer.release()
        return path.name if path is not None and path.exists() else None
