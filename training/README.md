# Training

| File | Purpose |
| --- | --- |
| `data.yaml` | Dataset config. Paths are relative to this file and point to `../datasets/css`. |
| `yolov8-finetuning-for-ppe-detection.ipynb` | The original exploration and training notebook (produced the v1 YOLOv8s model). |
| `results/yolov8s/` | Curves, confusion matrices, sample predictions and `results.csv` for the v1 YOLOv8s run. |
| `results/yolo26s/` | The same for the YOLO26s run. |

## Reproduce

```bash
pip install -e ".[dataset]"
ppe-guard dataset download                      # → datasets/css (2,605 / 114 / 82 images)
ppe-guard train --model yolo26s.pt --epochs 200 --batch 16 --imgsz 640 --seed 88
ppe-guard val --weights runs/detect/yolo26s-css/weights/best.pt --split val
ppe-guard val --weights runs/detect/yolo26s-css/weights/best.pt --split test
```

`ppe-guard train` is a thin wrapper around `ultralytics` `model.train(...)`. Any extra tuning can be
done directly with the Ultralytics API or CLI using `training/data.yaml`.

## Dataset

[Construction Site Safety](https://universe.roboflow.com/roboflow-universe-projects/construction-site-safety),
Roboflow export v28, licensed CC BY 4.0. Images were resized (stretched) to 640×640 by Roboflow, and
the training split contains Roboflow's augmented copies (flip, crop, ±12° rotation) of each source
image.

| id | class | id | class |
| --- | --- | --- | --- |
| 0 | Hardhat | 5 | Person |
| 1 | Mask | 6 | Safety Cone |
| 2 | NO-Hardhat | 7 | Safety Vest |
| 3 | NO-Mask | 8 | machinery |
| 4 | NO-Safety Vest | 9 | vehicle |

## Notes on the numbers

- `results.csv` metrics are on the **validation** split, which Ultralytics also uses to pick
  `best.pt`. That makes them slightly optimistic.
- The README reports `ppe-guard val` results for both splits. Report the **test** split when
  comparing models.
- With `optimizer=auto` Ultralytics chooses the optimizer and learning rate itself, so `lr0` in
  `args.yaml` is not the learning rate that was actually used. The training log prints the real one.
