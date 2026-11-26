"""Test helpers: a fake detector and synthetic videos (imported by the tests)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ppe_guard.compliance import Detection


class FakeDetector:
    """Stands in for :class:`ppe_guard.detector.Detector`.

    Every frame shows one tracked worker. The worker is bare-headed (``NO-Hardhat``) while the
    frame's top-left pixel is bright, and wears a hard hat otherwise, so tests can script
    violations by drawing frames.
    """

    instances = 0

    def __init__(self, settings=None) -> None:
        from ppe_guard.config import DetectionSettings

        self.settings = settings or DetectionSettings()
        self.weights_path = Path("fake-ppe.pt")
        FakeDetector.instances += 1

    def reset_tracker(self) -> None:
        pass

    def __call__(self, frame, track: bool = True):
        h, w = frame.shape[:2]
        person = (w * 0.30, h * 0.20, w * 0.50, h * 0.95)
        head = (w * 0.35, h * 0.20, w * 0.45, h * 0.32)
        torso = (w * 0.32, h * 0.40, w * 0.48, h * 0.65)
        bare = frame[0, 0].mean() > 127
        return [
            Detection("Person", 0.9, person, 1 if track else None),
            Detection("NO-Hardhat" if bare else "Hardhat", 0.85, head),
            Detection("Safety Vest", 0.8, torso),
        ]



def make_video(path: Path, frames: int = 60, fps: float = 10.0, bare=lambda i: True,
               size=(320, 240)) -> Path:
    """Write a small synthetic video; ``bare(i)`` decides whether frame ``i`` is a violation."""
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
    assert w.isOpened()
    for i in range(frames):
        img = np.full((size[1], size[0], 3), 60, np.uint8)
        cv2.putText(img, str(i), (100, 130), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (200, 200, 200), 2)
        if bare(i):
            img[:8, :8] = 255
        w.write(img)
    w.release()
    return path
