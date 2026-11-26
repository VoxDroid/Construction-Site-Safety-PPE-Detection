<div align="center">

# PPE Guard

**Real-time PPE compliance monitoring for construction sites.**
It spots workers without hard hats or hi-vis vests, judges each worker separately, keeps snapshot evidence and sends alerts.

[![CI](https://github.com/VoxDroid/ppe-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/VoxDroid/ppe-guard/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue)
![Models](https://img.shields.io/badge/models-YOLO26s%20%7C%20YOLOv8s-f5b700)

[Website](https://voxdroid.github.io/ppe-guard/) ·
[Quick start](#quick-start) ·
[Dashboard](#the-dashboard) ·
[Model results](#model-results) ·
[Train your own](#training)

<img src="docs/assets/demo.gif" alt="Workers on a construction site with detection boxes around hard hats, vests and people" width="720">

</div>

---

## Why

Construction is one of the most dangerous industries. In the US, construction and extraction workers
accounted for **976 of the 4,764 fatal work injuries in 2020**
([BLS Census of Fatal Occupational Injuries](https://www.bls.gov/news.release/archives/cfoi_12162021.htm)).
Hard hats and high-visibility clothing are basic controls, but checking them all day across a busy
site is hard to do by hand.

PPE Guard watches existing cameras and flags workers who are missing required equipment. Site
supervisors get a short, reviewable list of incidents with photos, not a wall of raw detections.

> [!IMPORTANT]
> PPE Guard is a decision-support tool, not a certified safety system. It will miss some
> violations and raise some false alarms. Keep a human in the loop and follow your local rules on
> workplace video monitoring and worker privacy.

## Features

- **Per-worker verdicts.** Hard hats, vests and masks are matched to the person wearing them,
  using overlap plus a body-region check (head vs. torso). One worker's helmet can't make the
  person next to them look compliant.
- **Tracking with smoothing.** ByteTrack follows each person across frames. A violation opens
  only after an item has looked missing for most of a configurable window (2 s by default), so a
  single bad frame doesn't trigger an alert. It closes once the worker is compliant again or
  leaves the frame.
- **Any camera.** Webcams, video files and RTSP/HTTP IP cameras, several at once, with automatic
  reconnect for streams.
- **Evidence and review.** Every violation is stored with timestamped snapshots, can be marked
  reviewed with a note, and can be exported to CSV for audits.
- **Alerts.** Webhooks in generic JSON, Slack or Discord format, with a per-camera cooldown.
- **Configurable rules.** Choose the required PPE per site (helmet, vest, mask), plus
  timing, confidence and strict mode (anything not visible counts as missing).
- **Runs anywhere.** One `pip install`, or Docker on CPU or an NVIDIA GPU. A REST and WebSocket
  API lets you plug it into other systems.
- **Reproducible training.** The dataset download, training, evaluation and export are all one
  command each.

## How it works

```mermaid
flowchart LR
    A[Camera / video / RTSP] --> B[YOLO detector<br/>10 classes]
    B --> C[ByteTrack<br/>person IDs]
    C --> D[Assign PPE to person<br/>overlap + body region]
    D --> E[Per-worker history<br/>smoothing over N seconds]
    E -->|missing long enough| F[Violation opened]
    F --> G[(SQLite + snapshots)]
    F --> H[Webhook alert]
    F --> I[Live dashboard]
```

The detector recognises 10 classes: `Hardhat`, `NO-Hardhat`, `Safety Vest`, `NO-Safety Vest`,
`Mask`, `NO-Mask`, `Person`, `Safety Cone`, `machinery` and `vehicle`. For every tracked person,
each required item is **worn**, **missing** or **unknown** (not visible). A missing item has to
persist for the violation delay before it counts.

## Quick start

**Requirements:** Python 3.10 to 3.13. An NVIDIA GPU is recommended for live video. A CPU works
for images and is usable on a single low-resolution stream.

```bash
git clone https://github.com/VoxDroid/ppe-guard.git
cd ppe-guard
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# 1. Install PyTorch for your hardware (see https://pytorch.org/get-started/locally/)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128   # NVIDIA GPU
# pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu   # CPU only

# 2. Install PPE Guard
pip install -e .

# 3. Check your setup and fetch the trained model
ppe-guard doctor
ppe-guard weights download
```

### Check an image, a folder or a video

```bash
ppe-guard predict site_photo.jpg --save out/
ppe-guard predict photos/ --require helmet,vest,mask
ppe-guard predict shift_recording.mp4 --show --save out/
ppe-guard predict rtsp://user:pass@192.168.1.20/stream1 --show
```

```text
site_photo.jpg: 3 worker(s), 1 non-compliant
     p0  compliant  helmet=worn, vest=worn, mask=unknown
     p1  violation  helmet=missing, vest=worn, mask=unknown
     p2  compliant  helmet=worn, vest=worn, mask=unknown
```

Add `--fail-on-violation` to exit with status 1 when something is found, which is handy for scripts
and batch jobs.

### Run the dashboard

```bash
ppe-guard serve                      # http://localhost:8000
```

Open **Settings** and add your cameras (webcam index `0`, a video file path or an `rtsp://` URL),
choose the required PPE, then press **Start** on the **Live** page.

### Docker

```bash
docker compose up -d --build         # CPU image, http://localhost:8000
```

For an NVIDIA GPU, build with `--build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu128` and
uncomment the `deploy` block in [`docker-compose.yml`](docker-compose.yml). Settings, the database
and snapshots are kept in `./data`, and model weights in `./models`.

> [!WARNING]
> The dashboard has no login. It binds to `localhost` by default. If you expose it on a network,
> put it behind a reverse proxy with authentication (for example Caddy, nginx or Tailscale).

## The dashboard

| Live | Violations | Settings |
| --- | --- | --- |
| ![Live view](docs/assets/dashboard-live.png) | ![Violations](docs/assets/dashboard-violations.png) | ![Settings](docs/assets/dashboard-settings.png) |

- **Live**: an annotated stream per camera with every worker's ID and PPE status, and a live
  event feed.
- **Violations**: filter by camera, missing item, period, status and review state. Open an
  entry to see its snapshots, add a note, mark it reviewed or delete it. **Export CSV** downloads
  the filtered list.
- **Settings**: required PPE, timing, detection, alerts and cameras. Changes apply to running
  cameras within a second, except the model, which applies on the next camera start.

### Settings reference

| Setting | Default | Meaning |
| --- | --- | --- |
| Required PPE | helmet, vest | Items every worker must wear |
| `violation_delay` | 2 s | How long an item must look missing before a violation opens |
| `clear_after` | 3 s | How long a worker must be compliant before the violation closes |
| `lost_timeout` | 3 s | How long a worker may be out of view before being forgotten |
| `treat_unseen_as_missing` | off | Strict mode: required items that can't be seen count as missing |
| `min_overlap` | 0.5 | Share of a PPE box that must lie inside a person box to be assigned |
| `snapshot_interval` / `max_snapshots` | 10 s / 5 | Evidence frames saved per violation |
| `confidence` / `iou` | 0.40 / 0.50 | Detector thresholds |
| `max_fps` | 15 | Processing cap per camera |
| `webhook_url`, `format`, `cooldown` | none, generic, 60 s | Alerts |

Settings are stored in `data/settings.json`. Change the folder with `--data-dir` or
`PPE_GUARD_DATA_DIR`.

### API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health`, `/api/stats` | Health and summary counts |
| `GET` `PUT` | `/api/settings` | Read or replace settings (validated) |
| `GET` `POST` | `/api/cameras` | List or add cameras |
| `PUT` `DELETE` | `/api/cameras/{id}` | Edit or remove a camera |
| `POST` | `/api/cameras/{id}/start`, `/stop` | Control processing |
| `GET` | `/api/cameras/{id}/stream` | Annotated MJPEG stream |
| `GET` | `/api/violations` | Filter, sort and page violations |
| `GET` | `/api/violations/export.csv` | CSV export (same filters) |
| `GET` `DELETE` | `/api/violations/{id}` | Details with snapshots, or delete |
| `POST` | `/api/violations/{id}/ack` | Mark reviewed, with an optional note |
| `WS` | `/ws` | Live frame summaries and violation events |

Interactive API docs are served at `/docs`.

## Model results

Both models were fine-tuned from COCO weights on the
[Construction Site Safety dataset](https://universe.roboflow.com/roboflow-universe-projects/construction-site-safety)
(2,605 train images including Roboflow's augmented copies, 114 validation and 82 test images) at 640 px, batch 16, for 200 epochs, on an
RTX 5060 Ti (16 GB). They were evaluated with `ppe-guard val` at the same settings. The **test**
split was never used for training or model selection.

| Model | Split | Precision | Recall | mAP50 | mAP50-95 | GPU inference |
| --- | --- | --- | --- | --- | --- | --- |
| **ppe-yolo26s** (default) | val | 0.933 | 0.834 | 0.871 | 0.620 | 6.2 ms |
| | test | 0.940 | 0.765 | 0.822 | **0.574** | |
| ppe-yolov8s (v1) | val | 0.950 | 0.800 | 0.864 | 0.604 | 5.8 ms |
| | test | 0.923 | 0.782 | 0.814 | 0.556 | |

YOLO26s is the default: it has the best mAP50-95 on the test split (+0.018) and higher precision, and it is a smaller file. YOLOv8s has slightly higher test recall (0.782 against 0.765), so it can be worth trying if missed detections matter more to you than false alarms (`ppe-guard weights download ppe-yolov8s`, then set **Model** to `ppe-yolov8s` under **Settings → Detection** and restart the camera). The test split is small (82 images), so treat differences of a point or two as close to noise. GPU inference is per image at batch 16 on the RTX 5060 Ti, excluding pre- and post-processing.

**Where the models struggle** (test split, both models): `NO-Hardhat` has the lowest recall of
the PPE classes, so a bare head is harder to confirm than a helmet. Small or distant `Safety Cone`s
are often missed. Low light, unusual helmet colours and heavy occlusion all reduce accuracy. Use
the confidence threshold and `violation_delay` to trade missed violations against false alarms,
and check a sample of footage from your own site before relying on it.

Training curves, confusion matrices and per-epoch metrics are in
[`training/results/`](training/results/).

## Training

```bash
pip install -e ".[dataset]"
ppe-guard dataset download                       # Kaggle mirror of the Roboflow dataset → datasets/css
ppe-guard train --model yolo26s.pt --epochs 200  # any Ultralytics detector: yolo11s.pt, yolov8m.pt, ...
ppe-guard val --weights runs/detect/yolo26s-css/weights/best.pt --split test
ppe-guard export --weights runs/detect/yolo26s-css/weights/best.pt --format onnx   # or engine, openvino, tflite...
```

To use your own model in the dashboard, set **Settings → Detection → Model** to the `.pt` path. A
model trained on another PPE dataset works too, as long as its class names include a person class
and `hardhat`/`helmet`, `vest` or `mask` style names (with `NO-` variants for missing items).

The original exploratory notebook, which produced the v1 YOLOv8s model, is in
[`training/yolov8-finetuning-for-ppe-detection.ipynb`](training/yolov8-finetuning-for-ppe-detection.ipynb).

## Project layout

```text
src/ppe_guard/
  compliance.py   per-worker PPE association, smoothing and violation lifecycle
  detector.py     YOLO + tracker wrapper and frame drawing
  camera.py       one processing thread per camera
  storage.py      SQLite violations database and snapshots
  alerts.py       webhook alerts
  server/         FastAPI app and the dashboard (plain HTML/JS/CSS, no build step)
  cli.py          the ppe-guard command
training/         data.yaml, the original notebook, published results
tests/            unit and API tests (CPU only, no weights needed)
docs/             GitHub Pages site and README images
```

## Roadmap

- Zones: only enforce PPE inside drawn areas, with different rules per zone
- More PPE: gloves, safety glasses and harnesses (needs more labelled data)
- Optional login and roles for the dashboard
- Daily and weekly PDF compliance summaries
- Edge deployment guides (Jetson, Raspberry Pi with Hailo, OpenVINO)

Ideas and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Acknowledgements and licences

- Dataset: [Construction Site Safety](https://universe.roboflow.com/roboflow-universe-projects/construction-site-safety)
  (export v28) from Roboflow Universe, licensed **CC BY 4.0**, mirrored on
  [Kaggle](https://www.kaggle.com/datasets/snehilsanyal/construction-site-safety-image-dataset-roboflow).
  Its images come from several public Roboflow projects and YouTube videos (see the dataset README).
- Detection and tracking: [Ultralytics](https://github.com/ultralytics/ultralytics) (YOLOv8,
  YOLO26, ByteTrack integration).
- The training notebook builds on community Kaggle work on YOLOv8 PPE fine-tuning.

PPE Guard's own code is released under the [MIT License](LICENSE). Ultralytics, and therefore
models trained and run with it, are licensed under **AGPL-3.0** by Ultralytics. Commercial or
closed-source use may need an [Ultralytics Enterprise licence](https://www.ultralytics.com/license).
Check your obligations before deploying.

## Support

If this project helps you, you can support it through
[Ko-fi](https://ko-fi.com/izeno), [Buy Me a Coffee](https://buymeacoffee.com/izeno),
[Liberapay](https://liberapay.com/izeno) or [Open Collective](https://opencollective.com/voxdroid).
