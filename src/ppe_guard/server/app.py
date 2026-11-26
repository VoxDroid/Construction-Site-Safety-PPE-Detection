"""FastAPI dashboard server: REST API, MJPEG camera streams and a live WebSocket feed."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ppe_guard import __version__
from ppe_guard.alerts import AlertDispatcher
from ppe_guard.camera import CameraManager
from ppe_guard.config import CameraConfig, Settings, SettingsStore, default_data_dir
from ppe_guard.storage import Storage

STATIC_DIR = Path(str(files("ppe_guard.server") / "static"))


class Broadcaster:
    """Fan-out of JSON messages from camera threads to WebSocket clients."""

    def __init__(self) -> None:
        self._clients: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, msg: dict) -> None:  # called from any thread
        if self._loop is None or not self._clients:
            return
        data = json.dumps(msg)
        with contextlib.suppress(RuntimeError):  # event loop already closed during shutdown
            self._loop.call_soon_threadsafe(self._fanout, data)

    def _fanout(self, data: str) -> None:
        for q in list(self._clients):
            if q.full():  # slow client: drop its oldest message
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(data)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)


class AckBody(BaseModel):
    acknowledged: bool = True
    note: str | None = None


def create_app(data_dir: Path | None = None, autostart: bool = True) -> FastAPI:
    data_dir = (data_dir or default_data_dir()).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    store = SettingsStore(data_dir / "settings.json")
    storage = Storage(data_dir)
    broadcaster = Broadcaster()
    alerts = AlertDispatcher(lambda: store.get().alerts)
    cameras = CameraManager(store.get, storage, alerts, broadcaster.publish)
    cameras.sync(store.get().cameras)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        broadcaster.bind(asyncio.get_running_loop())
        if autostart:
            for c in store.get().cameras:
                if c.enabled and c.autostart:
                    cameras.get(c.id).start()
        yield
        await asyncio.to_thread(cameras.stop_all)
        alerts.close()
        storage.close()

    app = FastAPI(title="PPE Guard", version=__version__, lifespan=lifespan)
    app.state.store, app.state.storage, app.state.cameras = store, storage, cameras

    def camera_or_404(camera_id: str):
        w = cameras.get(camera_id)
        if w is None:
            raise HTTPException(404, f"camera '{camera_id}' not found")
        return w

    # ---- meta ---------------------------------------------------------------------------

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/stats")
    def stats():
        s = storage.stats()
        live = [w for w in cameras.all() if w.status == "running"]
        s["cameras_running"] = len(live)
        s["cameras_total"] = len(cameras.all())
        s["workers_in_view"] = sum(len(w.workers) for w in live)
        s["alert_error"] = alerts.last_error
        return s

    # ---- settings -----------------------------------------------------------------------

    @app.get("/api/settings", response_model=Settings)
    def get_settings():
        return store.get()

    @app.put("/api/settings", response_model=Settings)
    def put_settings(new: Settings):
        saved = store.set(new)
        cameras.sync(saved.cameras)
        return saved

    @app.post("/api/settings/reset", response_model=Settings)
    def reset_settings():
        saved = store.set(Settings(cameras=store.get().cameras))  # keep the camera list
        return saved

    @app.post("/api/alerts/test")
    def test_alert():
        try:
            status = alerts.send_test()
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(502, f"webhook failed: {e}") from e
        return {"status": status}

    # ---- cameras ------------------------------------------------------------------------

    @app.get("/api/cameras")
    def list_cameras():
        return [w.info() for w in cameras.all()]

    @app.post("/api/cameras", status_code=201)
    def add_camera(cfg: CameraConfig):
        s = store.get()
        if any(c.id == cfg.id for c in s.cameras):
            raise HTTPException(409, f"camera '{cfg.id}' already exists")
        s.cameras.append(cfg)
        cameras.sync(store.set(s).cameras)
        return camera_or_404(cfg.id).info()

    @app.put("/api/cameras/{camera_id}")
    def update_camera(camera_id: str, cfg: CameraConfig):
        if cfg.id != camera_id:
            raise HTTPException(400, "camera id cannot be changed")
        s = store.get()
        s.cameras = [cfg if c.id == camera_id else c for c in s.cameras]
        if all(c.id != camera_id for c in s.cameras):
            raise HTTPException(404, f"camera '{camera_id}' not found")
        cameras.sync(store.set(s).cameras)
        return camera_or_404(camera_id).info()

    @app.delete("/api/cameras/{camera_id}", status_code=204)
    def delete_camera(camera_id: str):
        camera_or_404(camera_id)
        s = store.get()
        s.cameras = [c for c in s.cameras if c.id != camera_id]
        cameras.sync(store.set(s).cameras)
        return Response(status_code=204)

    @app.post("/api/cameras/{camera_id}/start")
    def start_camera(camera_id: str):
        w = camera_or_404(camera_id)
        if not w.cfg.enabled:
            raise HTTPException(409, "camera is disabled")
        w.start()
        return w.info()

    @app.post("/api/cameras/{camera_id}/stop")
    async def stop_camera(camera_id: str):
        w = camera_or_404(camera_id)
        await asyncio.to_thread(w.stop)
        return w.info()

    @app.get("/api/cameras/{camera_id}/stream")
    async def stream(camera_id: str, request: Request):
        w = camera_or_404(camera_id)

        async def frames():
            last = -1
            idle_since = time.monotonic()
            while not await request.is_disconnected():
                fid, jpeg = w.latest_jpeg()
                if jpeg is not None and fid != last:
                    last, idle_since = fid, time.monotonic()
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
                elif not w.running and time.monotonic() - idle_since > 2:
                    break
                await asyncio.sleep(1 / 30)

        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store"})

    @app.get("/api/cameras/{camera_id}/frame.jpg")
    def frame(camera_id: str):
        _, jpeg = camera_or_404(camera_id).latest_jpeg()
        if jpeg is None:
            raise HTTPException(404, "no frame yet")
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    # ---- violations ---------------------------------------------------------------------

    def filters(camera_id, item, since, until, ongoing, acknowledged):
        return {"camera_id": camera_id, "item": item, "since": since, "until": until,
                "ongoing": ongoing, "acknowledged": acknowledged}

    @app.get("/api/violations")
    def list_violations(
        camera_id: str | None = None,
        item: Literal["helmet", "vest", "mask"] | None = None,
        since: float | None = None,
        until: float | None = None,
        ongoing: bool | None = None,
        acknowledged: bool | None = None,
        sort: Literal["started_at", "last_seen", "ended_at", "camera_id", "duration"] = "started_at",
        order: Literal["asc", "desc"] = "desc",
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        return storage.list_violations(**filters(camera_id, item, since, until, ongoing, acknowledged),
                                       sort=sort, order=order, limit=limit, offset=offset)

    @app.get("/api/violations/export.csv")
    def export_csv(
        camera_id: str | None = None,
        item: Literal["helmet", "vest", "mask"] | None = None,
        since: float | None = None,
        until: float | None = None,
        ongoing: bool | None = None,
        acknowledged: bool | None = None,
    ):
        body = storage.export_csv(**filters(camera_id, item, since, until, ongoing, acknowledged))
        name = time.strftime("ppe-violations-%Y%m%d-%H%M%S.csv")
        return PlainTextResponse(body, media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/violations/{violation_id}")
    def get_violation(violation_id: str):
        v = storage.get_violation(violation_id)
        if v is None:
            raise HTTPException(404, "violation not found")
        return v

    @app.post("/api/violations/{violation_id}/ack")
    def ack_violation(violation_id: str, body: AckBody):
        if not storage.acknowledge(violation_id, body.acknowledged, body.note):
            raise HTTPException(404, "violation not found")
        return storage.get_violation(violation_id)

    @app.delete("/api/violations/{violation_id}", status_code=204)
    def delete_violation(violation_id: str):
        if not storage.delete_violation(violation_id):
            raise HTTPException(404, "violation not found")
        return Response(status_code=204)

    @app.get("/snapshots/{filename}")
    def snapshot(filename: str, download: bool = False):
        p = storage.snapshot_path(filename)
        if p is None:
            raise HTTPException(404, "snapshot not found")
        return FileResponse(p, media_type="image/jpeg",
                            filename=p.name if download else None)

    # ---- live feed ----------------------------------------------------------------------

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        q = broadcaster.subscribe()
        try:
            while True:
                await socket.send_text(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            broadcaster.unsubscribe(q)

    # ---- UI -----------------------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
