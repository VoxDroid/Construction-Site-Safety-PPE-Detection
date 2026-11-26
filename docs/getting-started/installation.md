# Installation

PPE Guard is a Python package. It needs **Python 3.10 or newer** and runs on Windows, Linux and
macOS. A GPU is optional: a desktop CPU handles two or three cameras at about 5 fps each; an
NVIDIA GPU handles four to eight cameras at 6-12 fps each (see [Performance](../deployment/performance.md)).

!!! tip "Just want to see it?"
    The [live demo](https://voxdroid.github.io/ppe-guard/demo/) runs the model in your browser
    with nothing to install.

## 1. Install PyTorch

Install the PyTorch build for your hardware first. The command depends on your machine; the
[PyTorch site](https://pytorch.org/get-started/locally/) lists them all. Common ones:

=== "NVIDIA GPU"

    ```bash
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
    ```

=== "CPU only"

    ```bash
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
    ```

=== "Apple silicon"

    ```bash
    pip install torch torchvision
    ```

If you skip this step, `pip` installs the default PyTorch build for your platform, which on
Windows and Linux may be CPU only or a large CUDA download.

## 2. Install PPE Guard

=== "From GitHub"

    ```bash
    pip install "git+https://github.com/VoxDroid/ppe-guard.git"
    ```

=== "From a clone (for development)"

    ```bash
    git clone https://github.com/VoxDroid/ppe-guard.git
    cd ppe-guard
    python -m venv .venv
    source .venv/bin/activate        # Windows: .venv\Scripts\activate
    pip install -e ".[dev]"
    ```

We recommend a virtual environment so PPE Guard's dependencies don't clash with other projects.

## 3. Check the setup

```console
$ ppe-guard doctor
ppe-guard   2.0.1
python      3.12.10 (Windows AMD64)
torch       2.11.0+cu128
cuda:0      NVIDIA GeForce RTX 5060 Ti (15.9 GB)
ultralytics 8.4.219
ppe-yolo26s missing (ppe-guard weights download ppe-yolo26s)
ppe-yolov8s missing (ppe-guard weights download ppe-yolov8s)
dataset     missing (ppe-guard dataset download), only needed for training
```

The models download automatically the first time they are needed. To fetch them ahead of time
(for example on a machine that will be offline later):

```bash
ppe-guard weights download             # the default model, ppe-yolo26s
ppe-guard weights download ppe-yolov8s # the v1 model
```

Weights are saved to `./models`, or to the folder in `PPE_GUARD_MODELS_DIR`.

## Optional extras

| Extra | Install | For |
| --- | --- | --- |
| `dataset` | `pip install "ppe-guard[dataset]"` | `ppe-guard dataset download` (training) |
| `dev` | `pip install -e ".[dev]"` | tests and linting |
| `e2e` | `pip install -e ".[e2e]"` then `playwright install chromium` | browser tests of the dashboard |
| `docs` | `pip install -e ".[docs]"` | building this documentation |

## Docker

Prefer containers? See [Docker](../deployment/docker.md).

## Next

Continue with the [Quickstart](quickstart.md).
