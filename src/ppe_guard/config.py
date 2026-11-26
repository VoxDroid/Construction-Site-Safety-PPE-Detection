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


def _slug(v: str) -> str:
    if not _ID_RE.match(v):
        raise ValueError("use 1-40 lowercase letters, digits, '-' or '_'")
    return v


class Zone(BaseModel):
    """A polygon on a camera image, in coordinates normalised to 0-1 (x right, y down).

    * ``ppe`` zones: PPE is only checked for workers standing inside one. ``required_ppe``
      overrides the global rule inside the zone.
    * ``restricted`` zones: anyone who stays inside raises a zone violation.

    A worker's position is the bottom centre of their box (their feet).
    """

    id: str = Field(..., description="Short slug, unique per camera.")
    name: str = Field(..., min_length=1, max_length=60)
    kind: Literal["ppe", "restricted"] = "ppe"
    points: list[tuple[float, float]] = Field(..., min_length=3, max_length=64)
    required_ppe: RequiredPPE | None = Field(
        None, description="PPE zones only: items required inside this zone (default: global rule)."
    )
    enabled: bool = True

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        return _slug(v)

    @field_validator("points")
    @classmethod
    def _in_unit_square(cls, v: list[tuple[float, float]]) -> list[tuple[float, float]]:
        out = []
        for x, y in v:
            if not (-0.01 <= x <= 1.01 and -0.01 <= y <= 1.01):
                raise ValueError("zone points must be normalised to the 0-1 range")
            out.append((round(min(max(x, 0.0), 1.0), 4), round(min(max(y, 0.0), 1.0), 4)))
        return out


class CameraConfig(BaseModel):
    id: str = Field(..., description="Short slug, e.g. 'gate-north'.")
    name: str = Field(..., min_length=1, max_length=80)
    source: str = Field(..., min_length=1, description="Webcam index ('0'), file path, or rtsp/http URL.")
    enabled: bool = True
    autostart: bool = False
    loop: bool = Field(True, description="Restart video files when they end.")
    zones: list[Zone] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        return _slug(v)

    @field_validator("zones")
    @classmethod
    def _unique_zone_ids(cls, v: list[Zone]) -> list[Zone]:
        ids = [z.id for z in v]
        if len(ids) != len(set(ids)):
            raise ValueError("zone ids must be unique per camera")
        return v


class GeneralSettings(BaseModel):
    site_name: str = Field("My site", min_length=1, max_length=80,
                           description="Shown in the dashboard, reports and alerts.")


class RecordingSettings(BaseModel):
    clips: bool = Field(True, description="Save a short video clip with every violation.")
    pre_seconds: float = Field(4.0, ge=0, le=30, description="Seconds of video kept from before a violation.")
    max_seconds: float = Field(20.0, ge=2, le=120, description="Longest clip, including the pre-roll.")
    fps: float = Field(8.0, ge=1, le=30, description="Frame rate of saved clips.")
    max_width: int = Field(800, ge=320, le=1920, description="Clips are scaled down to this width.")


class StorageSettings(BaseModel):
    retention_days: int = Field(
        0, ge=0, le=3650,
        description="Delete violations, snapshots and clips older than this. 0 keeps everything.",
    )


class AnalysisSettings(BaseModel):
    max_upload_mb: int = Field(500, ge=1, le=10_000, description="Largest file accepted by Analyze.")
    keep_jobs: int = Field(20, ge=1, le=500, description="Finished analysis jobs to keep on disk.")


def _default_cameras() -> list[CameraConfig]:
    return [CameraConfig(id="webcam", name="Webcam", source="0")]


class Settings(BaseModel):
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    detection: DetectionSettings = Field(default_factory=DetectionSettings)
    compliance: ComplianceSettings = Field(default_factory=ComplianceSettings)
    alerts: AlertSettings = Field(default_factory=AlertSettings)
    recording: RecordingSettings = Field(default_factory=RecordingSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
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
