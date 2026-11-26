"""A camera running end to end on a video file, with a fake detector: violations, snapshots,
clips, worker-time and zones."""

import time

import pytest
from fastapi.testclient import TestClient

from helpers import make_video
from ppe_guard.server import create_app
from ppe_guard.video import ClipRecorder, ClipWriter, open_writer, video_format


def wait(cond, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_video_format_is_probed():
    fmt = video_format()
    assert fmt.fourcc in ("avc1", "VP80", "mp4v")


def test_open_writer_writes_a_file(tmp_path):
    import numpy as np

    w, path, fmt = open_writer(tmp_path / "out", 10, (320, 240))
    for _ in range(10):
        w.write(np.zeros((240, 320, 3), np.uint8))
    w.release()
    assert path.exists() and path.stat().st_size > 0 and path.suffix == fmt.ext


def test_clip_recorder_pre_roll_and_deadlines():
    r = ClipRecorder(fps=10, pre_seconds=1.0, max_seconds=3.0, post_seconds=0.5)
    for i in range(30):  # 3 s of frames before the violation
        r.push(i / 10, b"f%d" % i)
    r.start("v1", 3.0)
    assert r.recording == 1
    done = []
    t = 3.0
    while not done and t < 10:
        t += 0.1
        done = r.push(t, b"x")
        if abs(t - 4.0) < 0.05:
            r.stop("v1", t)
    assert done and done[0].violation_id == "v1"
    clip = done[0]
    assert clip.frames[0][0] >= 2.0 - 1e-9  # pre-roll starts 1 s before
    assert 4.4 <= t <= 4.7  # stopped at 4.0 + 0.5 s post-roll
    # max length cut-off
    r.start("v2", 10.0)
    t = 10.0
    out = []
    while not out:
        t += 0.1
        out = r.push(t, b"y")
    assert t == pytest.approx(12.0, abs=0.15)  # 3 s total minus 1 s pre-roll
    r.start("v3", 20.0)
    assert [c.violation_id for c in r.flush()] == ["v3"] and r.recording == 0


def test_clip_writer_encodes(tmp_path):
    import cv2
    import numpy as np

    saved = []
    w = ClipWriter(tmp_path, lambda vid, name: saved.append((vid, name)))
    frame = cv2.imencode(".jpg", np.zeros((200, 300, 3), np.uint8))[1].tobytes()
    from ppe_guard.video import _Clip

    assert w.submit(_Clip("abc", [(i / 8, frame) for i in range(16)], 0), 8, 200)
    assert not w.submit(_Clip("tiny", [(0, frame)], 0), 8, 200)  # too short
    w.close()
    assert saved and saved[0][0] == "abc" and (tmp_path / saved[0][1]).stat().st_size > 0


@pytest.fixture
def running(tmp_path, fake_detector):
    vid = make_video(tmp_path / "cam.avi", frames=80, fps=20, bare=lambda i: True)
    app = create_app(tmp_path / "data", autostart=False)
    with TestClient(app) as c:
        s = c.get("/api/settings").json()
        s["cameras"] = [{"id": "cam", "name": "Test cam", "source": str(vid), "loop": False}]
        s["compliance"].update(violation_delay=0.5, clear_after=0.5, lost_timeout=0.5)
        s["recording"].update(pre_seconds=1, max_seconds=3, fps=8)
        assert c.put("/api/settings", json=s).status_code == 200
        yield c, app


def test_camera_records_violation_snapshot_clip_and_activity(running):
    c, _ = running
    with c.websocket_connect("/ws") as ws:
        assert c.post("/api/cameras/cam/start").status_code == 200
        kinds = set()
        while "violation" not in kinds:
            kinds.add(ws.receive_json()["type"])
    assert wait(lambda: c.get("/api/cameras/cam").json()["status"] == "ended")
    v = c.get("/api/violations").json()["items"]
    assert len(v) == 1 and v[0]["missing"] == ["helmet"] and not v[0]["ongoing"]
    assert v[0]["snapshot_count"] >= 1
    assert c.get(f"/snapshots/{v[0]['thumbnail']}").status_code == 200
    assert wait(lambda: c.get(f"/api/violations/{v[0]['id']}").json()["clip"])
    clip = c.get(f"/api/violations/{v[0]['id']}").json()["clip"]
    r = c.get(f"/clips/{clip}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("video/")
    # range requests let browsers seek in clips
    part = c.get(f"/clips/{clip}", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100
    a = c.get("/api/analytics?days=1").json()
    assert a["totals"]["compliance_rate"] is not None and a["totals"]["compliance_rate"] < 1
    assert c.get("/api/cameras/cam/still.jpg").status_code == 200
    assert c.get("/api/stats").json()["total"] == 1


def test_camera_zones_api_and_restricted_zone(running, tmp_path):
    c, _ = running
    zones = [{"id": "pit", "name": "Pit", "kind": "restricted", "points": [[0, 0], [1, 0], [1, 1], [0, 1]]},
             {"id": "work", "name": "Work", "kind": "ppe", "points": [[0, 0], [0.1, 0], [0.1, 0.1]],
              "required_ppe": {"helmet": False, "vest": True, "mask": False}}]
    r = c.put("/api/cameras/cam/zones", json=zones)
    assert r.status_code == 200 and len(r.json()["zones"]) == 2
    bad = c.put("/api/cameras/cam/zones", json=[zones[0], zones[0]])
    assert bad.status_code == 422
    assert c.put("/api/cameras/nope/zones", json=[]).status_code == 404
    c.post("/api/cameras/cam/start")
    assert wait(lambda: c.get("/api/cameras/cam").json()["status"] == "ended")
    items = c.get("/api/violations").json()["items"]
    # the worker stands in the no-entry zone but outside the PPE zone: one zone violation only
    assert [(v["kind"], v["zone_name"]) for v in items] == [("zone", "Pit")]
    assert c.get("/api/violations?kind=zone").json()["total"] == 1


def test_still_frame_for_stopped_camera(running):
    c, _ = running
    r = c.get("/api/cameras/cam/still.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    c.post("/api/cameras", json={"id": "bad", "name": "Bad", "source": "missing-file.mp4"})
    assert c.get("/api/cameras/bad/still.jpg").status_code == 404
