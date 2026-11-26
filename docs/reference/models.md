# Models and results

PPE Guard ships two models, both fine-tuned from COCO-pretrained weights on the
[Construction Site Safety dataset](https://universe.roboflow.com/roboflow-universe-projects/construction-site-safety)
(Roboflow export v28, CC BY 4.0) at 640 px, batch 16, for 200 epochs, on one RTX 5060 Ti.

| Name | Base | File | Notes |
| --- | --- | --- | --- |
| `ppe-yolo26s` | YOLO26s | 20 MB | **Default.** NMS-free head. |
| `ppe-yolov8s` | YOLOv8s | 22 MB | The v1 model, kept for comparison. |

Both download from the [v2.0.0 release](https://github.com/VoxDroid/ppe-guard/releases/tag/v2.0.0)
on first use (`ppe-guard weights download` to fetch them ahead of time). The release also has an
ONNX export of `ppe-yolo26s`, which the [browser demo](https://voxdroid.github.io/ppe-guard/demo/)
uses.

## Results

Evaluated with `ppe-guard val` at the training settings. The **test** split (82 images) was never
used for training or for picking the best epoch, so it is the fair comparison.

| Model | Split | Precision | Recall | mAP50 | mAP50-95 | GPU inference |
| --- | --- | --- | --- | --- | --- | --- |
| **ppe-yolo26s** | val | 0.933 | 0.834 | 0.871 | 0.620 | 6.2 ms |
| | test | 0.940 | 0.765 | 0.822 | **0.574** | |
| ppe-yolov8s | val | 0.950 | 0.800 | 0.864 | 0.604 | 5.8 ms |
| | test | 0.923 | 0.782 | 0.814 | 0.556 | |

GPU inference is per image at batch 16 on the RTX 5060 Ti, without pre- and post-processing.

YOLO26s has the better mAP50-95 and precision; YOLOv8s finds slightly more objects (recall). The
test split is small, so differences of a point or two are close to noise.

### Per class (test split, ppe-yolo26s)

| Class | Precision | Recall | mAP50 | mAP50-95 |
| --- | --- | --- | --- | --- |
| Hardhat | 0.990 | 0.901 | 0.963 | 0.677 |
| Mask | 0.963 | 0.750 | 0.780 | 0.633 |
| NO-Hardhat | 0.843 | 0.655 | 0.636 | 0.392 |
| NO-Mask | 0.928 | 0.797 | 0.873 | 0.468 |
| NO-Safety Vest | 0.962 | 0.767 | 0.851 | 0.603 |
| Person | 0.942 | 0.836 | 0.905 | 0.666 |
| Safety Cone | 0.864 | 0.500 | 0.586 | 0.288 |
| Safety Vest | 0.964 | 0.902 | 0.898 | 0.701 |
| machinery | 0.948 | 0.831 | 0.872 | 0.714 |
| vehicle | 1.000 | 0.707 | 0.857 | 0.596 |

## Where the models struggle

- **Bare heads** (`NO-Hardhat`) have the lowest recall of the PPE classes, so a missing hard hat
  is harder to confirm than a worn one. The temporal smoothing and the `NO-*`-without-person rule
  help, but some violations will be missed.
- **Small or distant** objects, notably safety cones.
- **Low light, backlighting, unusual helmet colours** and **heavy occlusion**.

The dataset is mostly daytime photos from public sources. If your site looks different (night
shifts, a specific uniform colour, overhead cameras), [fine-tuning](training.md) on a few hundred
labelled frames from your cameras will help more than anything else.

## Using your own model

Any Ultralytics detection model works if its class names follow the dataset's naming, or common
aliases: `person`/`worker`, `hardhat`/`hard hat`/`helmet`, `no-hardhat`/`no helmet`,
`safety vest`/`vest`, `no-safety vest`/`no vest`, `mask`/`face mask`, `no-mask`. Other classes are
ignored. Set **Settings → Detection & rules → Model** to the `.pt` path and restart cameras.

## Licence

The model weights derive from [Ultralytics YOLO](https://github.com/ultralytics/ultralytics),
which is AGPL-3.0, and are trained on a CC BY 4.0 dataset. PPE Guard's own code is MIT. If you
build a product or service on these weights, review the AGPL-3.0 terms or Ultralytics' commercial
licence.
