"""Camera workers: capture → detect/track → compliance → snapshots, clips, alerts and live frames."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from ppe_guard.alerts import AlertDispatcher
from ppe_guard.compliance import ComplianceEngine, ViolationEvent
from ppe_guard.config import CameraConfig, Settings
from ppe_guard.detector import Detector, draw
from ppe_guard.storage import Storage
from ppe_guard.video import ClipRecorder, ClipWriter

Publish = Callable[[dict], None]
ACTIVITY_FLUSH_SECONDS = 60.0


def parse_source(source: str) -> int | str:
    """``"0"`` → webcam index 0; anything else is passed to OpenCV as a path or URL."""
    return int(source) if source.strip().isdigit() else source.strip()


def is_file_source(source: str) -> bool:
    s = parse_source(source)
    return isinstance(s, str) and "://" not in s and Path(s).exists()


def grab_frame(source: str, timeout: float = 8.0) -> np.ndarray | None:
    """Read one frame from a source (used to draw zones while a camera is stopped)."""
    result: list[np.ndarray | None] = [None]

    def _read() -> None:
        cap = cv2.VideoCapture(parse_source(source))
        try:
            if not cap.isOpened():
                return
            if is_file_source(source):
                n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                if n > 10:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, n // 4)
            for _ in range(5):  # webcams often return black frames first
                ok, frame = cap.read()
                if ok and frame is not None:
                    result[0] = frame
                    if frame.mean() > 8:
                        break
        finally:
            cap.release()

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout)
    return result[0]


class CameraWorker:
    def __init__(
        self,
        cfg: CameraConfig,
        get_settings: Callable[[], Settings],
        storage: Storage,
        alerts: AlertDispatcher,
        publish: Publish,
        clips: ClipWriter | None = None,
    ) -> None:
        self.cfg = cfg
        self._get_settings = get_settings
        self._storage = storage
        self._alerts = alerts
        self._publish = publish
        self._clips = clips
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame_lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._raw: np.ndarray | None = None
        self.frame_id = 0
        self.status = "stopped"
        self.error: str | None = None
        self.fps = 0.0
        self.workers: list[dict] = []
        self.weights: str | None = None
        self.started_at: float | None = None
        self.frame_size: tuple[int, int] | None = None

    # ---- control ------------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self.error = None
        self.status = "starting"
        self._thread = threading.Thread(target=self._run, name=f"camera-{self.cfg.id}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None

    def latest_jpeg(self) -> tuple[int, bytes | None]:
        with self._frame_lock:
            return self.frame_id, self._jpeg

    def latest_raw_jpeg(self) -> bytes | None:
        """The last frame without annotations (for the zone editor)."""
        with self._frame_lock:
            raw = self._raw
        if raw is None:
            return None
        ok, buf = cv2.imencode(".jpg", raw, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes() if ok else None

    def info(self) -> dict:
        return {
            **self.cfg.model_dump(),
            "status": self.status,
            "error": self.error,
            "fps": round(self.fps, 1),
            "workers": self.workers,
            "weights": self.weights,
            "started_at": self.started_at,
            "frame_size": self.frame_size,
        }

    # ---- loop ---------------------------------------------------------------------------

    def _set_status(self, status: str, error: str | None = None) -> None:
        self.status, self.error = status, error
        self._publish({"type": "camera", "camera_id": self.cfg.id, "status": status, "error": error})

    def _open(self) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(parse_source(self.cfg.source))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _flush_activity(self, engine: ComplianceEngine) -> None:
        a = engine.take_activity()
        self._storage.add_activity(self.cfg.id, time.time(), a.worker_seconds, a.violation_seconds,
                                   a.peak_workers)

    def _run(self) -> None:
        settings = self._get_settings()
        try:
            detector = Detector(settings.detection)
            self.weights = detector.weights_path.name
        except Exception as e:
            self._set_status("error", f"could not load model: {e}")
            return
        engine = ComplianceEngine(settings.compliance, zones=self.cfg.zones)
        rec = settings.recording
        recorder = ClipRecorder(fps=rec.fps, pre_seconds=rec.pre_seconds, max_seconds=rec.max_seconds)
        is_file = is_file_source(self.cfg.source)
        cap = self._open()
        if not cap.isOpened():
            self._set_status("error", f"could not open source '{self.cfg.source}'")
            return
        src_fps = cap.get(cv2.CAP_PROP_FPS) if is_file else 0.0
        self.started_at = time.time()
        self._set_status("running")

        backoff = 1.0
        last_settings = last_heartbeat = last_publish = last_activity = time.monotonic()
        fps_t, fps_n = time.monotonic(), 0
        try:
            while not self._stop.is_set():
                t0 = time.monotonic()
                ok, frame = cap.read()
                if not ok or frame is None:
                    if is_file and self.cfg.loop:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        self._handle(engine.reset(), None, recorder, settings)
                        detector.reset_tracker()
                        continue
                    if is_file:
                        self._set_status("ended")
                        break
                    self._set_status("reconnecting", "stream lost")
                    cap.release()
                    if self._stop.wait(backoff):
                        break
                    backoff = min(backoff * 2, 30.0)
                    cap = self._open()
                    if cap.isOpened():
                        self._set_status("running")
                    continue
                backoff = 1.0

                # Pick up settings and zone changes once a second (weights need a restart).
                if t0 - last_settings > 1.0:
                    settings = self._get_settings()
                    engine.update_settings(settings.compliance)
                    engine.set_zones(self.cfg.zones)
                    rec = settings.recording
                    recorder.configure(rec.fps, rec.pre_seconds, rec.max_seconds)
                    detector.settings = settings.detection.model_copy(
                        update={"weights": detector.settings.weights}
                    )
                    last_settings = t0

                h, w = frame.shape[:2]
                self.frame_size = (w, h)
                detections = detector(frame)
                result = engine.update(detections, frame_size=(w, h))
                annotated = draw(frame, result, detections, engine.required, engine.zones)

                ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
                jpeg = buf.tobytes() if ok else None
                if jpeg is not None:
                    with self._frame_lock:
                        self._jpeg, self._raw = jpeg, frame
                        self.frame_id += 1
                    if settings.recording.clips:
                        for clip in recorder.push(time.time(), jpeg):
                            self._write_clip(clip, settings)

                self._handle(result.events, annotated, recorder, settings)

                if t0 - last_heartbeat > 5.0:  # keep last_seen of ongoing violations fresh
                    for v in engine.open_violations():
                        self._storage.update_violation(v.to_dict())
                    last_heartbeat = t0
                if t0 - last_activity > ACTIVITY_FLUSH_SECONDS:
                    self._flush_activity(engine)
                    last_activity = t0

                self.workers = [wv.to_dict() for wv in result.workers]
                fps_n += 1
                now = time.monotonic()
                if now - fps_t >= 1.0:
                    self.fps, fps_t, fps_n = fps_n / (now - fps_t), now, 0
                if now - last_publish >= 0.25:
                    self._publish({"type": "frame", "camera_id": self.cfg.id, "fps": round(self.fps, 1),
                                   "violating": result.violating, "workers": self.workers})
                    last_publish = now

                # Pace: never faster than max_fps, and play files at their native speed.
                limit = settings.detection.max_fps
                if is_file and src_fps > 0:
                    limit = min(limit, src_fps)
                remaining = 1.0 / limit - (time.monotonic() - t0)
                if remaining > 0:
                    self._stop.wait(remaining)
        except Exception as e:
            self._set_status("error", f"{type(e).__name__}: {e}")
        finally:
            cap.release()
            self._handle(engine.reset(), None, recorder, settings)
            for clip in recorder.flush():
                self._write_clip(clip, settings)
            self._flush_activity(engine)
            self.workers = []
            with self._frame_lock:
                self._raw = None
            if self.status not in ("error", "ended"):
                self._set_status("stopped")

    def _write_clip(self, clip, settings: Settings) -> None:
        if self._clips is not None:
            self._clips.submit(clip, settings.recording.fps, settings.recording.max_width)

    def _handle(self, events: list[ViolationEvent], frame, recorder: ClipRecorder,
                settings: Settings) -> None:
        now = time.time()
        for ev in events:
            v = ev.violation.to_dict()
            record = {**v, "camera_id": self.cfg.id, "camera_name": self.cfg.name}
            if ev.kind == "opened":
                self._storage.open_violation(v, self.cfg.id, self.cfg.name)
                if settings.recording.clips:
                    recorder.start(v["id"], now)
                self._alerts.notify(record)
            else:
                self._storage.update_violation(v)
                if ev.kind == "closed":
                    recorder.stop(v["id"], now)
            if ev.snapshot and frame is not None:
                self._storage.add_snapshot(v["id"], frame)
            self._publish({"type": "violation", "event": ev.kind, "violation": record})


class CameraManager:
    def __init__(self, get_settings, storage: Storage, alerts: AlertDispatcher, publish: Publish,
                 clips: ClipWriter | None = None) -> None:
        self._get_settings = get_settings
        self._storage = storage
        self._alerts = alerts
        self._publish = publish
        self._clips = clips
        self._workers: dict[str, CameraWorker] = {}
        self._lock = threading.Lock()

    def sync(self, cameras: list[CameraConfig]) -> None:
        """Align workers with the configured cameras (stopping removed or changed ones)."""
        with self._lock:
            wanted = {c.id: c for c in cameras}
            restart = set()
            for cid in list(self._workers):
                w = self._workers[cid]
                new = wanted.get(cid)
                if new is None or new.source != w.cfg.source:
                    if w.running and new is not None and new.enabled:
                        restart.add(cid)  # same camera, new source: keep it running
                    w.stop()
                    del self._workers[cid]
                elif new != w.cfg:
                    w.cfg = new
                    if not new.enabled:
                        w.stop()
            for cid, c in wanted.items():
                if cid not in self._workers:
                    self._workers[cid] = CameraWorker(c, self._get_settings, self._storage,
                                                      self._alerts, self._publish, self._clips)
                    if cid in restart:
                        self._workers[cid].start()

    def get(self, camera_id: str) -> CameraWorker | None:
        return self._workers.get(camera_id)

    def all(self) -> list[CameraWorker]:
        return list(self._workers.values())

    def stop_all(self) -> None:
        for w in self.all():
            w.stop()
