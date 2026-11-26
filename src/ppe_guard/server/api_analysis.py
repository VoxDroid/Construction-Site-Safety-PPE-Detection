"""Upload an image or video and get a per-worker verdict (the *Analyze* page)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse

from ppe_guard.analysis import IMAGE_EXT, VIDEO_EXT, kind_of
from ppe_guard.server.context import Context, ctx

router = APIRouter()
Ctx = Annotated[Context, Depends(ctx)]
CHUNK = 1 << 20


@router.post("/api/analyze", status_code=201)
def analyze(request: Request, c: Ctx, file: Annotated[UploadFile, File()]):
    name = file.filename or "upload"
    if kind_of(name) is None:
        allowed = ", ".join(sorted(IMAGE_EXT | VIDEO_EXT))
        raise HTTPException(415, f"unsupported file type; use one of {allowed}")
    limit = c.store.get().analysis.max_upload_mb * (1 << 20)
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > limit + (1 << 16):
        raise HTTPException(413, f"file is larger than {limit >> 20} MB")
    job, dest = c.analysis.create(name)
    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := file.file.read(CHUNK):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"file is larger than {limit >> 20} MB")
                out.write(chunk)
    except BaseException:
        c.analysis.discard(job["id"])
        raise
    if size == 0:
        c.analysis.discard(job["id"])
        raise HTTPException(400, "empty file")
    return c.analysis.start(job["id"])


@router.get("/api/analyze/jobs")
def jobs(c: Ctx):
    return c.analysis.list()


@router.get("/api/analyze/jobs/{job_id}")
def job(job_id: str, c: Ctx):
    j = c.analysis.get(job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    return j


@router.post("/api/analyze/jobs/{job_id}/cancel")
def cancel(job_id: str, c: Ctx):
    if c.analysis.get(job_id) is None:
        raise HTTPException(404, "job not found")
    if not c.analysis.cancel(job_id):
        raise HTTPException(409, "job is not running")
    return c.analysis.get(job_id)


@router.delete("/api/analyze/jobs/{job_id}", status_code=204)
def delete(job_id: str, c: Ctx):
    if not c.analysis.delete(job_id):
        raise HTTPException(404, "job not found")
    return Response(status_code=204)


@router.get("/analysis/{job_id}/{filename}")
def job_file(job_id: str, filename: str, c: Ctx, download: bool = False):
    p = c.analysis.file_path(job_id, filename)
    if p is None:
        raise HTTPException(404, "file not found")
    media = {".jpg": "image/jpeg", ".mp4": "video/mp4", ".webm": "video/webm"}.get(p.suffix)
    return FileResponse(p, media_type=media, filename=p.name if download else None)
