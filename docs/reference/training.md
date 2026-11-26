# Training

You can reproduce the published models, try another YOLO architecture, or fine-tune on your own
site's footage. Training needs a clone of the repository and, realistically, an NVIDIA GPU.

## Reproduce the published model

```bash
git clone https://github.com/VoxDroid/ppe-guard.git && cd ppe-guard
pip install -e ".[dataset]"
ppe-guard dataset download    # → datasets/css: 2,605 train, 114 val, 82 test images
ppe-guard train --model yolo26s.pt --epochs 200 --batch 16 --imgsz 640 --seed 88
ppe-guard val --weights runs/detect/yolo26s-css/weights/best.pt --split val
ppe-guard val --weights runs/detect/yolo26s-css/weights/best.pt --split test
```

`ppe-guard dataset download` fetches the dataset from its
[Kaggle mirror](https://www.kaggle.com/datasets/snehilsanyal/construction-site-safety-image-dataset-roboflow)
without an account. Training YOLO26s for 200 epochs took about 2 hours 10 minutes on an RTX 5060
Ti.

`ppe-guard train` is a thin wrapper around Ultralytics' `model.train()` with
`training/data.yaml`. Any Ultralytics base model works: `yolo26n.pt` (faster, less accurate),
`yolo26m.pt` (slower, more accurate), `yolo11s.pt`, `yolov8s.pt`… See
`ppe-guard train --help` and the [CLI reference](cli.md#train).

The curves, confusion matrices and per-epoch metrics of the published runs are in
[`training/results/`](https://github.com/VoxDroid/ppe-guard/tree/main/training/results).

## Fine-tune on your own site

The most effective improvement for a particular site is a few hundred labelled frames from its
own cameras.

1. **Collect frames.** Save frames from your cameras at different times of day, for example one
   every 30 seconds with `ffmpeg -i rtsp://... -vf fps=1/30 frames/%05d.jpg`. Prefer variety over
   volume: different people, distances, lighting and weather.
2. **Label them** with the same 10 classes, in YOLO format. Free tools such as
   [Label Studio](https://labelstud.io/) or [CVAT](https://www.cvat.ai/) export it, as does
   Roboflow. Most of them can pre-label images with a model, which you then correct.
3. **Combine** your images with the original dataset (keep the original splits), and write a
   `data.yaml` that points at both.
4. **Train from the published weights** with fewer epochs:

    ```bash
    ppe-guard train --model models/ppe-yolo26s.pt --data my-data.yaml --epochs 60 --name my-site
    ```

5. **Evaluate on images from your site that were not used for training**, and compare with the
   published model on the same images before switching.

## Export

For other runtimes (TensorRT on Jetson, OpenVINO on Intel, CoreML, TFLite…):

```bash
ppe-guard export --weights models/ppe-yolo26s.pt --format onnx
ppe-guard export --weights models/ppe-yolo26s.pt --format engine --half   # TensorRT, on the target GPU
```

The dashboard itself loads `.pt` files and anything else Ultralytics can load, including `.onnx`
and `.engine`.

## Notes on the numbers

- `results.csv` metrics are on the **validation** split, which Ultralytics also uses to pick
  `best.pt`, so they are slightly optimistic. Compare models on the **test** split.
- With `optimizer=auto`, Ultralytics chooses the optimizer and learning rate itself, so `lr0` in
  `args.yaml` is not the learning rate that was used. The training log shows the real one.
- The original exploration notebook that produced the v1 model is
  `training/yolov8-finetuning-for-ppe-detection.ipynb`.
