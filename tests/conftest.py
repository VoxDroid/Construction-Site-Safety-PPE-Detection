"""Shared fixtures. Nothing here needs a GPU or downloads a model."""

from __future__ import annotations

import pytest

from helpers import FakeDetector, make_video


@pytest.fixture
def fake_detector(monkeypatch):
    import ppe_guard.camera
    import ppe_guard.detector

    monkeypatch.setattr(ppe_guard.camera, "Detector", FakeDetector)
    monkeypatch.setattr(ppe_guard.detector, "Detector", FakeDetector)
    return FakeDetector


@pytest.fixture
def video_file(tmp_path):
    return make_video(tmp_path / "clip.avi")
