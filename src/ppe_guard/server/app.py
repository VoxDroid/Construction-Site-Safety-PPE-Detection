"""FastAPI dashboard server: REST API, MJPEG camera streams and a live WebSocket feed."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from contextlib import asynccontextmanager
from http.cookies import SimpleCookie
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ppe_guard import __version__
from ppe_guard.alerts import AlertDispatcher
from ppe_guard.analysis import AnalysisManager
from ppe_guard.auth import COOKIE, AuthManager
from ppe_guard.camera import CameraManager
from ppe_guard.config import SettingsStore, default_data_dir
from ppe_guard.server import api_analysis, api_cameras, api_core, api_violations
from ppe_guard.server.context import Broadcaster, Context
from ppe_guard.storage import Storage
from ppe_guard.video import ClipWriter

STATIC_DIR = Path(str(files("ppe_guard.server") / "static"))
MAINTENANCE_SECONDS = 3600

_PUBLIC_EXACT = {"/login", "/api/health", "/favicon.ico", "/api/auth/status", "/api/auth/login",
                 "/api/auth/logout"}
_PUBLIC_PREFIX = ("/static/",)


class AuthMiddleware:
    """Require a session cookie or API token for everything except the login page and assets."""

    def __init__(self, app, auth: AuthManager) -> None:
        self.app, self.auth = app, auth

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or not self.auth.enabled:
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIX):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        cookie = None
        if "cookie" in headers:
            jar = SimpleCookie()
            with contextlib.suppress(Exception):
                jar.load(headers["cookie"])
            if COOKIE in jar:
                cookie = jar[COOKIE].value
        auth = headers.get("authorization", "")
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else None
        query_token = None
        if scope["type"] == "websocket" or scope.get("method") in ("GET", "HEAD"):
            query_token = (parse_qs(scope.get("query_string", b"").decode()).get("token") or [None])[0]
        if self.auth.allowed(cookie, bearer, query_token):
            return await self.app(scope, receive, send)

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return None
        if scope.get("method") == "GET" and not path.startswith("/api/") and \
                "text/html" in headers.get("accept", ""):
            target = path + ("?" + scope["query_string"].decode() if scope.get("query_string") else "")
            loc = "/login" + ("" if path == "/" else "?next=" + quote(target, safe=""))
            await send({"type": "http.response.start", "status": 303,
                        "headers": [(b"location", loc.encode()), (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": b""})
            return None
        body = json.dumps({"detail": "login required"}).encode()
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"),
                                (b"www-authenticate", b'Bearer realm="ppe-guard"')]})
        await send({"type": "http.response.body", "body": body})
        return None


def create_app(data_dir: Path | None = None, autostart: bool = True,
               password: str | None = None) -> FastAPI:
    """Build the app. ``password`` (or ``PPE_GUARD_PASSWORD``) turns on the login."""
    data_dir = (data_dir or default_data_dir()).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    store = SettingsStore(data_dir / "settings.json")
    storage = Storage(data_dir)
    broadcaster = Broadcaster()
    alerts = AlertDispatcher(lambda: store.get().alerts, lambda: store.get().general.site_name)
    auth = AuthManager(data_dir, env_password=password)

    def clip_saved(violation_id: str, filename: str) -> None:
        storage.set_clip(violation_id, filename)
        broadcaster.publish({"type": "clip", "violation_id": violation_id, "clip": filename})

    clips = ClipWriter(storage.clip_dir, clip_saved)
    cameras = CameraManager(store.get, storage, alerts, broadcaster.publish, clips)
    cameras.sync(store.get().cameras)
    analysis = AnalysisManager(data_dir, store.get, broadcaster.publish)
    context = Context(data_dir, store, storage, alerts, auth, broadcaster, clips, cameras, analysis)

    def maintenance() -> None:
        s = store.get()
        if s.storage.retention_days > 0:
            n = storage.purge(time.time() - s.storage.retention_days * 86400)
            if n:
                print(f"[ppe-guard] retention: deleted {n} old violation(s)")
        analysis.prune(s.analysis.keep_jobs)

    async def maintenance_loop() -> None:
        while True:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(maintenance)
            await asyncio.sleep(MAINTENANCE_SECONDS)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        broadcaster.bind(asyncio.get_running_loop())
        task = asyncio.create_task(maintenance_loop())
        if autostart:
            for c in store.get().cameras:
                if c.enabled and c.autostart:
                    cameras.get(c.id).start()
        yield
        task.cancel()
        await asyncio.to_thread(cameras.stop_all)
        await asyncio.to_thread(clips.close)
        await asyncio.to_thread(analysis.close)
        alerts.close()
        storage.close()

    app = FastAPI(title="PPE Guard", version=__version__, lifespan=lifespan,
                  description="PPE compliance monitoring API. See the docs for authentication.")
    app.state.ctx = context
    # Kept for tests and scripts that used the 2.0 attribute names.
    app.state.store, app.state.storage, app.state.cameras = store, storage, cameras
    app.add_middleware(AuthMiddleware, auth=auth)

    for r in (api_core.router, api_cameras.router, api_violations.router, api_analysis.router):
        app.include_router(r)

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

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/login", include_in_schema=False)
    def login_page():
        return FileResponse(STATIC_DIR / "login.html", headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
