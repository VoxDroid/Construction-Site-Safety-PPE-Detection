"""Camera management, live MJPEG streams and frames for the zone editor."""

from __future__ import annotations

import asyncio
import time
from typing import Annotated

import cv2
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from ppe_guard.camera import grab_frame
from ppe_guard.config import CameraConfig, Zone
from ppe_guard.server.context import Context, camera_or_404, ctx

router = APIRouter()
Ctx = Annotated[Context, Depends(ctx)]
_NO_STORE = {"Cache-Control": "no-store"}


@router.get("/api/cameras")
def list_cameras(c: Ctx):
    return [w.info() for w in c.cameras.all()]


@router.get("/api/cameras/{camera_id}")
def get_camera(camera_id: str, c: Ctx):
    return camera_or_404(c, camera_id).info()


@router.post("/api/cameras", status_code=201)
def add_camera(cfg: CameraConfig, c: Ctx):
    s = c.store.get()
    if any(x.id == cfg.id for x in s.cameras):
        raise HTTPException(409, f"camera '{cfg.id}' already exists")
    s.cameras.append(cfg)
    c.cameras.sync(c.store.set(s).cameras)
    return camera_or_404(c, cfg.id).info()


@router.put("/api/cameras/{camera_id}")
def update_camera(camera_id: str, cfg: CameraConfig, c: Ctx):
    if cfg.id != camera_id:
        raise HTTPException(400, "camera id cannot be changed")
    s = c.store.get()
    if all(x.id != camera_id for x in s.cameras):
        raise HTTPException(404, f"camera '{camera_id}' not found")
    s.cameras = [cfg if x.id == camera_id else x for x in s.cameras]
    c.cameras.sync(c.store.set(s).cameras)
    return camera_or_404(c, camera_id).info()


@router.put("/api/cameras/{camera_id}/zones")
def set_zones(camera_id: str, zones: list[Zone], c: Ctx):
    s = c.store.get()
    cam = next((x for x in s.cameras if x.id == camera_id), None)
    if cam is None:
        raise HTTPException(404, f"camera '{camera_id}' not found")
    try:
        updated = CameraConfig.model_validate({**cam.model_dump(), "zones": [z.model_dump() for z in zones]})
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    s.cameras = [updated if x.id == camera_id else x for x in s.cameras]
    c.cameras.sync(c.store.set(s).cameras)
    return camera_or_404(c, camera_id).info()


@router.delete("/api/cameras/{camera_id}", status_code=204)
def delete_camera(camera_id: str, c: Ctx):
    camera_or_404(c, camera_id)
    s = c.store.get()
    s.cameras = [x for x in s.cameras if x.id != camera_id]
    c.cameras.sync(c.store.set(s).cameras)
    return Response(status_code=204)


@router.post("/api/cameras/{camera_id}/start")
def start_camera(camera_id: str, c: Ctx):
    w = camera_or_404(c, camera_id)
    if not w.cfg.enabled:
        raise HTTPException(409, "camera is disabled")
    w.start()
    return w.info()


@router.post("/api/cameras/{camera_id}/stop")
async def stop_camera(camera_id: str, c: Ctx):
    w = camera_or_404(c, camera_id)
    await asyncio.to_thread(w.stop)
    return w.info()


@router.get("/api/cameras/{camera_id}/stream")
async def stream(camera_id: str, request: Request, c: Ctx):
    w = camera_or_404(c, camera_id)

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
                             headers=_NO_STORE)


@router.get("/api/cameras/{camera_id}/frame.jpg")
def frame(camera_id: str, c: Ctx):
    _, jpeg = camera_or_404(c, camera_id).latest_jpeg()
    if jpeg is None:
        raise HTTPException(404, "no frame yet")
    return Response(jpeg, media_type="image/jpeg", headers=_NO_STORE)


@router.get("/api/cameras/{camera_id}/still.jpg")
def still(camera_id: str, c: Ctx):
    """An unannotated frame: the live one when running, otherwise one read from the source."""
    w = camera_or_404(c, camera_id)
    jpeg = w.latest_raw_jpeg() if w.running else None
    if jpeg is None:
        img = grab_frame(w.cfg.source)
        if img is None:
            raise HTTPException(404, f"could not read a frame from '{w.cfg.source}'")
        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        jpeg = buf.tobytes()
    return Response(jpeg, media_type="image/jpeg", headers=_NO_STORE)
