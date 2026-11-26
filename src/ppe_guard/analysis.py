"""Analyze uploaded images and videos (the dashboard's *Analyze* page).

Images are judged at once. Videos become background jobs: they are processed on the video's
own clock, the annotated result is saved as a browser-playable video, and every violation is
listed with a snapshot and its time in the video.
"""

from __future__ import annotations

import contextlib
import json
import queue
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import cv2

from ppe_guard.compliance import ComplianceEngine, WorkerState, evaluate_still
from ppe_guard.config import Settings
from ppe_guard.video import fit_width, open_writer

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".jfif", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".mpg", ".mpeg", ".wmv"}
OUTPUT_MAX_WIDTH = 1280

Publish = Callable[[dict], None]


def kind_of(filename: str) -> str | None:
    ext = Path(filename).suffix.lower()
    return "image" if ext in IMAGE_EXT else "video" if ext in VIDEO_EXT else None


class JobCancelled(Exception):
    pass


class AnalysisManager:
    def __init__(self, data_dir: Path, get_settings: Callable[[], Settings], publish: Publish) -> None:
        self.root = data_dir / "analysis"
        self.root.mkdir(parents=True, exist_ok=True)
        self._get_settings = get_settings
        self._publish = publish
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._cancel: set[str] = set()
        self._image_det = None
        self._image_det_lock = threading.Lock()
        self._q: queue.Queue[str | None] = queue.Queue()
        self._load()
        self._thread = threading.Thread(target=self._run, name="analysis", daemon=True)
        self._thread.start()

    # ---- bookkeeping --------------------------------------------------------------------

    def _load(self) -> None:
        for f in self.root.glob("*/job.json"):
            try:
                job = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if job.get("status") in ("queued", "running"):
                job.update(status="failed", error="interrupted by a server restart")
                self._write(job)
            self._jobs[job["id"]] = job

    def _write(self, job: dict) -> None:
        d = self.root / job["id"]
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "job.tmp"
        tmp.write_text(json.dumps(job), encoding="utf-8")
        tmp.replace(d / "job.json")

    def _update(self, job: dict, publish: bool = True, **changes) -> None:
        with self._lock:
            job.update(changes)
            self._write(job)
        if publish:
            self._publish({"type": "job", "job": self.public(job)})

    @staticmethod
    def public(job: dict) -> dict:
        return {k: v for k, v in job.items() if not k.startswith("_")}

    def list(self) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: -j["created_at"])
        return [self.public(j) for j in jobs]

    def get(self, job_id: str) -> dict | None:
        j = self._jobs.get(job_id)
        return self.public(j) if j else None

    def file_path(self, job_id: str, filename: str) -> Path | None:
        if job_id not in self._jobs:
            return None
        folder = (self.root / job_id).resolve()
        p = (folder / filename).resolve()
        if p.parent != folder or not p.is_file() or p.name in ("job.json",) or p.name.startswith("input"):
            return None
        return p

    def create(self, filename: str) -> tuple[dict, Path]:
        kind = kind_of(filename)
        if kind is None:
            raise ValueError("unsupported file type")
        job_id = uuid.uuid4().hex[:12]
        d = self.root / job_id
        d.mkdir(parents=True)
        safe_name = Path(filename).name[:120]
        job = {"id": job_id, "kind": kind, "filename": safe_name, "status": "uploading",
               "progress": 0.0, "created_at": time.time(), "finished_at": None, "error": None,
               "result": None}
        with self._lock:
            self._jobs[job_id] = job
        return job, d / f"input{Path(filename).suffix.lower()}"

    def discard(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
        shutil.rmtree(self.root / job_id, ignore_errors=True)

    def delete(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return False
        if job["status"] in ("queued", "running"):
            self._cancel.add(job_id)
            for _ in range(50):
                if job["status"] not in ("queued", "running"):
                    break
                time.sleep(0.1)
        self.discard(job_id)
        return True

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job["status"] not in ("queued", "running"):
            return False
        self._cancel.add(job_id)
        if job["status"] == "queued":
            self._update(job, status="cancelled", finished_at=time.time())
        return True

    def prune(self, keep: int) -> None:
        done = [j for j in self.list() if j["status"] not in ("queued", "running", "uploading")]
        for j in done[keep:]:
            self.discard(j["id"])

    def close(self) -> None:
        for job_id, j in list(self._jobs.items()):
            if j["status"] in ("queued", "running"):
                self._cancel.add(job_id)
        self._q.put(None)
        self._thread.join(10)

    # ---- running ------------------------------------------------------------------------

    def start(self, job_id: str) -> dict:
        """Run an uploaded job: images synchronously, videos on the background queue."""
        job = self._jobs[job_id]
        self.prune(self._get_settings().analysis.keep_jobs)
        if job["kind"] == "image":
            self._update(job, publish=False, status="running")
            try:
                self._run_image(job)
            except Exception as e:
                self._update(job, status="failed", error=f"{type(e).__name__}: {e}", finished_at=time.time())
            return self.public(job)
        self._update(job, status="queued")
        self._q.put(job_id)
        return self.public(job)

    def _detector(self, settings: Settings):
        from ppe_guard.detector import Detector

        if self._image_det is None or self._image_det.settings.weights != settings.detection.weights:
            self._image_det = Detector(settings.detection)
        else:
            self._image_det.settings = settings.detection
        return self._image_det

    def _run_image(self, job: dict) -> None:
        from ppe_guard.compliance import PPEItem
        from ppe_guard.detector import draw

        settings = self._get_settings()
        d = self.root / job["id"]
        src = next(d.glob("input.*"))
        frame = cv2.imread(str(src))
        if frame is None:
            raise ValueError("could not read the image")
        required = [PPEItem(k) for k, v in settings.compliance.required_ppe.model_dump().items() if v]
        with self._image_det_lock:
            det = self._detector(settings)
            dets = det(frame, track=False)
            weights = det.weights_path.name
        res = evaluate_still(dets, required, settings.compliance.min_overlap,
                             settings.compliance.treat_unseen_as_missing)
        out = draw(frame, res, dets, required)
        cv2.imwrite(str(d / "result.jpg"), out, [cv2.IMWRITE_JPEG_QUALITY, 90])
        src.unlink(missing_ok=True)
        workers = [w.to_dict() for w in res.workers]
        h, w = frame.shape[:2]
        self._update(job, status="done", progress=1.0, finished_at=time.time(), result={
            "image": "result.jpg",
            "width": w,
            "height": h,
            "weights": weights,
            "required": [r.value for r in required],
            "workers": workers,
            "counts": {
                "workers": sum(1 for x in res.workers if not x.synthetic),
                "violations": sum(1 for x in res.workers if x.state is WorkerState.VIOLATION),
                "compliant": sum(1 for x in res.workers if x.state is WorkerState.COMPLIANT),
                "unknown": sum(1 for x in res.workers if x.state is WorkerState.UNKNOWN),
            },
            "detections": [{"cls": x.cls, "conf": round(x.conf, 3), "box": [round(v, 1) for v in x.box]}
                           for x in dets],
        })

    def _run(self) -> None:
        while True:
            job_id = self._q.get()
            if job_id is None:
                return
            job = self._jobs.get(job_id)
            if job is None or job["status"] != "queued":
                continue
            try:
                self._run_video(job)
            except JobCancelled:
                self._update(job, status="cancelled", finished_at=time.time())
            except Exception as e:
                self._update(job, status="failed", error=f"{type(e).__name__}: {e}", finished_at=time.time())
            finally:
                self._cancel.discard(job_id)

    def _run_video(self, job: dict) -> None:
        from ppe_guard.detector import Detector, draw

        settings = self._get_settings()
        d = self.root / job["id"]
        src = next(d.glob("input.*"))
        cap = cv2.VideoCapture(str(src))
        if not cap.isOpened():
            raise ValueError("could not open the video")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        if not 1 <= fps <= 240:
            fps = 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        self._update(job, status="running", progress=0.0)
        det = Detector(settings.detection)
        engine = ComplianceEngine(settings.compliance)
        writer = out_path = fmt = None
        violations: dict[str, dict] = {}
        workers_seen: set[str] = set()
        n, t_start, last_pub = 0, time.time(), 0.0

        def record(events, frame, t):
            for ev in events:
                v = ev.violation
                row = violations.get(v.id)
                if row is None:
                    snap = f"v_{v.id}.jpg"
                    if frame is not None:
                        cv2.imwrite(str(d / snap), fit_width(frame, 960), [cv2.IMWRITE_JPEG_QUALITY, 85])
                    row = violations[v.id] = {"id": v.id, "kind": v.kind, "worker": v.worker_key,
                                              "missing": [], "zone": v.zone_name, "start": round(t, 2),
                                              "end": None, "snapshot": snap if frame is not None else None}
                row["missing"] = sorted(i.value for i in v.missing)
                if ev.kind == "closed":
                    row["end"] = round(v.ended_at if v.ended_at is not None else t, 2)

        try:
            while True:
                if job["id"] in self._cancel:
                    raise JobCancelled
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                t = n / fps
                dets = det(frame)
                h, w = frame.shape[:2]
                res = engine.update(dets, now=t, frame_size=(w, h))
                out = draw(frame, res, dets, engine.required)
                record(res.events, out, t)
                workers_seen |= {x.key for x in res.workers if not x.synthetic}
                out = fit_width(out, OUTPUT_MAX_WIDTH)
                if writer is None:
                    size = (out.shape[1] // 2 * 2, out.shape[0] // 2 * 2)
                    writer, out_path, fmt = open_writer(d / "result", fps, size)
                if (out.shape[1], out.shape[0]) != size:
                    out = cv2.resize(out, size)
                writer.write(out)
                n += 1
                now = time.time()
                if now - last_pub > 0.5:
                    last_pub = now
                    self._update(job, progress=round(min(n / total, 0.99), 3) if total else 0.0,
                                 frames=n)
        finally:
            cap.release()
            if writer is not None:
                writer.release()
        record(engine.reset(), None, n / fps)
        act = engine.take_activity()
        with contextlib.suppress(OSError):
            src.unlink()
        if n == 0:
            raise ValueError("the video has no readable frames")
        elapsed = max(time.time() - t_start, 1e-6)
        rows = sorted(violations.values(), key=lambda r: r["start"])
        self._update(job, status="done", progress=1.0, finished_at=time.time(), frames=n, result={
            "video": out_path.name if out_path else None,
            "mime": fmt.mime if fmt else None,
            "playable": fmt.playable if fmt else False,
            "fps": round(fps, 2),
            "frames": n,
            "duration": round(n / fps, 2),
            "processing_fps": round(n / elapsed, 1),
            "weights": det.weights_path.name,
            "required": [r.value for r in engine.required],
            "workers_seen": len(workers_seen),
            "compliance_rate": round(1 - act.violation_seconds / act.worker_seconds, 4)
            if act.worker_seconds > 0 else None,
            "violations": rows,
        })
