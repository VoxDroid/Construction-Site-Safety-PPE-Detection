# Performance

How many cameras one server can handle, and how the dashboard behaves under load. All numbers
come from `scripts/stress.py`, which you can run on your own hardware (see the end of this page).

## Test machine

| | |
| --- | --- |
| CPU | AMD Ryzen 5 5600G (6 cores, 12 threads) |
| GPU | NVIDIA GeForce RTX 5060 Ti, 16 GB |
| RAM | 32 GB |
| Software | Windows 11, Python 3.12, PyTorch 2.11 (CUDA 12.8), Ultralytics 8.4, PPE Guard 2.0.0 |
| Model | `ppe-yolo26s`, image size 640 |

Every test ran for 150 seconds after a 30-second warm-up, with:

- cameras playing three sample videos in a loop (640×360 and 1280×720, 25-30 fps),
- a database seeded with **100,000 violations** over 90 days, plus worker-time,
- live-view viewers on the MJPEG streams and dashboard clients on the WebSocket,
- clients calling the API in a loop: stats, camera list, violation lists with filters and deep
  pages, 7- and 90-day analytics, weekly reports and settings.

## Results

| Scenario | Cameras × Max FPS | Processed fps per camera | API median | API p95 | API errors | Server RAM |
| --- | --- | --- | --- | --- | --- | --- |
| A. Typical | 4 × 15 | **12.1** (min 9.9) | 42 ms | 1.4 s | 0 | 2.0 GB |
| B. Typical | 8 × 15 | **6.7** (min 3.7) | 63 ms | 3.5 s | 0 | 2.4 GB |
| C. Typical, capped | 8 × 5 | **4.8** (min 4.3) | 40 ms | 0.7 s | 0 | 2.4 GB |
| D. API flood | 8 × 15 | 4.0 (min 2.7) | 224 ms | 10.6 s | 0 | 2.4 GB |

"Typical" means 3 clients calling the API back to back (far more than a few people with the
dashboard open), 4 stream viewers and 10 WebSocket clients. The "API flood" scenario uses 16
clients calling back to back, 8 viewers and 20 WebSocket clients.

Per-request times in the typical 8-camera scenario (B), 95th percentile:

| Request | p95 |
| --- | --- |
| Stats, camera list, settings | 15-60 ms |
| Violation list, filtered, sorted or 5,000 rows deep | 45-110 ms |
| Analytics, 7 days | 0.5 s |
| Weekly report | 0.6 s |
| Analytics, 90 days (100,000 violations) | 3.9 s |

In every scenario:

- no camera stopped or stalled, and the server stayed healthy,
- stream viewers received every processed frame,
- every WebSocket client received the live messages (thousands each),
- violations were recorded with snapshots and clips while the load ran,
- memory stayed flat (within about 80 MB over the run), and GPU memory (for the whole card,
  including the desktop) stayed between 1.9 and 2.4 GB.

## What limits a server

**Processed frames per second across all cameras.** Under typical load, one server process
handled about **45-55 frames per second in total**, shared between its cameras (4 cameras at
~12 fps, 8 at ~6.7 fps).
The GPU is far from busy at that rate. The limit is the Python work around each inference
(preparing the frame, tracking, matching PPE), which runs one camera at a time per CPU core under
Python's global interpreter lock.

That is enough for PPE monitoring: with the default 2-second violation delay, **4-5 fps per camera
is plenty** to catch violations reliably. To spread the capacity evenly, set **Max FPS per camera**
to about `45 / number of cameras`. For more cameras than one process can serve, run a second
`ppe-guard serve` (with its own `--data-dir` and port) for the other cameras.

**Memory.** Each running camera loads its own copy of the model and tracker: the server used
about 2.0 GB of RAM with 4 cameras and 2.4 GB with 8, so roughly 90 MB per camera on top of a
1.6 GB base. GPU memory grew by about 100 MB per camera.

**Database size.** The database stays fast at 100,000 violations: lists and stats return in
well under 0.1 s on an otherwise idle server. Analytics over long periods is the heaviest
request; it scales with the number of violations in the period. Set a retention period if you
expect millions of violations.

## Without a GPU

On a CPU, inference itself becomes the limit. On the test machine's Ryzen 5 5600G (no GPU),
one stream processed **15 fps at image size 640** and **21 fps at 480** (67 and 47 ms per frame).
That total is shared between cameras, so a desktop CPU can watch two or three cameras at about
5 fps each. Laptop and low-power CPUs are slower; a smaller model
(`ppe-guard train --model yolo26n.pt`) or a smaller **Image size** helps. Measure on your own
machine with the script below.

## Run the test yourself

```bash
pip install -e ".[dev]"
python scripts/stress.py --videos "path/to/videos/*.mp4" --cameras 8 --duration 150 \
  --api-concurrency 3 --viewers 4 --ws-clients 10
```

Options include `--max-fps`, `--imgsz`, `--seed-violations` and `--keep` (keep the data folder).
The script prints a JSON report like this (abridged):

```json
{
  "cameras": 8,
  "api": {"requests": 950, "errors": 0, "p50_ms": 63.3, "p95_ms": 3546.0},
  "camera_fps": {"mean_per_camera": 6.66, "min_per_camera": 3.7, "total": 53.3},
  "mjpeg_viewers": {"viewers": 4, "frames_min": 922, "errors": []},
  "websocket": {"clients": 10, "messages_min": 4046},
  "server": {"rss_start_mb": 2324.0, "rss_end_mb": 2400.0, "cpu_mean_pct": 363.0},
  "violations_recorded_during_test": 51,
  "server_still_healthy": true
}
```

!!! note "Video files vs. live cameras"
    The test uses video files, which never run faster than their own frame rate. Live RTSP
    cameras behave the same way; decoding high-resolution H.265 streams adds CPU load, so use a
    camera's sub-stream (for example 1280×720) where you can.
