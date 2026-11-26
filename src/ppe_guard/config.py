"""Settings models and their on-disk store.

Settings live in ``<data dir>/settings.json``. The data directory defaults to ``./data``
and can be changed with ``--data-dir`` or the ``PPE_GUARD_DATA_DIR`` environment variable.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator


class RequiredPPE(BaseModel):
    helmet: bool = True
    vest: bool = True
    mask: bool = False


class ComplianceSettings(BaseModel):
    required_ppe: RequiredPPE = Field(default_factory=RequiredPPE)
    violation_delay: float = Field(
        2.0, ge=0, le=60, description="Seconds an item must look missing before a violation opens."
    )
    clear_after: float = Field(
        3.0, ge=0, le=300, description="Seconds of compliance before an open violation closes."
    )
    lost_timeout: float = Field(
        3.0, ge=0.1, le=300, description="Seconds a worker may be out of view before being forgotten."
    )
    treat_unseen_as_missing: bool = Field(
        False,
        description="Strict mode: a required item that is not visible counts as missing.",
    )
    min_overlap: float = Field(
        0.5, gt=0, le=1, description="Share of a PPE box that must lie inside a person box."
    )
    snapshot_interval: float = Field(10.0, ge=1, le=3600)
    max_snapshots: int = Field(5, ge=1, le=100)


class DetectionSettings(BaseModel):
    weights: str = Field("auto", description="Path, model alias (see `ppe-guard weights list`) or 'auto'.")
    confidence: float = Field(0.40, ge=0.01, le=1)
    iou: float = Field(0.50, ge=0.05, le=1)
    imgsz: int = Field(640, ge=160, le=1920)
    device: str | None = Field(None, description="'cpu', '0', 'cuda:0', 'mps'... None = auto.")
    tracker: Literal["bytetrack.yaml", "botsort.yaml"] = "bytetrack.yaml"
    max_fps: float = Field(15.0, ge=1, le=120, description="Upper bound on processed frames per second.")


class AlertSettings(BaseModel):
    webhook_url: str | None = None
    format: Literal["generic", "slack", "discord"] = "generic"
    cooldown: float = Field(60.0, ge=0, le=86400, description="Minimum seconds between alerts per camera.")
    public_url: str | None = Field(
        None, description="Dashboard address included in alert messages, e.g. https://ppe.example.com"
    )

    @field_validator("webhook_url", "public_url")
    @classmethod
    def _http_only(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        if not re.match(r"^https?://", v):
            raise ValueError("must start with http:// or https://")
        return v.rstrip("/")


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class CameraConfig(BaseModel):
    id: str = Field(..., description="Short slug, e.g. 'gate-north'.")
    name: str = Field(..., min_length=1, max_length=80)
    source: str = Field(..., min_length=1, description="Webcam index ('0'), file path, or rtsp/http URL.")
    enabled: bool = True
    autostart: bool = False
    loop: bool = Field(True, description="Restart video files when they end.")

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError("use 1-40 lowercase letters, digits, '-' or '_'")
        return v


def _default_cameras() -> list[CameraConfig]:
    return [CameraConfig(id="webcam", name="Webcam", source="0")]


class Settings(BaseModel):
    detection: DetectionSettings = Field(default_factory=DetectionSettings)
    compliance: ComplianceSettings = Field(default_factory=ComplianceSettings)
    alerts: AlertSettings = Field(default_factory=AlertSettings)
    cameras: list[CameraConfig] = Field(default_factory=_default_cameras)

    @field_validator("cameras")
    @classmethod
    def _unique_ids(cls, v: list[CameraConfig]) -> list[CameraConfig]:
        ids = [c.id for c in v]
        if len(ids) != len(set(ids)):
            raise ValueError("camera ids must be unique")
        return v


def default_data_dir() -> Path:
    return Path(os.environ.get("PPE_GUARD_DATA_DIR", "data")).resolve()


class SettingsStore:
    """Thread-safe load/save of :class:`Settings` as JSON."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._settings = self._load()

    def _load(self) -> Settings:
        if self.path.exists():
            try:
                return Settings.model_validate_json(self.path.read_text(encoding="utf-8"))
            except (ValidationError, json.JSONDecodeError) as e:
                backup = self.path.with_suffix(".invalid.json")
                self.path.replace(backup)
                print(f"[ppe-guard] settings file was invalid, moved to {backup.name}: {e}")
        return Settings()

    def get(self) -> Settings:
        with self._lock:
            return self._settings.model_copy(deep=True)

    def set(self, settings: Settings) -> Settings:
        with self._lock:
            self._settings = settings
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
            tmp.replace(self.path)
            return settings.model_copy(deep=True)
