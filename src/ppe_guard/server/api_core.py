"""Health, stats, settings, system information and authentication routes."""

from __future__ import annotations

import platform
import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ppe_guard import __version__
from ppe_guard.auth import COOKIE
from ppe_guard.config import Settings
from ppe_guard.server.context import Context, ctx
from ppe_guard.video import video_format

router = APIRouter()
Ctx = Annotated[Context, Depends(ctx)]


@router.get("/api/health")
def health():
    return {"status": "ok", "version": __version__}


@router.get("/api/stats")
def stats(c: Ctx):
    s = c.storage.stats()
    cams = c.cameras.all()
    live = [w for w in cams if w.status == "running"]
    s["cameras_running"] = len(live)
    s["cameras_total"] = len(cams)
    s["workers_in_view"] = sum(sum(1 for x in w.workers if not x["synthetic"]) for w in live)
    s["violating_now"] = sum(sum(1 for x in w.workers if x["state"] == "violation") for w in live)
    s["alert_error"] = c.alerts.last_error
    return s


# ---- settings ---------------------------------------------------------------------------


@router.get("/api/settings", response_model=Settings)
def get_settings(c: Ctx):
    return c.store.get()


@router.put("/api/settings", response_model=Settings)
def put_settings(new: Settings, c: Ctx):
    saved = c.store.set(new)
    c.cameras.sync(saved.cameras)
    return saved


@router.post("/api/settings/reset", response_model=Settings)
def reset_settings(c: Ctx):
    return c.store.set(Settings(cameras=c.store.get().cameras))  # keep the camera list


@router.post("/api/alerts/test")
def test_alert(c: Ctx):
    try:
        status = c.alerts.send_test()
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"webhook failed: {e}") from e
    return {"status": status}


@router.get("/api/models")
def models():
    from ppe_guard.weights import DEFAULT_MODEL, MODELS, models_dir

    d = models_dir()
    out = [{"name": m.alias, "description": m.description, "downloaded": (d / m.filename).exists(),
            "default": m.alias == DEFAULT_MODEL} for m in MODELS.values()]
    known = {m.filename for m in MODELS.values()}
    if d.exists():
        out += [{"name": str(p), "description": "custom weights", "downloaded": True, "default": False}
                for p in sorted(d.glob("*.pt")) if p.name not in known]
    return out


def _dir_size(path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


@router.get("/api/system")
def system(c: Ctx):
    try:
        import torch

        device = (torch.cuda.get_device_name(0) if torch.cuda.is_available()
                  else "Apple MPS" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()
                  else "CPU")
        torch_v = torch.__version__
    except Exception:  # pragma: no cover - torch missing or broken
        device, torch_v = "unknown", None
    try:
        import ultralytics

        ul = ultralytics.__version__
    except Exception:  # pragma: no cover
        ul = None
    fmt = video_format()
    du = shutil.disk_usage(c.data_dir)
    return {
        "version": __version__,
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.machine()}",
        "torch": torch_v,
        "ultralytics": ul,
        "device": device,
        "video_codec": {"fourcc": fmt.fourcc, "mime": fmt.mime, "playable": fmt.playable},
        "data_dir": str(c.data_dir),
        "storage": {
            "snapshots_bytes": _dir_size(c.storage.snapshot_dir),
            "clips_bytes": _dir_size(c.storage.clip_dir),
            "analysis_bytes": _dir_size(c.analysis.root),
            "database_bytes": sum(_dir_size(c.data_dir / n) for n in ("ppe_guard.db", "ppe_guard.db-wal")),
            "disk_free_bytes": du.free,
            "disk_total_bytes": du.total,
        },
        "websocket_clients": c.broadcaster.clients,
    }


# ---- auth -------------------------------------------------------------------------------


class LoginBody(BaseModel):
    password: str
    remember: bool = False


class PasswordBody(BaseModel):
    current: str | None = None
    new: str


class ClearPasswordBody(BaseModel):
    current: str


@router.get("/api/auth/status")
def auth_status(request: Request, c: Ctx):
    return {
        "enabled": c.auth.enabled,
        "authenticated": c.auth.allowed(request.cookies.get(COOKIE),
                                        _bearer(request), None),
        "token_set": c.auth.token_set,
        "from_env": c.auth.from_env,
    }


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else None


def _client(request: Request) -> str:
    return request.client.host if request.client else "?"


def _set_cookie(request: Request, response: Response, value: str, max_age: int, remember: bool) -> None:
    response.set_cookie(COOKIE, value, max_age=max_age if remember else None, httponly=True,
                        samesite="lax", secure=request.url.scheme == "https", path="/")


@router.post("/api/auth/login")
def login(body: LoginBody, request: Request, response: Response, c: Ctx):
    if not c.auth.enabled:
        return {"ok": True}
    client = _client(request)
    if c.auth.limiter.blocked(client):
        raise HTTPException(429, "too many failed attempts, try again in a few minutes")
    if not c.auth.check_password(body.password):
        c.auth.limiter.fail(client)
        raise HTTPException(401, "wrong password")
    c.auth.limiter.reset(client)
    value, max_age = c.auth.issue_session(body.remember)
    _set_cookie(request, response, value, max_age, body.remember)
    return {"ok": True}


@router.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


def _require_login(request: Request, c: Context) -> None:
    """Changing credentials needs a real login when a password is set."""
    if c.auth.enabled and not c.auth.check_session(request.cookies.get(COOKIE)):
        raise HTTPException(401, "log in to change security settings")


@router.post("/api/auth/password")
def set_password(body: PasswordBody, request: Request, response: Response, c: Ctx):
    _require_login(request, c)
    if c.auth.enabled and not c.auth.check_password(body.current or ""):
        raise HTTPException(403, "current password is wrong")
    try:
        c.auth.set_password(body.new)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    value, max_age = c.auth.issue_session(False)  # stay logged in after the secret rotates
    _set_cookie(request, response, value, max_age, False)
    return {"enabled": True}


@router.post("/api/auth/password/clear")
def clear_password(body: ClearPasswordBody, request: Request, response: Response, c: Ctx):
    _require_login(request, c)
    if not c.auth.check_password(body.current):
        raise HTTPException(403, "current password is wrong")
    c.auth.clear_password()
    response.delete_cookie(COOKIE, path="/")
    return {"enabled": False}


@router.post("/api/auth/token")
def create_token(request: Request, c: Ctx):
    _require_login(request, c)
    if not c.auth.enabled:
        raise HTTPException(409, "set a password first")
    return {"token": c.auth.create_token()}


@router.delete("/api/auth/token")
def revoke_token(request: Request, c: Ctx):
    _require_login(request, c)
    c.auth.revoke_token()
    return {"token_set": False}
