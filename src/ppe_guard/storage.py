"""SQLite storage for violations and their snapshots."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS violations (
    id            TEXT PRIMARY KEY,
    camera_id     TEXT NOT NULL,
    camera_name   TEXT NOT NULL,
    worker_key    TEXT NOT NULL,
    missing       TEXT NOT NULL,           -- JSON list, e.g. ["helmet","vest"]
    started_at    REAL NOT NULL,           -- unix seconds
    last_seen     REAL NOT NULL,
    ended_at      REAL,                    -- NULL while the violation is ongoing
    acknowledged  INTEGER NOT NULL DEFAULT 0,
    note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_violations_started ON violations(started_at);
CREATE INDEX IF NOT EXISTS idx_violations_camera ON violations(camera_id);
CREATE TABLE IF NOT EXISTS snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    violation_id  TEXT NOT NULL REFERENCES violations(id) ON DELETE CASCADE,
    filename      TEXT NOT NULL,
    taken_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_violation ON snapshots(violation_id);
"""

SORT_COLUMNS = {"started_at", "last_seen", "ended_at", "camera_id", "duration"}


class Storage:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.snapshot_dir = data_dir / "snapshots"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(data_dir / "ppe_guard.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)
        # Violations left open by a crash or shutdown end at their last sighting.
        self._db.execute("UPDATE violations SET ended_at = last_seen WHERE ended_at IS NULL")
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- writes -------------------------------------------------------------------------

    def open_violation(self, v: dict, camera_id: str, camera_name: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO violations (id, camera_id, camera_name, worker_key, missing,"
                " started_at, last_seen) VALUES (?,?,?,?,?,?,?)",
                (v["id"], camera_id, camera_name, v["worker_key"], json.dumps(v["missing"]),
                 v["started_at"], v["last_seen"]),
            )
            self._db.commit()

    def update_violation(self, v: dict) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE violations SET missing = ?, last_seen = ?, ended_at = ? WHERE id = ?",
                (json.dumps(v["missing"]), v["last_seen"], v["ended_at"], v["id"]),
            )
            self._db.commit()

    def add_snapshot(self, violation_id: str, frame: np.ndarray, taken_at: float | None = None) -> str:
        taken_at = taken_at or time.time()
        stamp = datetime.fromtimestamp(taken_at).strftime("%Y%m%d-%H%M%S")
        filename = f"{violation_id}_{stamp}_{int(taken_at * 1000) % 1000:03d}.jpg"
        cv2.imwrite(str(self.snapshot_dir / filename), frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
        with self._lock:
            self._db.execute(
                "INSERT INTO snapshots (violation_id, filename, taken_at) VALUES (?,?,?)",
                (violation_id, filename, taken_at),
            )
            self._db.commit()
        return filename

    def acknowledge(self, violation_id: str, acknowledged: bool, note: str | None) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE violations SET acknowledged = ?, note = COALESCE(?, note) WHERE id = ?",
                (int(acknowledged), note, violation_id),
            )
            self._db.commit()
            return cur.rowcount > 0

    def delete_violation(self, violation_id: str) -> bool:
        with self._lock:
            files = [r["filename"] for r in self._db.execute(
                "SELECT filename FROM snapshots WHERE violation_id = ?", (violation_id,))]
            cur = self._db.execute("DELETE FROM violations WHERE id = ?", (violation_id,))
            self._db.commit()
        for f in files:
            (self.snapshot_dir / f).unlink(missing_ok=True)
        return cur.rowcount > 0

    # ---- reads --------------------------------------------------------------------------

    def snapshot_path(self, filename: str) -> Path | None:
        """Resolve a snapshot filename, refusing anything outside the snapshot folder."""
        p = (self.snapshot_dir / filename).resolve()
        if p.parent != self.snapshot_dir.resolve() or not p.is_file():
            return None
        return p

    def _row(self, r: sqlite3.Row, snapshots: list[dict] | None = None) -> dict:
        d = dict(r)
        d["missing"] = json.loads(d["missing"])
        d["acknowledged"] = bool(d["acknowledged"])
        end = d["ended_at"] or d["last_seen"]
        d["duration"] = round(end - d["started_at"], 1)
        d["ongoing"] = d["ended_at"] is None
        if snapshots is not None:
            d["snapshots"] = snapshots
        return d

    def _where(self, camera_id, item, since, until, ongoing, acknowledged):
        clauses, args = [], []
        if camera_id:
            clauses.append("camera_id = ?")
            args.append(camera_id)
        if item:
            clauses.append("EXISTS (SELECT 1 FROM json_each(missing) WHERE value = ?)")
            args.append(item)
        if since is not None:
            clauses.append("started_at >= ?")
            args.append(since)
        if until is not None:
            clauses.append("started_at < ?")
            args.append(until)
        if ongoing is not None:
            clauses.append("ended_at IS NULL" if ongoing else "ended_at IS NOT NULL")
        if acknowledged is not None:
            clauses.append("acknowledged = ?")
            args.append(int(acknowledged))
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", args

    def list_violations(
        self,
        camera_id: str | None = None,
        item: str | None = None,
        since: float | None = None,
        until: float | None = None,
        ongoing: bool | None = None,
        acknowledged: bool | None = None,
        sort: str = "started_at",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        if sort not in SORT_COLUMNS:
            raise ValueError(f"sort must be one of {sorted(SORT_COLUMNS)}")
        direction = "ASC" if order.lower() == "asc" else "DESC"
        sort_sql = "(COALESCE(ended_at, last_seen) - started_at)" if sort == "duration" else sort
        where, args = self._where(camera_id, item, since, until, ongoing, acknowledged)
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) FROM violations{where}", args).fetchone()[0]
            rows = self._db.execute(
                f"SELECT v.*, (SELECT COUNT(*) FROM snapshots s WHERE s.violation_id = v.id) AS snapshot_count,"
                f" (SELECT filename FROM snapshots s WHERE s.violation_id = v.id ORDER BY taken_at LIMIT 1) AS thumbnail"
                f" FROM violations v{where} ORDER BY {sort_sql} {direction} LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
        return {"total": total, "items": [self._row(r) for r in rows]}

    def get_violation(self, violation_id: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM violations WHERE id = ?", (violation_id,)).fetchone()
            if r is None:
                return None
            snaps = [dict(s) for s in self._db.execute(
                "SELECT filename, taken_at FROM snapshots WHERE violation_id = ? ORDER BY taken_at",
                (violation_id,))]
        return self._row(r, snaps)

    def stats(self, now: float | None = None) -> dict:
        now = now or time.time()
        midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        day_ago = now - 86400
        with self._lock:
            q = self._db.execute
            today = q("SELECT COUNT(*) FROM violations WHERE started_at >= ?", (midnight,)).fetchone()[0]
            ongoing = q("SELECT COUNT(*) FROM violations WHERE ended_at IS NULL").fetchone()[0]
            total = q("SELECT COUNT(*) FROM violations").fetchone()[0]
            unack = q("SELECT COUNT(*) FROM violations WHERE acknowledged = 0").fetchone()[0]
            by_item = {r[0]: r[1] for r in q(
                "SELECT j.value, COUNT(*) FROM violations, json_each(violations.missing) j"
                " WHERE started_at >= ? GROUP BY j.value", (day_ago,))}
            by_camera = {r[0]: r[1] for r in q(
                "SELECT camera_name, COUNT(*) FROM violations WHERE started_at >= ? GROUP BY camera_name",
                (day_ago,))}
            hourly = [0] * 24
            for (ts,) in q("SELECT started_at FROM violations WHERE started_at >= ?", (day_ago,)):
                hourly[min(23, int((ts - day_ago) // 3600))] += 1
        return {
            "today": today,
            "ongoing": ongoing,
            "total": total,
            "unacknowledged": unack,
            "last_24h_by_item": by_item,
            "last_24h_by_camera": by_camera,
            "last_24h_hourly": hourly,
        }

    def export_csv(self, **filters) -> str:
        data = self.list_violations(**filters, limit=1_000_000, offset=0)["items"]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "camera_id", "camera_name", "worker", "missing", "started", "ended",
                    "duration_s", "acknowledged", "note", "snapshots"])
        iso = lambda t: datetime.fromtimestamp(t).isoformat(timespec="seconds") if t else ""  # noqa: E731
        for v in data:
            w.writerow([v["id"], v["camera_id"], v["camera_name"], v["worker_key"],
                        "|".join(v["missing"]), iso(v["started_at"]), iso(v["ended_at"]),
                        v["duration"], v["acknowledged"], v["note"] or "", v["snapshot_count"]])
        return buf.getvalue()
