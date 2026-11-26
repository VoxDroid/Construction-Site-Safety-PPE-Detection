"""Locating and downloading the trained PPE models."""

from __future__ import annotations

import os
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

RELEASE_URL = (
    "https://github.com/VoxDroid/ppe-guard/releases/download/v2.0.0"
)


@dataclass(frozen=True)
class ModelInfo:
    alias: str
    filename: str
    description: str


MODELS: dict[str, ModelInfo] = {
    m.alias: m
    for m in (
        ModelInfo("ppe-yolo26s", "ppe-yolo26s.pt", "YOLO26s, 200 epochs, test mAP50-95 0.574"),
        ModelInfo("ppe-yolov8s", "ppe-yolov8s.pt", "YOLOv8s, 200 epochs, test mAP50-95 0.556 (v1)"),
    )
}
DEFAULT_MODEL = "ppe-yolo26s"


def models_dir() -> Path:
    return Path(os.environ.get("PPE_GUARD_MODELS_DIR", "models")).resolve()


def download(alias: str, dest_dir: Path | None = None) -> Path:
    info = MODELS[alias]
    dest_dir = dest_dir or models_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / info.filename
    url = f"{RELEASE_URL}/{info.filename}"
    tmp = dest.with_suffix(".part")
    print(f"Downloading {alias} from {url}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    tmp.replace(dest)
    return dest


def resolve(spec: str = "auto", allow_download: bool = True) -> Path:
    """Turn a weights spec (path, alias or ``"auto"``) into a local file path."""
    if spec in ("", "auto"):
        # Prefer whichever bundled model is already on disk before downloading anything.
        for alias in [DEFAULT_MODEL, *MODELS]:
            p = models_dir() / MODELS[alias].filename
            if p.exists():
                return p
        spec = DEFAULT_MODEL
    if spec in MODELS:
        p = models_dir() / MODELS[spec].filename
        if p.exists():
            return p
        if not allow_download:
            raise FileNotFoundError(f"{p} not found; run `ppe-guard weights download {spec}`")
        return download(spec)
    p = Path(spec).expanduser()
    if p.exists():
        return p.resolve()
    raise FileNotFoundError(
        f"weights '{spec}' not found. Use a file path or one of: {', '.join(MODELS)}"
    )
