"""API tests. They never start a camera, so no model or GPU is needed."""

import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from ppe_guard.alerts import build_payload
from ppe_guard.config import AlertSettings
from ppe_guard.server import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, autostart=False)
    with TestClient(app) as c:
        c.app_state = app.state
        yield c


def _violation(storage, vid="abc123", camera="webcam", missing=("helmet",), ago=60, ended=True):
    now = time.time()
    v = {"id": vid, "worker_key": "t1", "missing": list(missing), "started_at": now - ago,
         "last_seen": now - ago + 5, "ended_at": now - ago + 5 if ended else None}
    storage.open_violation(v, camera, camera.title())
    storage.update_violation(v)
    return v


def test_health_and_index(client):
    assert client.get("/api/health").json()["status"] == "ok"
    r = client.get("/")
    assert r.status_code == 200 and "PPE Guard" in r.text
    assert client.get("/static/js/main.js").status_code == 200


def test_settings_roundtrip_and_validation(client):
    s = client.get("/api/settings").json()
    assert s["compliance"]["required_ppe"] == {"helmet": True, "vest": True, "mask": False}
    s["compliance"]["violation_delay"] = 4
    s["compliance"]["required_ppe"]["mask"] = True
    r = client.put("/api/settings", json=s)
    assert r.status_code == 200
    assert client.get("/api/settings").json()["compliance"]["violation_delay"] == 4

    s["detection"]["confidence"] = 5  # out of range
    assert client.put("/api/settings", json=s).status_code == 422
    s["detection"]["confidence"] = 0.4
    s["alerts"]["webhook_url"] = "file:///etc/passwd"
    assert client.put("/api/settings", json=s).status_code == 422


def test_settings_persist_to_disk(tmp_path):
    with TestClient(create_app(tmp_path, autostart=False)) as c:
        s = c.get("/api/settings").json()
        s["compliance"]["clear_after"] = 9
        c.put("/api/settings", json=s)
    with TestClient(create_app(tmp_path, autostart=False)) as c:
        assert c.get("/api/settings").json()["compliance"]["clear_after"] == 9


def test_camera_crud(client):
    assert [c["id"] for c in client.get("/api/cameras").json()] == ["webcam"]
    r = client.post("/api/cameras", json={"id": "gate-1", "name": "Gate", "source": "rtsp://x/y"})
    assert r.status_code == 201 and r.json()["status"] == "stopped"
    assert client.post("/api/cameras", json={"id": "gate-1", "name": "G", "source": "0"}).status_code == 409
    assert client.post("/api/cameras", json={"id": "Bad Id!", "name": "G", "source": "0"}).status_code == 422
    r = client.put("/api/cameras/gate-1", json={"id": "gate-1", "name": "Gate 1", "source": "rtsp://x/y"})
    assert r.json()["name"] == "Gate 1"
    assert client.delete("/api/cameras/gate-1").status_code == 204
    assert client.get("/api/cameras/gate-1/frame.jpg").status_code == 404


def test_violations_list_filter_sort_and_csv(client):
    st = client.app_state.storage
    _violation(st, "v1", "webcam", ("helmet",), ago=100)
    _violation(st, "v2", "gate", ("vest", "helmet"), ago=50)
    _violation(st, "v3", "gate", ("vest",), ago=10, ended=False)

    data = client.get("/api/violations").json()
    assert data["total"] == 3 and [v["id"] for v in data["items"]] == ["v3", "v2", "v1"]
    assert client.get("/api/violations?item=vest").json()["total"] == 2
    assert client.get("/api/violations?camera_id=webcam").json()["total"] == 1
    assert client.get("/api/violations?ongoing=true").json()["items"][0]["id"] == "v3"
    assert client.get("/api/violations?sort=started_at&order=asc").json()["items"][0]["id"] == "v1"
    # sort is whitelisted: SQL can't be injected through it
    assert client.get("/api/violations?sort=id;DROP TABLE violations").status_code == 422

    csv = client.get("/api/violations/export.csv")
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert csv.text.count("\n") == 4 and "vest|helmet" in csv.text

    stats = client.get("/api/stats").json()
    assert stats["ongoing"] == 1 and stats["total"] == 3
    assert stats["last_24h_by_item"] == {"helmet": 2, "vest": 2}


def test_violation_detail_ack_delete_and_snapshots(client):
    st = client.app_state.storage
    _violation(st, "v9")
    fname = st.add_snapshot("v9", np.zeros((40, 60, 3), dtype=np.uint8))
    v = client.get("/api/violations/v9").json()
    assert v["snapshots"][0]["filename"] == fname
    assert client.get(f"/snapshots/{fname}").headers["content-type"] == "image/jpeg"

    r = client.post("/api/violations/v9/ack", json={"acknowledged": True, "note": "toolbox talk given"})
    assert r.json()["acknowledged"] and r.json()["note"] == "toolbox talk given"
    assert client.get("/api/stats").json()["unacknowledged"] == 0

    assert client.delete("/api/violations/v9").status_code == 204
    assert client.get("/api/violations/v9").status_code == 404
    assert client.get(f"/snapshots/{fname}").status_code == 404
    assert not (st.snapshot_dir / fname).exists()


@pytest.mark.parametrize("name", ["../settings.json", "..%2Fsettings.json", "..\\settings.json"])
def test_snapshot_path_traversal_blocked(client, name):
    assert client.get(f"/snapshots/{name}").status_code == 404


def test_open_violations_are_closed_on_restart(tmp_path):
    with TestClient(create_app(tmp_path, autostart=False)) as c:
        _violation(c.app.state.storage, "live", ended=False)
    with TestClient(create_app(tmp_path, autostart=False)) as c:
        v = c.get("/api/violations/live").json()
        assert not v["ongoing"] and v["ended_at"] == v["last_seen"]


def test_alert_payload_formats():
    ev = {"id": "x1", "camera_id": "c", "camera_name": "North gate", "worker_key": "t3",
          "missing": ["helmet", "vest"], "started_at": 1_700_000_000, "last_seen": 0, "ended_at": None}
    slack = build_payload(AlertSettings(webhook_url="https://h/x", format="slack",
                                        public_url="https://ppe.example.com/"), ev)
    assert "North gate" in slack["text"] and "helmet, vest" in slack["text"]
    assert "https://ppe.example.com/#/violations/x1" in slack["text"]
    assert "content" in build_payload(AlertSettings(format="discord"), ev)
    generic = build_payload(AlertSettings(), ev)
    assert generic["type"] == "ppe_violation" and generic["missing"] == ["helmet", "vest"]


def test_test_alert_requires_webhook(client):
    assert client.post("/api/alerts/test").status_code == 400
