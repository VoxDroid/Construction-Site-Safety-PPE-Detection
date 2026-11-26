"""Webhook alerts for new violations (generic JSON, Slack or Discord)."""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.request
from datetime import datetime

from ppe_guard.config import AlertSettings


def build_payload(settings: AlertSettings, event: dict) -> dict:
    """Shape the webhook body for the configured service."""
    missing = ", ".join(event["missing"]) or "PPE"
    when = datetime.fromtimestamp(event["started_at"]).strftime("%Y-%m-%d %H:%M:%S")
    text = f"PPE violation on {event['camera_name']}: worker missing {missing} (since {when})."
    if settings.public_url:
        text += f"\n{settings.public_url}/#/violations/{event['id']}"
    if settings.format == "slack":
        return {"text": text}
    if settings.format == "discord":
        return {"content": text}
    return {"type": "ppe_violation", "message": text, **event}


def post_json(url: str, payload: dict, timeout: float = 10.0) -> int:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "ppe-guard"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


class AlertDispatcher:
    """Sends alerts on a background thread so slow webhooks never stall video processing."""

    def __init__(self, get_settings) -> None:
        self._get_settings = get_settings
        self._queue: queue.Queue[dict | None] = queue.Queue(maxsize=200)
        self._last_sent: dict[str, float] = {}
        self._thread = threading.Thread(target=self._run, name="alerts", daemon=True)
        self._thread.start()
        self.last_error: str | None = None

    def notify(self, event: dict) -> bool:
        """Queue an alert for a newly opened violation, honouring the per-camera cooldown."""
        s: AlertSettings = self._get_settings()
        if not s.webhook_url:
            return False
        now = time.time()
        cam = event.get("camera_id", "")
        if now - self._last_sent.get(cam, 0.0) < s.cooldown:
            return False
        self._last_sent[cam] = now
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            return False
        return True

    def send_test(self) -> int:
        s: AlertSettings = self._get_settings()
        if not s.webhook_url:
            raise ValueError("no webhook_url configured")
        now = time.time()
        sample = {"id": "test", "camera_id": "test", "camera_name": "Test camera",
                  "worker_key": "t1", "missing": ["helmet"], "started_at": now,
                  "last_seen": now, "ended_at": None, "test": True}
        return post_json(s.webhook_url, build_payload(s, sample))

    def close(self) -> None:
        self._queue.put(None)

    def _run(self) -> None:
        while True:
            event = self._queue.get()
            if event is None:
                return
            s: AlertSettings = self._get_settings()
            if not s.webhook_url:
                continue
            try:
                post_json(s.webhook_url, build_payload(s, event))
                self.last_error = None
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                print(f"[ppe-guard] alert failed: {self.last_error}")
