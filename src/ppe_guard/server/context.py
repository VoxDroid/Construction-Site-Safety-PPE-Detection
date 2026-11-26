"""Shared server state, reachable from routes as ``request.app.state.ctx``."""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, Request

from ppe_guard.alerts import AlertDispatcher
from ppe_guard.analysis import AnalysisManager
from ppe_guard.auth import AuthManager
from ppe_guard.camera import CameraManager, CameraWorker
from ppe_guard.config import SettingsStore
from ppe_guard.storage import Storage
from ppe_guard.video import ClipWriter


class Broadcaster:
    """Fan-out of JSON messages from camera threads to WebSocket clients."""

    def __init__(self) -> None:
        self._clients: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def clients(self) -> int:
        return len(self._clients)

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
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)


@dataclass
class Context:
    data_dir: Path
    store: SettingsStore
    storage: Storage
    alerts: AlertDispatcher
    auth: AuthManager
    broadcaster: Broadcaster
    clips: ClipWriter
    cameras: CameraManager
    analysis: AnalysisManager


def ctx(request: Request) -> Context:
    return request.app.state.ctx


def camera_or_404(c: Context, camera_id: str) -> CameraWorker:
    w = c.cameras.get(camera_id)
    if w is None:
        raise HTTPException(404, f"camera '{camera_id}' not found")
    return w
