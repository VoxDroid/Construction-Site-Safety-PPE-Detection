# Cameras

PPE Guard reads video with OpenCV, so it accepts most things a camera can produce. Each camera
runs on its own thread with its own tracker.

## Adding a camera

**Settings → Cameras → Add camera**, or with the [API](../reference/api.md):

```bash
curl -X POST http://localhost:8000/api/cameras \
  -H "Content-Type: application/json" \
  -d '{"id": "north-gate", "name": "North gate", "source": "rtsp://user:pass@192.168.1.20/stream1", "autostart": true}'
```

| Field | Meaning |
| --- | --- |
| **Name** | Shown everywhere in the dashboard, reports and alerts. |
| **ID** | A short slug (`north-gate`). Used in URLs and the database. It can't be changed later. |
| **Source** | Where the video comes from (below). |
| **Enabled** | Disabled cameras are kept but can't be started. |
| **Start with the server** | Starts the camera automatically when `ppe-guard serve` starts. |
| **Loop video files** | Restart a video file when it ends (handy for demos and testing). |

## Sources

| Source | Example | Notes |
| --- | --- | --- |
| Webcam | `0`, `1` | The index of the camera on the machine running the server. |
| Video file | `/data/videos/shift.mp4` | A path on the server. Plays at its own frame rate. |
| RTSP | `rtsp://user:pass@192.168.1.20:554/stream1` | Most IP cameras and NVRs. Use a sub-stream (lower resolution) if bandwidth is tight. |
| HTTP / MJPEG | `http://192.168.1.21:8080/video` | Phone camera apps and some IP cameras. |

Finding your camera's RTSP address: check its manual or web interface. Common patterns are
`rtsp://<ip>:554/Streaming/Channels/101` (Hikvision), `rtsp://<ip>:554/cam/realmonitor?channel=1&subtype=0`
(Dahua) and `rtsp://<ip>:554/stream1` (many others). Test the address in VLC first
(**Media → Open Network Stream**).

!!! note "Credentials in the source"
    Camera passwords in an RTSP URL are stored in `settings.json` and shown in **Settings →
    Cameras**. Protect the dashboard with a [password](security.md) and the data folder with file
    permissions.

## Starting and stopping

Use **Start** and **Stop** on the Live page or the camera page. When a stream drops, the camera
shows **Reconnecting** and retries with a growing delay (up to 30 s). A video file that ends shows
**Ended** unless looping is on.

Changing the **source** of a running camera restarts it. Other changes (name, zones) apply while
it runs.

## How fast does it run?

Each camera processes at most **Max FPS per camera** frames per second (15 by default, in
**Settings → Detection & rules**). Video files never play faster than their own frame rate. Lower
the limit to share a GPU between more cameras, or raise **Image size** for small, distant workers
at the cost of speed. Measured numbers are in [Performance](../deployment/performance.md).

## Changing the model

**Settings → Detection & rules → Model** accepts `auto`, a model name (`ppe-yolo26s`,
`ppe-yolov8s`) or a path to your own `.pt` file. Restart cameras to load a new model. See
[Training](../reference/training.md) to train your own.
