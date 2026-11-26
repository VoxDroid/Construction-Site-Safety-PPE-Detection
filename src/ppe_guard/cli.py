"""``ppe-guard`` command line interface."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import sys
import time
from pathlib import Path

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".jfif", ".tif", ".tiff"}
REPO_DATA_YAML = Path(__file__).resolve().parents[2] / "training" / "data.yaml"
KAGGLE_DATASET = "snehilsanyal/construction-site-safety-image-dataset-roboflow"


def _data_yaml(arg: str | None) -> str:
    if arg:
        return arg
    if Path("training/data.yaml").exists():
        return "training/data.yaml"
    if REPO_DATA_YAML.exists():
        return str(REPO_DATA_YAML)
    sys.exit("error: pass --data path/to/data.yaml (training/data.yaml was not found)")


# ---------------------------------------------------------------- serve


def cmd_serve(a: argparse.Namespace) -> None:
    import uvicorn

    from ppe_guard.server import create_app

    if a.models_dir:
        os.environ["PPE_GUARD_MODELS_DIR"] = str(Path(a.models_dir).resolve())
    app = create_app(Path(a.data_dir) if a.data_dir else None, autostart=not a.no_autostart)
    if a.host not in ("127.0.0.1", "localhost"):
        print("warning: the dashboard has no login; only expose it on a trusted network "
              "or behind an authenticating reverse proxy.", file=sys.stderr)
    print(f"PPE Guard dashboard: http://{'localhost' if a.host in ('0.0.0.0', '127.0.0.1') else a.host}:{a.port}")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


# ---------------------------------------------------------------- predict


def _iter_images(src: Path) -> list[Path]:
    if src.is_dir():
        return sorted(p for p in src.iterdir() if p.suffix.lower() in IMAGE_EXT)
    return [src]


def _print_workers(label: str, result) -> None:
    if not result.workers:
        print(f"{label}: no workers detected")
        return
    bad = sum(1 for w in result.workers if w.state.value == "violation")
    print(f"{label}: {len(result.workers)} worker(s), {bad} non-compliant")
    for w in result.workers:
        items = ", ".join(f"{k.value}={v.value}" for k, v in w.items.items())
        print(f"  {w.key:>5}  {w.state.value:<10} {items}")


def cmd_predict(a: argparse.Namespace) -> None:
    import cv2

    from ppe_guard.camera import is_file_source, parse_source
    from ppe_guard.compliance import ComplianceEngine, PPEItem, evaluate_still
    from ppe_guard.config import ComplianceSettings, DetectionSettings, RequiredPPE
    from ppe_guard.detector import Detector, draw

    required_names = [r.strip() for r in a.require.split(",") if r.strip()]
    try:
        required = [PPEItem(r) for r in required_names]
    except ValueError:
        sys.exit(f"error: --require takes a comma list of {', '.join(i.value for i in PPEItem)}")
    det = Detector(DetectionSettings(weights=a.weights, confidence=a.conf, device=a.device, imgsz=a.imgsz))
    print(f"model: {det.weights_path}", file=sys.stderr)
    save_dir = Path(a.save) if a.save else None
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)

    src_path = Path(a.source)
    is_image = src_path.exists() and (src_path.is_dir() or src_path.suffix.lower() in IMAGE_EXT)
    if is_image:
        any_violation = False
        for p in _iter_images(src_path):
            frame = cv2.imread(str(p))
            if frame is None:
                print(f"{p}: could not read image", file=sys.stderr)
                continue
            dets = det(frame, track=False)
            res = evaluate_still(dets, required, strict=a.strict)
            any_violation |= res.violating
            _print_workers(p.name, res)
            out = draw(frame, res, dets, required)
            if save_dir:
                cv2.imwrite(str(save_dir / f"{p.stem}_ppe.jpg"), out)
            if a.show:
                cv2.imshow("PPE Guard", out)
                cv2.waitKey(0)
        if a.show:
            cv2.destroyAllWindows()
        sys.exit(1 if any_violation and a.fail_on_violation else 0)

    # video file, webcam index or stream URL
    cap = cv2.VideoCapture(parse_source(a.source))
    if not cap.isOpened():
        sys.exit(f"error: could not open source '{a.source}'")
    is_file = is_file_source(a.source)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    engine = ComplianceEngine(ComplianceSettings(
        required_ppe=RequiredPPE(**{i.value: i in required for i in PPEItem}),
        violation_delay=a.delay,
        treat_unseen_as_missing=a.strict,
    ))
    writer = None
    n, opened, t_start = 0, 0, time.time()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            # For files use the video clock, so results don't depend on processing speed.
            now = n / fps if is_file else time.time()
            dets = det(frame)
            res = engine.update(dets, now=now)
            for ev in res.events:
                v = ev.violation
                stamp = f"{now:8.2f}s" if is_file else time.strftime("%H:%M:%S")
                if ev.kind == "opened":
                    opened += 1
                    print(f"[{stamp}] violation opened  worker {v.worker_key}: missing "
                          f"{', '.join(sorted(i.value for i in v.missing))}")
                elif ev.kind == "closed":
                    print(f"[{stamp}] violation closed  worker {v.worker_key} after "
                          f"{(v.ended_at or now) - v.started_at:.1f}s")
            out = draw(frame, res, dets, required)
            if save_dir:
                if writer is None:
                    name = (src_path.stem if is_file else "stream") + "_ppe.mp4"
                    h, w = out.shape[:2]
                    writer = cv2.VideoWriter(str(save_dir / name), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
                writer.write(out)
            if a.show:
                cv2.imshow("PPE Guard (q to quit)", out)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            n += 1
    except KeyboardInterrupt:
        pass
    finally:
        for ev in engine.reset():
            print(f"violation closed  worker {ev.violation.worker_key} (end of stream)")
        cap.release()
        if writer:
            writer.release()
        if a.show:
            cv2.destroyAllWindows()
    dt = time.time() - t_start
    print(f"processed {n} frames in {dt:.1f}s ({n / dt if dt else 0:.1f} fps), {opened} violation(s)",
          file=sys.stderr)
    sys.exit(1 if opened and a.fail_on_violation else 0)


# ---------------------------------------------------------------- training


def cmd_train(a: argparse.Namespace) -> None:
    from ultralytics import YOLO

    model = YOLO(a.model)
    name = a.name or f"{Path(a.model).stem}-css"
    model.train(data=_data_yaml(a.data), epochs=a.epochs, batch=a.batch, imgsz=a.imgsz,
                patience=a.patience, seed=a.seed, deterministic=True, device=a.device,
                workers=a.workers, project=str(Path(a.project).resolve()), name=name,
                exist_ok=True, fraction=a.fraction)
    best = Path(a.project).resolve() / name / "weights" / "best.pt"
    print(f"\nbest weights: {best}\nevaluate with: ppe-guard val --weights {best} --split test")


def cmd_val(a: argparse.Namespace) -> None:
    from ultralytics import YOLO

    from ppe_guard.weights import resolve

    m = YOLO(str(resolve(a.weights)))
    r = m.val(data=_data_yaml(a.data), split=a.split, imgsz=a.imgsz, batch=a.batch,
              device=a.device, plots=False, verbose=True)
    print(f"\n{a.split}: precision={r.box.mp:.3f} recall={r.box.mr:.3f} "
          f"mAP50={r.box.map50:.3f} mAP50-95={r.box.map:.3f}")


def cmd_export(a: argparse.Namespace) -> None:
    from ultralytics import YOLO

    from ppe_guard.weights import resolve

    out = YOLO(str(resolve(a.weights))).export(format=a.format, imgsz=a.imgsz, half=a.half)
    print(f"exported: {out}")


# ---------------------------------------------------------------- dataset / weights


def cmd_dataset_download(a: argparse.Namespace) -> None:
    dest = Path(a.dest).resolve()
    if (dest / "train" / "images").exists() and not a.force:
        print(f"dataset already present at {dest} (use --force to replace)")
        return
    try:
        import kagglehub
    except ImportError:
        sys.exit('error: install the dataset extra first:  pip install "ppe-guard[dataset]"')
    root = Path(kagglehub.dataset_download(KAGGLE_DATASET))
    found = next((p.parent.parent for p in root.rglob("images") if p.parent.name == "train"), None)
    if found is None:
        sys.exit(f"error: could not find train/images inside {root}")
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(found, dest)
    for split in ("train", "valid", "test"):
        n = len(list((dest / split / "images").glob("*"))) if (dest / split).exists() else 0
        print(f"  {split:<5} {n} images")
    print(f"dataset ready at {dest}")


def cmd_weights(a: argparse.Namespace) -> None:
    from ppe_guard.weights import DEFAULT_MODEL, MODELS, download, models_dir

    if a.action == "list":
        for m in MODELS.values():
            p = models_dir() / m.filename
            mark = "downloaded" if p.exists() else "not downloaded"
            print(f"{m.alias:<14} {m.description:<48} [{mark}]{'  (default)' if m.alias == DEFAULT_MODEL else ''}")
        print(f"\nmodels folder: {models_dir()}")
        return
    names = a.names or [DEFAULT_MODEL]
    for n in names:
        if n not in MODELS:
            sys.exit(f"error: unknown model '{n}'. Choose from: {', '.join(MODELS)}")
        print(f"saved {download(n)}")


def cmd_doctor(a: argparse.Namespace) -> None:
    from ppe_guard import __version__
    from ppe_guard.weights import MODELS, models_dir

    print(f"ppe-guard   {__version__}")
    print(f"python      {platform.python_version()} ({platform.system()} {platform.machine()})")
    try:
        import torch

        print(f"torch       {torch.__version__}")
        if torch.cuda.is_available():
            for i in range(torch.cuda.device_count()):
                p = torch.cuda.get_device_properties(i)
                print(f"cuda:{i}      {p.name} ({p.total_memory / 2**30:.1f} GB)")
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            print("device      Apple MPS")
        else:
            print("device      CPU only (no CUDA GPU found)")
    except ImportError:
        print("torch       NOT INSTALLED")
    try:
        import ultralytics

        print(f"ultralytics {ultralytics.__version__}")
    except ImportError:
        print("ultralytics NOT INSTALLED")
    for m in MODELS.values():
        p = models_dir() / m.filename
        print(f"{m.alias:<11} {'ok' if p.exists() else 'missing (ppe-guard weights download ' + m.alias + ')'}")
    ds = Path("datasets/css/train/images")
    print(f"dataset     {'ok' if ds.exists() else 'missing (ppe-guard dataset download), only needed for training'}")


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    from ppe_guard import __version__

    p = argparse.ArgumentParser(prog="ppe-guard", description="PPE compliance monitoring for construction sites.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the web dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--data-dir", help="where settings, the database and snapshots live (default ./data)")
    s.add_argument("--models-dir", help="where model weights are stored (default ./models)")
    s.add_argument("--no-autostart", action="store_true", help="don't start cameras marked autostart")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("predict", help="check images, a folder, a video, a webcam or a stream")
    s.add_argument("source", help="image, folder, video file, webcam index (0) or rtsp/http URL")
    s.add_argument("--weights", default="auto")
    s.add_argument("--conf", type=float, default=0.4)
    s.add_argument("--imgsz", type=int, default=640)
    s.add_argument("--device", default=None)
    s.add_argument("--require", default="helmet,vest", help="comma list of helmet,vest,mask")
    s.add_argument("--delay", type=float, default=2.0, help="seconds before a video violation opens")
    s.add_argument("--strict", action="store_true", help="unseen required items count as missing")
    s.add_argument("--show", action="store_true", help="open a preview window")
    s.add_argument("--save", metavar="DIR", help="write annotated images/video to DIR")
    s.add_argument("--fail-on-violation", action="store_true", help="exit with status 1 if any violation")
    s.set_defaults(func=cmd_predict)

    s = sub.add_parser("train", help="fine-tune a YOLO model on the PPE dataset")
    s.add_argument("--model", default="yolo26s.pt", help="base weights, e.g. yolo26s.pt, yolo11s.pt, yolov8s.pt")
    s.add_argument("--data", help="dataset yaml (default training/data.yaml)")
    s.add_argument("--epochs", type=int, default=200)
    s.add_argument("--batch", type=int, default=16)
    s.add_argument("--imgsz", type=int, default=640)
    s.add_argument("--patience", type=int, default=50)
    s.add_argument("--seed", type=int, default=88)
    s.add_argument("--device", default=None)
    s.add_argument("--workers", type=int, default=8)
    s.add_argument("--fraction", type=float, default=1.0, help="share of training images to use (quick tests)")
    s.add_argument("--project", default="runs/detect")
    s.add_argument("--name")
    s.set_defaults(func=cmd_train)

    s = sub.add_parser("val", help="evaluate weights on the validation or test split")
    s.add_argument("--weights", default="auto")
    s.add_argument("--data")
    s.add_argument("--split", choices=["val", "test"], default="test")
    s.add_argument("--imgsz", type=int, default=640)
    s.add_argument("--batch", type=int, default=16)
    s.add_argument("--device", default=None)
    s.set_defaults(func=cmd_val)

    s = sub.add_parser("export", help="export weights to ONNX, TensorRT, OpenVINO, CoreML, TFLite...")
    s.add_argument("--weights", default="auto")
    s.add_argument("--format", default="onnx")
    s.add_argument("--imgsz", type=int, default=640)
    s.add_argument("--half", action="store_true")
    s.set_defaults(func=cmd_export)

    d = sub.add_parser("dataset", help="dataset tools").add_subparsers(dest="action", required=True)
    s = d.add_parser("download", help="download the Construction Site Safety dataset from Kaggle")
    s.add_argument("--dest", default="datasets/css")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_dataset_download)

    s = sub.add_parser("weights", help="list or download the trained PPE models")
    s.add_argument("action", choices=["list", "download"])
    s.add_argument("names", nargs="*")
    s.set_defaults(func=cmd_weights)

    s = sub.add_parser("doctor", help="check Python, GPU, models and dataset")
    s.set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
