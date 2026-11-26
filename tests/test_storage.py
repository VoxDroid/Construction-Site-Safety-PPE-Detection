"""Storage: schema migration, analytics, retention, bulk actions and clips."""

import json
import sqlite3
import time
from datetime import date, datetime, timedelta

import numpy as np

from ppe_guard.storage import Storage


def add(storage, vid, *, camera="cam", missing=("helmet",), kind="ppe", zone=None, started=None,
        duration=10.0, ended=True):
    started = started or time.time() - 3600
    v = {"id": vid, "kind": kind, "worker_key": "t1", "missing": list(missing), "zone_id": zone,
         "zone_name": zone.title() if zone else None, "started_at": started,
         "last_seen": started + duration, "ended_at": started + duration if ended else None}
    storage.open_violation(v, camera, camera.title())
    storage.update_violation(v)
    return v


def test_migrates_a_2_0_database(tmp_path):
    db = sqlite3.connect(tmp_path / "ppe_guard.db")
    db.executescript("""
        CREATE TABLE violations (id TEXT PRIMARY KEY, camera_id TEXT NOT NULL, camera_name TEXT NOT NULL,
            worker_key TEXT NOT NULL, missing TEXT NOT NULL, started_at REAL NOT NULL, last_seen REAL NOT NULL,
            ended_at REAL, acknowledged INTEGER NOT NULL DEFAULT 0, note TEXT);
        INSERT INTO violations VALUES ('old1','gate','Gate','t1','["vest"]',1000,1010,1010,0,NULL);
    """)
    db.commit()
    db.close()
    s = Storage(tmp_path)
    v = s.get_violation("old1")
    assert v["kind"] == "ppe" and v["zone_name"] is None and v["clip"] is None
    assert v["missing"] == ["vest"]


def test_filters_by_kind_and_zone_csv(tmp_path):
    s = Storage(tmp_path)
    add(s, "a1")
    add(s, "z1", kind="zone", missing=(), zone="pit")
    assert s.list_violations(kind="zone")["total"] == 1
    assert s.list_violations(kind="ppe")["items"][0]["id"] == "a1"
    csv = s.export_csv()
    header, *rows = csv.strip().splitlines()
    assert header.startswith("id,type,camera_id") and "zone" in header and header.endswith("clip")
    assert any(",zone," in r and "Pit" in r for r in rows)


def test_bulk_ack_and_delete_remove_files(tmp_path):
    s = Storage(tmp_path)
    for i in range(3):
        add(s, f"v{i}")
    snap = s.add_snapshot("v0", np.zeros((20, 20, 3), np.uint8))
    (s.clip_dir / "v0.mp4").write_bytes(b"x")
    s.set_clip("v0", "v0.mp4")
    assert s.acknowledge_many(["v0", "v1", "nope"]) == 2
    assert s.list_violations(acknowledged=True)["total"] == 2
    assert s.delete_many(["v0", "v2"]) == 2
    assert not (s.snapshot_dir / snap).exists() and not (s.clip_dir / "v0.mp4").exists()
    assert s.list_violations()["total"] == 1
    assert s.acknowledge_many([]) == 0 and s.delete_many([]) == 0


def test_clip_for_deleted_violation_is_discarded(tmp_path):
    s = Storage(tmp_path)
    (s.clip_dir / "gone.mp4").write_bytes(b"x")
    s.set_clip("gone", "gone.mp4")
    assert not (s.clip_dir / "gone.mp4").exists()
    assert s.clip_path("../ppe_guard.db") is None


def test_purge_keeps_recent_and_ongoing(tmp_path):
    s = Storage(tmp_path)
    old = time.time() - 40 * 86400
    add(s, "old", started=old)
    add(s, "old-open", started=old, ended=False)
    add(s, "new")
    s.add_activity("cam", old, 100, 10, 2)
    assert s.purge(time.time() - 30 * 86400) == 1
    ids = {v["id"] for v in s.list_violations()["items"]}
    assert ids == {"old-open", "new"}


def test_activity_upserts_per_hour(tmp_path):
    s = Storage(tmp_path)
    t = datetime.now().replace(minute=5).timestamp()
    s.add_activity("cam", t, 60, 6, 2)
    s.add_activity("cam", t + 60, 60, 0, 3)
    s.add_activity("cam", t, 0, 0, 0)  # nothing to record
    row = s._db.execute("SELECT * FROM activity").fetchall()
    assert len(row) == 1 and row[0]["worker_seconds"] == 120 and row[0]["peak_workers"] == 3


def test_analytics_aggregates(tmp_path):
    s = Storage(tmp_path)
    today = date.today()
    noon = datetime.combine(today, datetime.min.time()).timestamp() + 12 * 3600
    yesterday_noon = noon - 86400
    add(s, "a", camera="gate", missing=("helmet", "vest"), started=noon, duration=4)
    add(s, "b", camera="gate", missing=("vest",), started=noon + 60, duration=8)
    add(s, "c", camera="yard", kind="zone", missing=(), zone="pit", started=yesterday_noon, duration=20)
    s.acknowledge("a", True, None)
    s.add_activity("gate", noon, 3600, 360, 4)
    s.add_activity("yard", yesterday_noon, 1800, 0, 1)

    a = s.analytics(today - timedelta(days=6), 7)
    t = a["totals"]
    assert t["violations"] == 3 and t["ppe"] == 2 and t["zone"] == 1
    assert t["acknowledged_rate"] == round(1 / 3, 4)
    assert t["median_duration"] == 8 and t["avg_duration"] == round(32 / 3, 1)
    assert t["worker_hours"] == 1.5 and t["compliance_rate"] == round(1 - 360 / 5400, 4)
    assert t["peak_workers"] == 4
    assert a["by_item"] == {"helmet": 1, "vest": 2, "mask": 0, "zone": 1}
    assert len(a["daily"]) == 7 and a["daily"][-1]["date"] == today.isoformat()
    assert a["daily"][-1]["violations"] == 2 and a["daily"][-1]["compliance_rate"] == 0.9
    assert a["daily"][-2]["zone"] == 1
    assert a["daily"][0]["compliance_rate"] is None
    assert sum(map(sum, a["heatmap"])) == 3
    assert a["heatmap"][today.weekday()][12] == 2
    assert [c["camera_id"] for c in a["by_camera"]] == ["gate", "yard"]
    assert a["by_zone"] == [{"zone": "Pit", "violations": 1}]

    only_yard = s.analytics(today - timedelta(days=6), 7, camera_id="yard")
    assert only_yard["totals"]["violations"] == 1 and only_yard["totals"]["worker_hours"] == 0.5
    json.dumps(a)  # serialisable


def test_stats_include_zone_and_compliance(tmp_path):
    s = Storage(tmp_path)
    add(s, "z", kind="zone", missing=(), zone="pit", started=time.time() - 60)
    s.add_activity("cam", time.time(), 100, 25, 1)
    st = s.stats()
    assert st["last_24h_by_item"].get("zone") == 1
    assert st["compliance_today"] == 0.75
