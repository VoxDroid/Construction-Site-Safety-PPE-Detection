# Contributing

Thanks for helping make construction sites safer. Bug reports, dataset ideas, docs fixes and code are all welcome.

## Development setup

```bash
git clone https://github.com/VoxDroid/ppe-guard.git
cd ppe-guard
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Pick the PyTorch build for your machine first (https://pytorch.org/get-started/locally/), e.g.
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128   # NVIDIA GPU
# pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu   # CPU only

pip install -e ".[dev,dataset]"
ppe-guard doctor
```

## Before opening a pull request

```bash
ruff check src tests     # lint
ruff format src tests    # optional formatting
pytest                   # tests run on CPU and need no model weights
```

- Keep pull requests focused: one fix or feature each.
- Add or update tests for behaviour changes. `tests/test_compliance.py` shows how to drive the
  compliance engine with synthetic detections and a fake clock.
- Update `README.md` and `CHANGELOG.md` when you change user-facing behaviour.
- Don't commit datasets, model weights, videos or anything under `data/`, `runs/` or `models/`.

## Where things live

| Path | What it is |
| --- | --- |
| `src/ppe_guard/compliance.py` | Per-worker PPE association, temporal smoothing and the violation lifecycle |
| `src/ppe_guard/detector.py` | YOLO + tracker wrapper and frame annotation |
| `src/ppe_guard/camera.py` | Per-camera capture/processing threads |
| `src/ppe_guard/storage.py` | SQLite violations database and snapshots |
| `src/ppe_guard/alerts.py` | Webhook alerts |
| `src/ppe_guard/server/` | FastAPI app and the no-build dashboard (`static/`) |
| `src/ppe_guard/cli.py` | The `ppe-guard` command |
| `training/` | Dataset config, the original notebook and published training results |
| `docs/` | GitHub Pages site and README images |

## Training improvements

If you train a better model, open an issue or PR with:

- the exact `ppe-guard train ...` command (or notebook changes),
- `ppe-guard val --split val` **and** `--split test` output,
- `results.csv` and the confusion matrix from the run.

Please report test-split numbers. Validation numbers alone flatter a model that was selected on them.
