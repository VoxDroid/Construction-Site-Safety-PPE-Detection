"""Camera workers: capture → detect/track → compliance → snapshots, alerts and live frames."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import cv2

from ppe_guard.alerts import AlertDispatcher
from ppe_guard.compliance import ComplianceEngine, ViolationEvent
from ppe_guard.config import CameraConfig, Settings
from ppe_guard.detector import Detector, draw
from ppe_guard.storage import Storage

Publish = Callable[[dict], None]


def parse_source(source: str) -> int | str:
    """``"0"`` → webcam index 0; anything else is passed to OpenCV as a path or URL."""
    return int(source) if source.strip().isdigit() else source.strip()


def is_file_source(source: str) -> bool:
    s = parse_source(source)
    return isinstance(s, str) and "://" not in s and Path(s).exists()


class CameraWorker:
    def __init__(
        self,
        cfg: CameraConfig,
        get_settings: Callable[[], Settings],
        storage: Storage,
        alerts: AlertDispatcher,
        publish: Publish,
    ) -> None:
        self.cfg = cfg
        self._get_settings = get_settings
        self._storage = storage
        self._alerts = alerts
        self._publish = publish
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame_lock = threading.Lock()
        self._jpeg: bytes | None = None
        self.frame_id = 0
        self.status = "stopped"
        self.error: str | None = None
        self.fps = 0.0
        self.workers: list[dict] = []
        self.weights: str | None = None

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

    def info(self) -> dict:
        return {
            **self.cfg.model_dump(),
            "status": self.status,
            "error": self.error,
            "fps": round(self.fps, 1),
            "workers": self.workers,
            "weights": self.weights,
        }

    # ---- loop ---------------------------------------------------------------------------

    def _set_status(self, status: str, error: str | None = None) -> None:
        self.status, self.error = status, error
        self._publish({"type": "camera", "camera_id": self.cfg.id, "status": status, "error": error})

    def _open(self) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(parse_source(self.cfg.source))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run(self) -> None:
        settings = self._get_settings()
        try:
            detector = Detector(settings.detection)
            self.weights = detector.weights_path.name
        except Exception as e:
            self._set_status("error", f"could not load model: {e}")
            return
        engine = ComplianceEngine(settings.compliance)
        is_file = is_file_source(self.cfg.source)
        cap = self._open()
        if not cap.isOpened():
            self._set_status("error", f"could not open source '{self.cfg.source}'")
            return
        src_fps = cap.get(cv2.CAP_PROP_FPS) if is_file else 0.0
        self._set_status("running")

        backoff = 1.0
        last_settings = last_heartbeat = last_publish = time.monotonic()
        fps_t, fps_n = time.monotonic(), 0
        try:
            while not self._stop.is_set():
                t0 = time.monotonic()
                ok, frame = cap.read()
                if not ok or frame is None:
                    if is_file and self.cfg.loop:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        self._handle(engine.reset(), None)
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

                # Pick up settings changes once a second (weights need a camera restart).
                if t0 - last_settings > 1.0:
                    settings = self._get_settings()
                    engine.update_settings(settings.compliance)
                    detector.settings = settings.detection.model_copy(
                        update={"weights": detector.settings.weights}
                    )
                    last_settings = t0

                detections = detector(frame)
                result = engine.update(detections)
                annotated = draw(frame, result, detections, engine.required)
                self._handle(result.events, annotated)

                if t0 - last_heartbeat > 5.0:  # keep last_seen of ongoing violations fresh
                    for w in engine._workers.values():
                        if w.violation:
                            self._storage.update_violation(w.violation.to_dict())
                    last_heartbeat = t0

                ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    with self._frame_lock:
                        self._jpeg = buf.tobytes()
                        self.frame_id += 1

                self.workers = [w.to_dict() for w in result.workers]
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
            self._handle(engine.reset(), None)
            self.workers = []
            if self.status not in ("error", "ended"):
                self._set_status("stopped")

    def _handle(self, events: list[ViolationEvent], frame) -> None:
        for ev in events:
            v = ev.violation.to_dict()
            record = {**v, "camera_id": self.cfg.id, "camera_name": self.cfg.name}
            if ev.kind == "opened":
                self._storage.open_violation(v, self.cfg.id, self.cfg.name)
                self._alerts.notify(record)
            else:
                self._storage.update_violation(v)
            if ev.snapshot and frame is not None:
                self._storage.add_snapshot(v["id"], frame)
            self._publish({"type": "violation", "event": ev.kind, "violation": record})


class CameraManager:
    def __init__(self, get_settings, storage: Storage, alerts: AlertDispatcher, publish: Publish) -> None:
        self._get_settings = get_settings
        self._storage = storage
        self._alerts = alerts
        self._publish = publish
        self._workers: dict[str, CameraWorker] = {}
        self._lock = threading.Lock()

    def sync(self, cameras: list[CameraConfig]) -> None:
        """Align workers with the configured cameras (stopping removed or changed ones)."""
        with self._lock:
            wanted = {c.id: c for c in cameras}
            for cid in list(self._workers):
                w = self._workers[cid]
                new = wanted.get(cid)
                if new is None or new.source != w.cfg.source:
                    w.stop()
                    del self._workers[cid]
                elif new != w.cfg:
                    w.cfg = new
                    if not new.enabled:
                        w.stop()
            for cid, c in wanted.items():
                if cid not in self._workers:
                    self._workers[cid] = CameraWorker(c, self._get_settings, self._storage,
                                                      self._alerts, self._publish)

    def get(self, camera_id: str) -> CameraWorker | None:
        return self._workers.get(camera_id)

    def all(self) -> list[CameraWorker]:
        return list(self._workers.values())

    def stop_all(self) -> None:
        for w in self.all():
            w.stop()
