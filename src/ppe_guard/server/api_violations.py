"""Violations, their snapshots and clips, analytics and printable reports."""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from ppe_guard.server.context import Context, ctx

router = APIRouter()
Ctx = Annotated[Context, Depends(ctx)]
Item = Literal["helmet", "vest", "mask"]
Kind = Literal["ppe", "zone"]


class AckBody(BaseModel):
    acknowledged: bool = True
    note: str | None = Field(None, max_length=2000)


class BulkBody(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=1000)
    action: Literal["ack", "unack", "delete"]


def _filters(camera_id, item, kind, since, until, ongoing, acknowledged) -> dict:
    return {"camera_id": camera_id, "item": item, "kind": kind, "since": since, "until": until,
            "ongoing": ongoing, "acknowledged": acknowledged}


@router.get("/api/violations")
def list_violations(
    c: Ctx,
    camera_id: str | None = None,
    item: Item | None = None,
    kind: Kind | None = None,
    since: float | None = None,
    until: float | None = None,
    ongoing: bool | None = None,
    acknowledged: bool | None = None,
    sort: Literal["started_at", "last_seen", "ended_at", "camera_id", "duration"] = "started_at",
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return c.storage.list_violations(**_filters(camera_id, item, kind, since, until, ongoing, acknowledged),
                                     sort=sort, order=order, limit=limit, offset=offset)


@router.get("/api/violations/export.csv")
def export_csv(
    c: Ctx,
    camera_id: str | None = None,
    item: Item | None = None,
    kind: Kind | None = None,
    since: float | None = None,
    until: float | None = None,
    ongoing: bool | None = None,
    acknowledged: bool | None = None,
):
    body = c.storage.export_csv(**_filters(camera_id, item, kind, since, until, ongoing, acknowledged))
    name = time.strftime("ppe-violations-%Y%m%d-%H%M%S.csv")
    return PlainTextResponse(body, media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/api/violations/bulk")
def bulk(body: BulkBody, c: Ctx):
    if body.action == "delete":
        return {"affected": c.storage.delete_many(body.ids)}
    return {"affected": c.storage.acknowledge_many(body.ids, body.action == "ack")}


@router.get("/api/violations/{violation_id}")
def get_violation(violation_id: str, c: Ctx):
    v = c.storage.get_violation(violation_id)
    if v is None:
        raise HTTPException(404, "violation not found")
    return v


@router.post("/api/violations/{violation_id}/ack")
def ack_violation(violation_id: str, body: AckBody, c: Ctx):
    if not c.storage.acknowledge(violation_id, body.acknowledged, body.note):
        raise HTTPException(404, "violation not found")
    return c.storage.get_violation(violation_id)


@router.delete("/api/violations/{violation_id}", status_code=204)
def delete_violation(violation_id: str, c: Ctx):
    if not c.storage.delete_violation(violation_id):
        raise HTTPException(404, "violation not found")
    return Response(status_code=204)


@router.get("/snapshots/{filename}")
def snapshot(filename: str, c: Ctx, download: bool = False):
    p = c.storage.snapshot_path(filename)
    if p is None:
        raise HTTPException(404, "snapshot not found")
    return FileResponse(p, media_type="image/jpeg", filename=p.name if download else None)


@router.get("/clips/{filename}")
def clip(filename: str, c: Ctx, download: bool = False):
    p = c.storage.clip_path(filename)
    if p is None:
        raise HTTPException(404, "clip not found")
    media = "video/webm" if p.suffix == ".webm" else "video/mp4"
    return FileResponse(p, media_type=media, filename=p.name if download else None)


# ---- analytics and reports ------------------------------------------------------------------


@router.get("/api/analytics")
def analytics(
    c: Ctx,
    days: int = Query(7, ge=1, le=366),
    end: date | None = None,
    camera_id: str | None = None,
):
    end = end or date.today()
    return c.storage.analytics(end - timedelta(days=days - 1), days, camera_id)


@router.get("/api/report")
def report(
    c: Ctx,
    start: date | None = None,
    days: int = Query(1, ge=1, le=31),
    camera_id: str | None = None,
):
    """Everything the printable report needs for one period."""
    start = start or date.today()
    data = c.storage.analytics(start, days, camera_id)
    since = time.mktime(start.timetuple())
    until = time.mktime((start + timedelta(days=days)).timetuple())
    data["violations"] = c.storage.list_violations(camera_id=camera_id, since=since, until=until,
                                                   sort="started_at", order="asc", limit=500)
    s = c.store.get()
    data["settings"] = {
        "required_ppe": s.compliance.required_ppe.model_dump(),
        "violation_delay": s.compliance.violation_delay,
        "cameras": [{"id": x.id, "name": x.name, "zones": len(x.zones)} for x in s.cameras],
    }
    data["generated_at"] = time.time()
    return data
