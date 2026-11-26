# Contributing

Thanks for helping make construction sites safer. Bug reports, feedback from real sites, dataset
ideas, docs fixes and code are all welcome.

## Development setup

```bash
git clone https://github.com/VoxDroid/ppe-guard.git
cd ppe-guard
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Pick the PyTorch build for your machine first (https://pytorch.org/get-started/locally/), e.g.
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128   # NVIDIA GPU
# pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu   # CPU only

pip install -e ".[dev,e2e,docs]"
playwright install chromium      # for the browser tests
ppe-guard doctor
```

Run the dashboard against a scratch data folder while you work:

```bash
ppe-guard serve --data-dir data-dev
```

The dashboard is plain HTML, CSS and JavaScript modules in `src/ppe_guard/server/static/`, with
no build step: edit and reload.

## Before opening a pull request

```bash
ruff check src tests scripts          # lint
pytest --ignore=tests/e2e             # unit and API tests: CPU only, no model weights needed
pytest tests/e2e                      # browser tests (Playwright + Chromium)
python scripts/gen_reference.py       # if you changed CLI options or settings
mkdocs serve                          # preview the docs at http://127.0.0.1:8000
```

- Keep pull requests focused: one fix or feature each.
- Add or update tests for behaviour changes. `tests/test_compliance.py` and `tests/test_zones.py`
  show how to drive the compliance engine with synthetic detections and a fake clock;
  `tests/helpers.py` has a fake detector and a synthetic video maker for end-to-end tests.
- Update the docs in `docs/` and `CHANGELOG.md` when you change user-facing behaviour.
  `docs/reference/cli.md` and `docs/reference/configuration.md` are generated; CI fails if they
  are stale.
- Don't commit datasets, model weights, videos or anything under `data/`, `runs/` or `models/`.

## Where things live

| Path | What it is |
| --- | --- |
| `src/ppe_guard/compliance.py` | Per-worker PPE association, zones, temporal smoothing and the violation lifecycle |
| `src/ppe_guard/detector.py` | YOLO + tracker wrapper and frame annotation |
| `src/ppe_guard/camera.py` | Per-camera capture and processing threads, snapshots and clips |
| `src/ppe_guard/storage.py` | SQLite: violations, snapshots, clips, worker-time and analytics |
| `src/ppe_guard/analysis.py` | Uploaded photo and video analysis |
| `src/ppe_guard/auth.py` | Password, sessions and API tokens |
| `src/ppe_guard/video.py` | Video writing with codec fallback, clip recording |
| `src/ppe_guard/alerts.py` | Webhook alerts |
| `src/ppe_guard/server/` | FastAPI app, routes and the dashboard (`static/`) |
| `src/ppe_guard/cli.py` | The `ppe-guard` command |
| `docs/` | The MkDocs site, including the browser demo in `docs/demo/` |
| `training/` | Dataset config, the original notebook and published training results |
| `scripts/` | `gen_reference.py` (docs) and `stress.py` (load test) |

## Training improvements

If you train a better model, open an issue or PR with:

- the exact `ppe-guard train ...` command (or notebook changes),
- `ppe-guard val --split val` **and** `--split test` output,
- `results.csv` and the confusion matrix from the run.

Please report test-split numbers. Validation numbers alone flatter a model that was selected on
them.

## Load testing

`scripts/stress.py` starts a server with many cameras, a large database, API clients, stream
viewers and WebSocket clients, and reports frame rates, latencies and memory:

```bash
python scripts/stress.py --videos "samples/*.mp4" --cameras 8 --duration 180
```
