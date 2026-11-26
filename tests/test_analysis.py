"""Analyze uploads (image and video) through the API, with a fake detector."""

import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from helpers import make_video
from ppe_guard.server import create_app


@pytest.fixture
def client(tmp_path, fake_detector):
    with TestClient(create_app(tmp_path / "data", autostart=False)) as c:
        yield c


def jpeg(bare: bool) -> bytes:
    img = np.full((240, 320, 3), 60, np.uint8)
    if bare:
        img[:8, :8] = 255
    return cv2.imencode(".jpg", img)[1].tobytes()


def wait_done(c, job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/analyze/jobs/{job_id}").json()
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_image_violation_and_compliant(client):
    r = client.post("/api/analyze", files={"file": ("site.jpg", jpeg(True), "image/jpeg")})
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["status"] == "done" and j["kind"] == "image"
    res = j["result"]
    assert res["counts"] == {"workers": 1, "violations": 1, "compliant": 0, "unknown": 0}
    assert res["workers"][0]["missing"] == ["helmet"]
    img = client.get(f"/analysis/{j['id']}/{res['image']}")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"

    ok = client.post("/api/analyze", files={"file": ("ok.png", jpeg(False), "image/png")}).json()
    assert ok["result"]["counts"]["compliant"] == 1
    assert [x["id"] for x in client.get("/api/analyze/jobs").json()] == [ok["id"], j["id"]]


def test_video_job_reports_violations(client, tmp_path):
    # 10 fps: compliant for 2 s, bare-headed for 3 s, compliant for 4 s
    vid = make_video(tmp_path / "walk.avi", frames=90, bare=lambda i: 20 <= i < 50)
    r = client.post("/api/analyze", files={"file": ("walk.avi", vid.read_bytes(), "video/x-msvideo")})
    assert r.status_code == 201
    j = wait_done(client, r.json()["id"])
    assert j["status"] == "done", j
    res = j["result"]
    assert res["frames"] == 90 and res["duration"] == 9.0 and res["workers_seen"] == 1
    assert len(res["violations"]) == 1
    v = res["violations"][0]
    assert v["missing"] == ["helmet"] and 2.5 <= v["start"] <= 4.0 and v["end"] is not None
    assert 0 < res["compliance_rate"] < 1
    out = client.get(f"/analysis/{j['id']}/{res['video']}")
    assert out.status_code == 200 and len(out.content) > 1000
    assert client.get(f"/analysis/{j['id']}/{v['snapshot']}").status_code == 200
    # the upload itself is not kept or served
    assert client.get(f"/analysis/{j['id']}/input.avi").status_code == 404
    assert client.delete(f"/api/analyze/jobs/{j['id']}").status_code == 204
    assert client.get(f"/api/analyze/jobs/{j['id']}").status_code == 404


def test_rejects_bad_uploads(client):
    assert client.post("/api/analyze", files={"file": ("notes.txt", b"hi", "text/plain")}).status_code == 415
    assert client.post("/api/analyze", files={"file": ("empty.jpg", b"", "image/jpeg")}).status_code == 400
    bad = client.post("/api/analyze", files={"file": ("broken.jpg", b"not an image", "image/jpeg")}).json()
    assert bad["status"] == "failed" and "could not read" in bad["error"]
    s = client.get("/api/settings").json()
    s["analysis"]["max_upload_mb"] = 1
    client.put("/api/settings", json=s)
    big = b"\xff\xd8" + b"0" * (2 << 20)
    assert client.post("/api/analyze", files={"file": ("big.jpg", big, "image/jpeg")}).status_code == 413


@pytest.mark.parametrize("name", ["..%2Fjob.json", "job.json", "..%2F..%2Fsettings.json"])
def test_analysis_files_cannot_escape(client, name):
    j = client.post("/api/analyze", files={"file": ("a.jpg", jpeg(False), "image/jpeg")}).json()
    assert client.get(f"/analysis/{j['id']}/{name}").status_code == 404
    assert client.get(f"/analysis/nope/{name}").status_code == 404


def test_old_jobs_are_pruned(client):
    s = client.get("/api/settings").json()
    s["analysis"]["keep_jobs"] = 2
    client.put("/api/settings", json=s)
    for _ in range(4):
        client.post("/api/analyze", files={"file": ("a.jpg", jpeg(False), "image/jpeg")})
    assert len(client.get("/api/analyze/jobs").json()) <= 3  # pruned before each new job
