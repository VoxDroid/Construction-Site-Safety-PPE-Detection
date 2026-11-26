# REST API

Everything the dashboard does goes through a JSON API that you can use too. The running server
also serves interactive documentation at **`/docs`** and the OpenAPI schema at
**`/openapi.json`**, which always match the installed version.

## Authentication

With no password set, no authentication is needed. With a password set, send either

- the session cookie from `POST /api/auth/login`, or
- an [API token](../guide/security.md#api-tokens): `Authorization: Bearer <token>`, or
  `?token=<token>` on GET requests.

Unauthenticated requests get `401`.

```bash
TOKEN=ppe_XXXXXXXX
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/stats
```

## Endpoints

### Status

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/health` | `{"status": "ok", "version": "2.0.1"}`. Never needs a login. |
| GET | `/api/stats` | Open violations, today's count, unreviewed count, compliance today, workers in view, cameras running, last-24-hour breakdowns. |
| GET | `/api/system` | Versions, compute device, video codec, disk usage. |
| GET | `/api/models` | Available models and whether they are downloaded. |

### Settings

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/settings` | All settings (see [Configuration](configuration.md)). |
| PUT | `/api/settings` | Replace all settings. Send the full object back with your changes; invalid values give `422` with details. |
| POST | `/api/settings/reset` | Restore defaults, keeping cameras and zones. |
| POST | `/api/alerts/test` | Send a test alert. `400` without a webhook, `502` if it fails. |

### Cameras

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/cameras` | Cameras with status, fps, current workers and zones. |
| GET | `/api/cameras/{id}` | One camera. |
| POST | `/api/cameras` | Add a camera: `{"id", "name", "source", "enabled", "autostart", "loop", "zones"}`. `409` if the ID exists. |
| PUT | `/api/cameras/{id}` | Replace a camera's settings. The ID can't change. |
| PUT | `/api/cameras/{id}/zones` | Replace its zones with a list (see [Zones](../guide/zones.md#zones-in-the-api-and-settings-file)). |
| DELETE | `/api/cameras/{id}` | Remove a camera. Its violations are kept. |
| POST | `/api/cameras/{id}/start` | Start processing. `409` if disabled. |
| POST | `/api/cameras/{id}/stop` | Stop processing. |
| GET | `/api/cameras/{id}/stream` | Annotated live video as MJPEG (`multipart/x-mixed-replace`). Works in an `<img>` tag. |
| GET | `/api/cameras/{id}/frame.jpg` | The latest annotated frame. |
| GET | `/api/cameras/{id}/still.jpg` | An unannotated frame (live, or read from the source when stopped). |

### Violations

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/violations` | List with filters (below), sorted and paginated. Returns `{"total", "items"}`. |
| GET | `/api/violations/export.csv` | The same filters, as CSV. |
| GET | `/api/violations/{id}` | One violation with its snapshot list. |
| POST | `/api/violations/{id}/ack` | `{"acknowledged": true, "note": "..."}`. |
| DELETE | `/api/violations/{id}` | Delete it with its snapshots and clip. |
| POST | `/api/violations/bulk` | `{"ids": [...], "action": "ack" \| "unack" \| "delete"}`. |
| GET | `/snapshots/{filename}` | A snapshot JPEG (`?download=1` to save it). |
| GET | `/clips/{filename}` | A clip (MP4 or WebM). Supports range requests for seeking. |

Filters for the list and CSV export:

| Parameter | Values |
| --- | --- |
| `camera_id` | A camera ID |
| `kind` | `ppe` or `zone` |
| `item` | `helmet`, `vest` or `mask` |
| `since`, `until` | Unix seconds (on the start time) |
| `ongoing` | `true` or `false` |
| `acknowledged` | `true` or `false` |
| `sort` | `started_at` (default), `last_seen`, `ended_at`, `camera_id`, `duration` |
| `order` | `desc` (default) or `asc` |
| `limit`, `offset` | Page size (1-500, default 50) and offset |

A violation looks like:

```json
{
  "id": "8e3da6d2eb4f",
  "kind": "ppe",
  "camera_id": "gate",
  "camera_name": "Main gate",
  "worker_key": "t14",
  "missing": ["helmet", "vest"],
  "zone_id": null,
  "zone_name": null,
  "started_at": 1795671362.4,
  "last_seen": 1795671380.1,
  "ended_at": 1795671380.1,
  "duration": 17.7,
  "ongoing": false,
  "acknowledged": true,
  "note": "Hard hat issued",
  "clip": "8e3da6d2eb4f.mp4",
  "snapshot_count": 2,
  "thumbnail": "8e3da6d2eb4f_20261126-133602_412.jpg"
}
```

### Analytics and reports

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/analytics?days=7&end=2026-11-26&camera_id=gate` | Totals, per-day series, weekday-by-hour heatmap, per camera and per zone. `days` 1-366; `end` defaults to today. |
| GET | `/api/report?start=2026-11-23&days=7&camera_id=gate` | Analytics for the period plus up to 500 violations and the rules in force. `days` 1-31. |

### Analyze

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/analyze` | Upload one file as `multipart/form-data` field `file`. Images return the finished result; videos return a queued job. `415` for unsupported types, `413` when too large. |
| GET | `/api/analyze/jobs` | All kept jobs, newest first. |
| GET | `/api/analyze/jobs/{id}` | One job with `status`, `progress` and, when done, `result`. |
| POST | `/api/analyze/jobs/{id}/cancel` | Stop a queued or running video. |
| DELETE | `/api/analyze/jobs/{id}` | Delete a job and its files. |
| GET | `/analysis/{id}/{filename}` | Result files (annotated image or video, snapshots). |

```bash
curl -F "file=@site.jpg" http://localhost:8000/api/analyze
```

### Login

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/auth/status` | `{"enabled", "authenticated", "token_set", "from_env"}`. |
| POST | `/api/auth/login` | `{"password", "remember"}`; sets the session cookie. `401` wrong password, `429` too many attempts. |
| POST | `/api/auth/logout` | Clears the cookie. |
| POST | `/api/auth/password` | `{"current", "new"}`; set or change the password (session login required). |
| POST | `/api/auth/password/clear` | `{"current"}`; remove the password. |
| POST | `/api/auth/token` | Create (or replace) the API token. Returns it once. |
| DELETE | `/api/auth/token` | Revoke the token. |

## WebSocket

Connect to **`/ws`** (with the session cookie or `?token=`) to receive JSON messages as things
happen:

| `type` | Sent when | Fields |
| --- | --- | --- |
| `frame` | About 4 times a second per running camera | `camera_id`, `fps`, `violating`, `workers` (each with `key`, `state`, `items`, `required`, `missing`, `zones`, `intrusion`, `box`) |
| `violation` | A violation opens, changes or closes | `event` (`opened`, `updated`, `closed`), `violation` (as above, with `camera_id` and `camera_name`) |
| `camera` | A camera's status changes | `camera_id`, `status` (`running`, `reconnecting`, `stopped`, `ended`, `error`), `error` |
| `clip` | A violation's clip has been written | `violation_id`, `clip` |
| `job` | An Analyze job progresses or finishes | `job` |

```python
import asyncio, json, websockets

async def main():
    async with websockets.connect("ws://localhost:8000/ws?token=ppe_XXXXXXXX") as ws:
        async for raw in ws:
            msg = json.loads(raw)
            if msg["type"] == "violation" and msg["event"] == "opened":
                v = msg["violation"]
                print(v["camera_name"], v["missing"] or v["zone_name"])

asyncio.run(main())
```

A client that reads too slowly loses its oldest messages rather than slowing the server down.
