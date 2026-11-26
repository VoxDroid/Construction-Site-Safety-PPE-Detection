"""SQLite storage: violations, snapshots, clips and worker-time used for compliance rates."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
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
CREATE INDEX IF NOT EXISTS idx_violations_camera ON violations(camera_id);
CREATE TABLE IF NOT EXISTS snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    violation_id  TEXT NOT NULL REFERENCES violations(id) ON DELETE CASCADE,
    filename      TEXT NOT NULL,
    taken_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_violation ON snapshots(violation_id);
-- Monitored worker-time per camera and hour, for compliance rates.
CREATE TABLE IF NOT EXISTS activity (
    camera_id          TEXT NOT NULL,
    hour               REAL NOT NULL,      -- unix seconds at the start of the hour
    worker_seconds     REAL NOT NULL DEFAULT 0,
    violation_seconds  REAL NOT NULL DEFAULT 0,
    peak_workers       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (camera_id, hour)
);
"""

# Columns added after 2.0.0's first schema; added in place to existing databases.
_MIGRATIONS = {
    "kind": "ALTER TABLE violations ADD COLUMN kind TEXT NOT NULL DEFAULT 'ppe'",
    "zone_id": "ALTER TABLE violations ADD COLUMN zone_id TEXT",
    "zone_name": "ALTER TABLE violations ADD COLUMN zone_name TEXT",
    "clip": "ALTER TABLE violations ADD COLUMN clip TEXT",
}

SORT_COLUMNS = {"started_at", "last_seen", "ended_at", "camera_id", "duration"}
ITEMS = ("helmet", "vest", "mask")
_ITEM_SUMS = ", ".join(f"COALESCE(SUM(instr(missing, '\"{i}\"') > 0), 0)" for i in ITEMS)


def _local_midnight(ts: float) -> float:
    return datetime.fromtimestamp(ts).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()



