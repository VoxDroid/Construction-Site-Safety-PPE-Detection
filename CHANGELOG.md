# Changelog

## 2.0.0

A rewrite that turns the training project into a usable monitoring tool.

### Added
- **`ppe-guard` Python package and CLI**: `serve`, `predict`, `train`, `val`, `export`,
  `dataset download`, `weights list/download`, `doctor`.
- **Per-worker compliance.** PPE boxes are matched to the person wearing them using
  overlap plus body-region checks, and people are followed across frames with ByteTrack.
  v1 judged the whole frame, so one worker's hard hat could make another worker look compliant.
- **Temporal smoothing.** A violation opens only after an item has looked missing for most of
  `violation_delay` seconds. It closes after `clear_after` seconds of compliance or when the
  worker leaves the frame.
- **Strict mode** (`treat_unseen_as_missing`) for sites that require every item to be visible.
- **Any video source**: webcams, video files (optionally looped) and RTSP/HTTP streams, with
  several cameras at once and automatic reconnect for streams.
- **New FastAPI dashboard**: live MJPEG views with per-worker status, a live event feed,
  violation review with snapshots, notes, filters and pagination, settings and camera
  management, and a WebSocket live feed.
- **Alerts**: webhooks in generic JSON, Slack or Discord format, with a per-camera cooldown.
- **CSV export** of violations for audits.
- **YOLO26s model** (new default, test mAP50-95 0.574 against 0.556 for v1) trained on the same dataset as the v1 YOLOv8s model. Both models are
  evaluated on the held-out test split.
- Docker image and compose file (CPU or NVIDIA GPU), GitHub Actions CI, GitHub Pages site,
  issue templates and a contributor guide.

### Fixed
- Multi-person mode never flagged missing vests (it looked for `no-vest`, but the class is
  `NO-Safety Vest`).
- SQL injection through the `sort` query parameter.
- `/download_snapshot` could read any file on disk via absolute paths.
- The Flask app ran with `debug=True` and a hard-coded secret key.
- The dashboard loaded the 80-epoch weights while the docs described the 200-epoch model.
- Violations left "open" after a crash are closed on the next start.

### Changed
- The dataset, sample videos and model weights are no longer stored in the repository.
  Download them with `ppe-guard dataset download` and `ppe-guard weights download`.
- The README was rewritten: metrics are re-measured, including test-split results, and claims
  about features that did not exist were removed.
- `Model-Training/`, `Model-Deployment/` and `Model-Testing/` were replaced by `src/ppe_guard/`
  and `training/`.

## 1.0.0

- YOLOv8s fine-tuned on the Construction Site Safety dataset, a training notebook and a Flask
  dashboard with webcam monitoring.
