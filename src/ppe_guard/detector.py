"""YOLO inference + tracking, and drawing per-worker results on frames."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ppe_guard.compliance import Detection, FrameResult, ItemStatus, PPEItem, WorkerState
from ppe_guard.config import DetectionSettings
from ppe_guard.weights import resolve


class Detector:
    """Wraps an Ultralytics model. One instance per video stream (trackers keep state)."""

    def __init__(self, settings: DetectionSettings | None = None) -> None:
        from ultralytics import YOLO  # imported lazily: it is slow and pulls in torch

        self.settings = settings or DetectionSettings()
        self.weights_path: Path = resolve(self.settings.weights)
        self.model = YOLO(str(self.weights_path))
        self.names: dict[int, str] = self.model.names

    def reset_tracker(self) -> None:
        """Forget track IDs, e.g. when a video file loops back to the start."""
        predictor = getattr(self.model, "predictor", None)
        for t in getattr(predictor, "trackers", None) or []:
            t.reset()

    def __call__(self, frame: np.ndarray, track: bool = True) -> list[Detection]:
        s = self.settings
        kw = dict(conf=s.confidence, iou=s.iou, imgsz=s.imgsz, device=s.device, verbose=False)
        if track:
            res = self.model.track(frame, persist=True, tracker=s.tracker, **kw)[0]
        else:
            res = self.model.predict(frame, **kw)[0]
        b = res.boxes
        if b is None or len(b) == 0:
            return []
        xyxy = b.xyxy.cpu().numpy()
        conf = b.conf.cpu().numpy()
        cls = b.cls.cpu().numpy().astype(int)
        ids = b.id.cpu().numpy().astype(int) if b.id is not None else [None] * len(cls)
        return [
            Detection(self.names[c], float(cf), tuple(float(v) for v in box), None if i is None else int(i))
            for box, cf, c, i in zip(xyxy, conf, cls, ids, strict=True)
        ]


# BGR colours
_STATE_COLOR = {
    WorkerState.COMPLIANT: (80, 175, 76),
    WorkerState.PENDING: (0, 165, 255),
    WorkerState.VIOLATION: (54, 67, 244),
    WorkerState.UNKNOWN: (160, 160, 160),
}
_ITEM_ABBR = {PPEItem.HELMET: "Helmet", PPEItem.VEST: "Vest", PPEItem.MASK: "Mask"}
_STATUS_MARK = {ItemStatus.WORN: "ok", ItemStatus.MISSING: "NO", ItemStatus.UNKNOWN: "?"}


def _label(img: np.ndarray, text: str, org: tuple[int, int], color: tuple[int, int, int]) -> None:
    font, scale, thick = cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1
    (tw, th), base = cv2.getTextSize(text, font, scale, thick)
    x, y = org
    y = max(y, th + base + 2)
    cv2.rectangle(img, (x, y - th - base - 4), (x + tw + 6, y), color, -1)
    cv2.putText(img, text, (x + 3, y - base - 1), font, scale, (255, 255, 255), thick, cv2.LINE_AA)


def draw(
    frame: np.ndarray,
    result: FrameResult,
    detections: list[Detection] | None = None,
    required: list[PPEItem] | None = None,
) -> np.ndarray:
    """Return a copy of ``frame`` with worker boxes and PPE status drawn on it."""
    img = frame.copy()
    required = required or list(PPEItem)

    if detections:
        for d in detections:
            if d.cls.lower() == "person":
                continue
            x1, y1, x2, y2 = (int(v) for v in d.box)
            bad = d.cls.lower().startswith("no")
            cv2.rectangle(img, (x1, y1), (x2, y2), (54, 67, 244) if bad else (200, 200, 60), 1)

    for w in result.workers:
        color = _STATE_COLOR[w.state]
        x1, y1, x2, y2 = (int(v) for v in w.box)
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 3 if w.state is WorkerState.VIOLATION else 2)
        parts = [f"{_ITEM_ABBR[i]}:{_STATUS_MARK[w.items[i]]}" for i in required]
        ident = ("?" if w.synthetic else "#") + w.key[1:]
        _label(img, f"{ident} " + " ".join(parts), (x1, y1), color)

    if result.violating:
        h, wd = img.shape[:2]
        cv2.rectangle(img, (0, 0), (wd - 1, h - 1), _STATE_COLOR[WorkerState.VIOLATION], 6)
        n = sum(1 for w in result.workers if w.state is WorkerState.VIOLATION)
        _label(img, f"PPE VIOLATION: {n} worker{'s' if n != 1 else ''}", (10, h - 12), (54, 67, 244))
    return img