def _cell(value: str) -> str:
    """Stop spreadsheet apps from running text typed by users as a formula (CSV injection)."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value

class Storage:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.snapshot_dir = data_dir / "snapshots"
        self.clip_dir = data_dir / "clips"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.clip_dir.mkdir(parents=True, exist_ok=True)
        # One connection for writes (serialised by a lock), and one read connection per thread.
        # With WAL, readers never wait for the writer, so slow dashboard queries can't stall the
        # camera threads that record violations.
        self._path = data_dir / "ppe_guard.db"
        self._lock = threading.Lock()
        self._local = threading.local()
        self._readers: list[sqlite3.Connection] = []
        self._readers_lock = threading.Lock()
        self._db = sqlite3.connect(self._path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)
        cols = {r["name"] for r in self._db.execute("PRAGMA table_info(violations)")}
        for col, sql in _MIGRATIONS.items():
            if col not in cols:
                self._db.execute(sql)
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_violations_open ON violations(ended_at)")
        # Covering index: lists, stats and analytics over a time range read only the index,
        # which is ~10x faster than following 100k row pointers.
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_violations_report ON violations(started_at, camera_id, kind,"
            " acknowledged, ended_at, last_seen, missing, zone_name, camera_name)")
        self._db.execute("DROP INDEX IF EXISTS idx_violations_started")
        # Violations left open by a crash or shutdown end at their last sighting.
        self._db.execute("UPDATE violations SET ended_at = last_seen WHERE ended_at IS NULL")
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()
        with self._readers_lock:
            for c in self._readers:
                c.close()
            self._readers.clear()

    def _r(self) -> sqlite3.Connection:
        """This thread's read-only connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA query_only=ON")
            self._local.conn = conn
            with self._readers_lock:
                self._readers.append(conn)
        return conn

    # ---- writes -------------------------------------------------------------------------

    def open_violation(self, v: dict, camera_id: str, camera_name: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO violations (id, camera_id, camera_name, worker_key, missing,"
                " started_at, last_seen, kind, zone_id, zone_name) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (v["id"], camera_id, camera_name, v["worker_key"], json.dumps(v["missing"]),
                 v["started_at"], v["last_seen"], v.get("kind", "ppe"), v.get("zone_id"),
                 v.get("zone_name")),
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

    def set_clip(self, violation_id: str, filename: str) -> None:
        with self._lock:
            cur = self._db.execute("UPDATE violations SET clip = ? WHERE id = ?", (filename, violation_id))
            self._db.commit()
        if cur.rowcount == 0:  # the violation was deleted while the clip was being written
            (self.clip_dir / filename).unlink(missing_ok=True)

    def add_activity(self, camera_id: str, at: float, worker_seconds: float,
                     violation_seconds: float, peak_workers: int) -> None:
        if worker_seconds <= 0 and peak_workers <= 0:
            return
        hour = at - at % 3600
        with self._lock:
            self._db.execute(
                "INSERT INTO activity (camera_id, hour, worker_seconds, violation_seconds, peak_workers)"
                " VALUES (?,?,?,?,?) ON CONFLICT(camera_id, hour) DO UPDATE SET"
                " worker_seconds = worker_seconds + excluded.worker_seconds,"
                " violation_seconds = violation_seconds + excluded.violation_seconds,"
                " peak_workers = MAX(peak_workers, excluded.peak_workers)",
                (camera_id, hour, worker_seconds, violation_seconds, peak_workers),
            )
            self._db.commit()

    def acknowledge(self, violation_id: str, acknowledged: bool, note: str | None) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE violations SET acknowledged = ?, note = COALESCE(?, note) WHERE id = ?",
                (int(acknowledged), note, violation_id),
            )
            self._db.commit()
            return cur.rowcount > 0

    def acknowledge_many(self, ids: list[str], acknowledged: bool = True) -> int:
        if not ids:
            return 0
        with self._lock:
            cur = self._db.execute(
                f"UPDATE violations SET acknowledged = ? WHERE id IN ({','.join('?' * len(ids))})",
                [int(acknowledged), *ids],
            )
            self._db.commit()
            return cur.rowcount

    def _delete_where(self, where: str, args: list) -> int:
        with self._lock:
            files = [r["filename"] for r in self._db.execute(
                f"SELECT filename FROM snapshots WHERE violation_id IN (SELECT id FROM violations {where})",
                args)]
            clips = [r["clip"] for r in self._db.execute(
                f"SELECT clip FROM violations {where} AND clip IS NOT NULL" if where else
                "SELECT clip FROM violations WHERE clip IS NOT NULL", args)]
            cur = self._db.execute(f"DELETE FROM violations {where}", args)
            self._db.commit()
        for f in files:
            (self.snapshot_dir / f).unlink(missing_ok=True)
        for c in clips:
            (self.clip_dir / c).unlink(missing_ok=True)
        return cur.rowcount

    def delete_violation(self, violation_id: str) -> bool:
        return self._delete_where("WHERE id = ?", [violation_id]) > 0

    def delete_many(self, ids: list[str]) -> int:
        if not ids:
            return 0
        return self._delete_where(f"WHERE id IN ({','.join('?' * len(ids))})", list(ids))

    def purge(self, before: float) -> int:
        """Delete finished violations (with their files) and activity older than ``before``."""
        n = self._delete_where("WHERE ended_at IS NOT NULL AND started_at < ?", [before])
        with self._lock:
            self._db.execute("DELETE FROM activity WHERE hour < ?", (before - 3600,))
            self._db.commit()
        return n

    # ---- reads --------------------------------------------------------------------------

    def _safe(self, folder: Path, filename: str) -> Path | None:
        p = (folder / filename).resolve()
        if p.parent != folder.resolve() or not p.is_file():
            return None
        return p

    def snapshot_path(self, filename: str) -> Path | None:
        """Resolve a snapshot filename, refusing anything outside the snapshot folder."""
        return self._safe(self.snapshot_dir, filename)

    def clip_path(self, filename: str) -> Path | None:
        return self._safe(self.clip_dir, filename)

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

    def _where(self, camera_id=None, item=None, since=None, until=None, ongoing=None,
               acknowledged=None, kind=None):
        clauses, args = [], []
        if camera_id:
            clauses.append("camera_id = ?")
            args.append(camera_id)
        if item:
            # `missing` is a JSON list of plain words; a string search avoids parsing it per row.
            clauses.append("instr(missing, ?) > 0")
            args.append(json.dumps(item))
        if kind:
            clauses.append("kind = ?")
            args.append(kind)
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
        kind: str | None = None,
        sort: str = "started_at",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        if sort not in SORT_COLUMNS:
            raise ValueError(f"sort must be one of {sorted(SORT_COLUMNS)}")
        direction = "ASC" if order.lower() == "asc" else "DESC"
        sort_sql = "(COALESCE(ended_at, last_seen) - started_at)" if sort == "duration" else sort
        where, args = self._where(camera_id, item, since, until, ongoing, acknowledged, kind)
        db = self._r()
        total = db.execute(f"SELECT COUNT(*) FROM violations{where}", args).fetchone()[0]
        rows = db.execute(
            f"SELECT v.*, (SELECT COUNT(*) FROM snapshots s WHERE s.violation_id = v.id) AS snapshot_count,"
            f" (SELECT filename FROM snapshots s WHERE s.violation_id = v.id ORDER BY taken_at LIMIT 1) AS thumbnail"
            f" FROM violations v{where} ORDER BY {sort_sql} {direction}, id LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
        return {"total": total, "items": [self._row(r) for r in rows]}

    def get_violation(self, violation_id: str) -> dict | None:
        db = self._r()
        r = db.execute("SELECT * FROM violations WHERE id = ?", (violation_id,)).fetchone()
        if r is None:
            return None
        snaps = [dict(s) for s in db.execute(
            "SELECT filename, taken_at FROM snapshots WHERE violation_id = ? ORDER BY taken_at",
            (violation_id,))]
        return self._row(r, snaps)

    def stats(self, now: float | None = None) -> dict:
        now = now or time.time()
        midnight = _local_midnight(now)
        day_ago = now - 86400
        q = self._r().execute
        today = q("SELECT COUNT(*) FROM violations WHERE started_at >= ?", (midnight,)).fetchone()[0]
        ongoing = q("SELECT COUNT(*) FROM violations WHERE ended_at IS NULL").fetchone()[0]
        total = q("SELECT COUNT(*) FROM violations").fetchone()[0]
        unack = q("SELECT COUNT(*) FROM violations WHERE acknowledged = 0").fetchone()[0]
        counts = q(f"SELECT {_ITEM_SUMS}, COALESCE(SUM(kind = 'zone'), 0) FROM violations"
                   " WHERE started_at >= ?", (day_ago,)).fetchone()
        by_item = {k: n for k, n in zip(ITEMS, counts[:3], strict=True) if n}
        if counts[3]:
            by_item["zone"] = counts[3]
        by_camera = {r[0]: r[1] for r in q(
            "SELECT camera_name, COUNT(*) FROM violations WHERE started_at >= ? GROUP BY camera_name",
            (day_ago,))}
        hourly = [0] * 24
        for h, n in q("SELECT CAST((started_at - ?) / 3600 AS INTEGER), COUNT(*) FROM violations"
                      " WHERE started_at >= ? GROUP BY 1", (day_ago, day_ago)):
            hourly[min(23, max(0, h))] += n
        ws, vs = q("SELECT COALESCE(SUM(worker_seconds),0), COALESCE(SUM(violation_seconds),0)"
                   " FROM activity WHERE hour >= ?", (midnight - 3600,)).fetchone()
        return {
            "today": today,
            "ongoing": ongoing,
            "total": total,
            "unacknowledged": unack,
            "compliance_today": round(1 - vs / ws, 4) if ws > 0 else None,
            "last_24h_by_item": by_item,
            "last_24h_by_camera": by_camera,
            "last_24h_hourly": hourly,
        }

    # ---- analytics ----------------------------------------------------------------------

    def analytics(self, start: date, days: int, camera_id: str | None = None) -> dict:
        """Aggregates for ``days`` local calendar days starting at ``start``.

        SQL groups violations into 15-minute buckets (cheap integer maths on the covering index);
        Python maps the buckets to local days and hours, which also handles daylight saving and
        half-hour time zones.
        """
        since = datetime.combine(start, datetime.min.time()).timestamp()
        until = datetime.combine(start + timedelta(days=days), datetime.min.time()).timestamp()
        where, args = self._where(camera_id=camera_id, since=since, until=until)
        cam_sql, cam_args = ("AND camera_id = ?", [camera_id]) if camera_id else ("", [])
        q = self._r().execute

        day_keys = [(start + timedelta(days=i)).isoformat() for i in range(days)]
        daily = {k: {"date": k, "violations": 0, "helmet": 0, "vest": 0, "mask": 0, "zone": 0,
                     "worker_seconds": 0.0, "violation_seconds": 0.0} for k in day_keys}
        heat = [[0] * 24 for _ in range(7)]  # weekday (Mon=0) x hour, local time
        items = dict.fromkeys(ITEMS, 0)
        for bucket, n, zone, helmet, vest, mask in q(
                f"SELECT CAST(started_at / 900 AS INTEGER), COUNT(*), SUM(kind = 'zone'), {_ITEM_SUMS}"
                f" FROM violations{where} GROUP BY 1", args):
            local = datetime.fromtimestamp(bucket * 900)
            d = daily.get(local.date().isoformat())
            if d is None:
                continue
            d["violations"] += n
            d["zone"] += zone
            for k, v in (("helmet", helmet), ("vest", vest), ("mask", mask)):
                d[k] += v
                items[k] += v
            heat[local.weekday()][local.hour] += n

        n_total, n_zone, acked, avg = q(
            f"SELECT COUNT(*), COALESCE(SUM(kind = 'zone'), 0), COALESCE(SUM(acknowledged), 0),"
            f" AVG(CASE WHEN ended_at IS NOT NULL THEN ended_at - started_at END) FROM violations{where}",
            args).fetchone()
        closed = where + (" AND " if where else " WHERE ") + "ended_at IS NOT NULL"
        n_closed = q(f"SELECT COUNT(*) FROM violations{closed}", args).fetchone()[0]
        median = None
        if n_closed:  # sorted inside SQLite, which doesn't hold Python's GIL
            median = q(f"SELECT ended_at - started_at AS d FROM violations{closed} ORDER BY d LIMIT 1 OFFSET ?",
                       [*args, n_closed // 2]).fetchone()[0]

        cams: dict[str, dict] = {}

        def cam(cid: str, name: str | None = None) -> dict:
            c = cams.setdefault(cid, {"camera_id": cid, "camera_name": name or cid, "violations": 0,
                                      "worker_seconds": 0.0, "violation_seconds": 0.0})
            if name:
                c["camera_name"] = name
            return c

        for cid, name, n in q(f"SELECT camera_id, MAX(camera_name), COUNT(*) FROM violations{where}"
                              " GROUP BY camera_id", args):
            cam(cid, name)["violations"] = n
        zones = [{"zone": z or "?", "violations": n} for z, n in q(
            f"SELECT zone_name, COUNT(*) FROM violations{where}{' AND' if where else ' WHERE'} kind = 'zone'"
            " GROUP BY zone_name ORDER BY 2 DESC", args)]

        peak = 0
        for d, cid, ws, vs, pk in q(
                "SELECT date(hour, 'unixepoch', 'localtime'), camera_id, SUM(worker_seconds),"
                f" SUM(violation_seconds), MAX(peak_workers) FROM activity WHERE hour >= ? AND hour < ? {cam_sql}"
                " GROUP BY 1, 2", [since, until, *cam_args]):
            if d not in daily:
                continue
            daily[d]["worker_seconds"] += ws
            daily[d]["violation_seconds"] += vs
            c = cam(cid)
            c["worker_seconds"] += ws
            c["violation_seconds"] += vs
            peak = max(peak, pk)
        if cams:
            names = dict(q(f"SELECT camera_id, MAX(camera_name) FROM violations WHERE camera_id IN"
                           f" ({','.join('?' * len(cams))}) GROUP BY camera_id", list(cams)).fetchall())
            for cid, c in cams.items():
                if c["camera_name"] == cid and cid in names:
                    c["camera_name"] = names[cid]

        def rate(ws: float, vs: float) -> float | None:
            return round(1 - vs / ws, 4) if ws > 0 else None

        for d in daily.values():
            d["compliance_rate"] = rate(d["worker_seconds"], d["violation_seconds"])
            d["worker_hours"] = round(d.pop("worker_seconds") / 3600, 2)
            d.pop("violation_seconds")
        ws = sum(c["worker_seconds"] for c in cams.values())
        vs = sum(c["violation_seconds"] for c in cams.values())
        for c in cams.values():
            c["compliance_rate"] = rate(c["worker_seconds"], c["violation_seconds"])
            c["worker_hours"] = round(c.pop("worker_seconds") / 3600, 2)
            c.pop("violation_seconds")
        return {
            "start": start.isoformat(),
            "days": days,
            "camera_id": camera_id,
            "totals": {
                "violations": n_total,
                "ppe": n_total - n_zone,
                "zone": n_zone,
                "acknowledged_rate": round(acked / n_total, 4) if n_total else None,
                "avg_duration": round(avg, 1) if avg is not None else None,
                "median_duration": round(median, 1) if median is not None else None,
                "worker_hours": round(ws / 3600, 2),
                "compliance_rate": rate(ws, vs),
                "peak_workers": peak,
            },
            "by_item": {**items, "zone": n_zone},
            "daily": list(daily.values()),
            "heatmap": heat,
            "by_camera": sorted(cams.values(), key=lambda c: -c["violations"]),
            "by_zone": zones,
        }

    def export_csv(self, **filters) -> str:
        data = self.list_violations(**filters, limit=1_000_000, offset=0)["items"]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "type", "camera_id", "camera_name", "worker", "missing", "zone", "started",
                    "ended", "duration_s", "acknowledged", "note", "snapshots", "clip"])
        iso = lambda t: datetime.fromtimestamp(t).isoformat(timespec="seconds") if t else ""  # noqa: E731
        for v in data:
            w.writerow([v["id"], v["kind"], v["camera_id"], _cell(v["camera_name"]), _cell(v["worker_key"]),
                        "|".join(v["missing"]), _cell(v["zone_name"] or ""), iso(v["started_at"]),
                        iso(v["ended_at"]), v["duration"], v["acknowledged"], _cell(v["note"] or ""),
                        v["snapshot_count"], v["clip"] or ""])
        return buf.getvalue()
