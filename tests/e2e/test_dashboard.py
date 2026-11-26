"""Browser tests of the dashboard (Playwright + Chromium).

They start a real server on a free port with a fake detector and a synthetic video, then click
through every page. Any console error or failed request fails the test.

    pip install -e ".[dev,e2e]" && playwright install chromium
    pytest tests/e2e
"""

from __future__ import annotations

import re
import socket
import threading
import time

import cv2
import numpy as np
import pytest

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect

PASSWORD = "site safety 2026"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, data_dir, password=None):
        import uvicorn

        from ppe_guard.server import create_app

        self.app = create_app(data_dir, autostart=False, password=password)
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(self.app, port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        end = time.time() + 20
        while not self.server.started and time.time() < end:
            time.sleep(0.05)
        assert self.server.started
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(20)


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # pragma: no cover - browser not installed
            pytest.skip(f"chromium not available: {e}")
        yield b
        b.close()


@pytest.fixture
def server(tmp_path, monkeypatch):
    import ppe_guard.camera
    import ppe_guard.detector
    from helpers import FakeDetector, make_video

    monkeypatch.setattr(ppe_guard.camera, "Detector", FakeDetector)
    monkeypatch.setattr(ppe_guard.detector, "Detector", FakeDetector)
    # 20 fps, looping: bare-headed for 3 s, then compliant for 3 s
    vid = make_video(tmp_path / "site.avi", frames=120, fps=20, bare=lambda i: i < 60)
    with Server(tmp_path / "data") as s:
        st = s.app.state.ctx.store
        cfg = st.get()
        cfg.general.site_name = "Test Site"
        cfg.compliance.violation_delay = 0.5
        cfg.compliance.clear_after = 0.5
        cfg.recording.pre_seconds = 1
        cfg.recording.max_seconds = 3
        from ppe_guard.config import CameraConfig

        cfg.cameras = [CameraConfig(id="gate", name="Main gate", source=str(vid), loop=True)]
        s.app.state.ctx.cameras.sync(st.set(cfg).cameras)
        yield s


@pytest.fixture
def page(browser, server):
    ctx = browser.new_context(viewport={"width": 1366, "height": 900})
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("console", lambda m: problems.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: problems.append(f"page error: {e}"))
    pg.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}") if r.status >= 500 else None)
    pg.problems = problems
    pg.base = server.url
    yield pg
    ctx.close()
    assert not problems, "\n".join(problems)


def start_camera_and_wait_for_violation(page):
    page.goto(page.base + "/#/live")
    expect(page.locator(".cam h3")).to_have_text("Main gate")
    page.get_by_role("button", name="Start", exact=True).click()
    expect(page.locator("[data-cam=gate] .pill.good")).to_have_text(re.compile("Live"))
    expect(page.locator(".chip.violation").first).to_be_visible(timeout=15000)
    expect(page.locator("#feed .feed-item").first).to_contain_text("Main gate", timeout=15000)


def test_live_camera_and_violation_review(page):
    start_camera_and_wait_for_violation(page)
    expect(page.locator("#site-name")).to_have_text("Test Site")
    expect(page.locator("#badge-unack")).to_be_visible(timeout=20000)

    # camera page
    page.locator(".cam h3 a").click()
    expect(page.locator("h1")).to_contain_text("Main gate")
    expect(page.locator("#workers table")).to_be_visible()

    # violations list and detail dialog
    page.locator(".nav a[data-nav=violations]").click()
    rows = page.locator("#rows tr[data-id]")
    expect(rows.first).to_be_visible()
    vid = rows.first.get_attribute("data-id")
    rows.first.locator("td").nth(3).click()
    dialog = page.locator("#dialog")
    expect(dialog).to_be_visible()
    expect(dialog.locator("h2")).to_contain_text("Missing hard hat")
    dialog.locator("#note").fill("Spoke to the worker")
    dialog.locator("#ack").check()
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_be_hidden()
    # new violations keep arriving, so find the reviewed one by its id
    expect(page.locator(f"#rows tr[data-id='{vid}'] .pill.good")).to_have_text(re.compile("Reviewed"))

    # a dialog belongs to its page: leaving the page by its address closes it
    page.goto(page.url.split("#")[0] + f"#/violations/{vid}")
    expect(dialog).to_be_visible()
    page.goto(page.url.split("#")[0] + "#/analytics")
    expect(dialog).to_be_hidden()
    page.locator(".nav a[data-nav=violations]").click()

    # filters and bulk actions
    page.select_option("select[name=review]", "yes")
    expect(page.locator(f"#rows tr[data-id='{vid}']")).to_be_visible()
    n = page.locator("#rows tr[data-id]").count()
    expect(page.locator("#rows tr[data-id] .pill.good")).to_have_count(n)  # all reviewed
    page.select_option("select[name=review]", "")
    page.locator("#all").check()
    expect(page.locator("#bulk")).to_be_visible()
    page.get_by_role("button", name="Mark reviewed").click()
    expect(page.locator("#bulk")).to_be_hidden()


def test_clip_is_recorded_and_playable(page, server):
    start_camera_and_wait_for_violation(page)
    storage = server.app.state.ctx.storage
    end = time.time() + 30
    clip = None
    while time.time() < end and not clip:
        clip = next((v["clip"] for v in storage.list_violations()["items"] if v["clip"]), None)
        time.sleep(0.2)
    assert clip, "no clip was written"
    vid = next(v["id"] for v in storage.list_violations()["items"] if v["clip"])
    page.goto(page.base + f"/#/violations/{vid}")
    video = page.locator("#dialog video")
    expect(video).to_be_visible()
    page.wait_for_function("document.querySelector('#dialog video').readyState >= 1", timeout=15000)
    duration = page.evaluate("document.querySelector('#dialog video').duration")
    assert duration > 0.5


def test_zone_editor_draws_and_saves(page, server):
    page.goto(page.base + "/#/cameras/gate/zones")
    img = page.locator("#still")
    page.wait_for_function("document.getElementById('still')?.naturalWidth > 0", timeout=15000)
    page.get_by_role("button", name="No-entry zone").click()
    box = page.locator("#svg").bounding_box()
    for fx, fy in ((0.1, 0.5), (0.6, 0.5), (0.6, 0.95), (0.1, 0.95)):
        page.mouse.click(box["x"] + box["width"] * fx, box["y"] + box["height"] * fy)
    page.keyboard.press("Enter")
    expect(page.locator(".zone-item")).to_have_count(1)
    page.locator("#z-name").fill("Excavation")
    page.get_by_role("button", name="Save zones").click()
    expect(page.locator(".toast")).to_contain_text("Zones saved")
    zones = server.app.state.ctx.store.get().cameras[0].zones
    assert [(z.name, z.kind, len(z.points)) for z in zones] == [("Excavation", "restricted", 4)]
    assert img.is_visible()


def test_analytics_reports_and_theme(page, server):
    start_camera_and_wait_for_violation(page)
    page.locator(".nav a[data-nav=analytics]").click()
    expect(page.locator("#kpis .kpi").first).to_contain_text("Violations")
    expect(page.locator("#c-daily svg")).to_be_visible()
    expect(page.locator("#c-heat .heat")).to_be_visible()
    page.get_by_role("button", name="30 days").click()
    expect(page).to_have_url(re.compile(r"days=30"))
    page.locator(".nav a[data-nav=reports]").click()
    expect(page.locator("#report h1")).to_have_text("Test Site")
    page.get_by_role("button", name="Week").click()
    expect(page.locator("#r-daily svg")).to_be_visible()
    before = page.evaluate("getComputedStyle(document.body).backgroundColor")
    page.locator("#theme-btn").click()
    after = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert before != after


def test_analyze_image_upload(page, tmp_path):
    img = np.full((240, 320, 3), 60, np.uint8)
    img[:8, :8] = 255  # the fake detector reads this as a bare head
    path = tmp_path / "photo.jpg"
    cv2.imwrite(str(path), img)
    page.goto(page.base + "/#/analyze")
    page.set_input_files("#file", str(path))
    expect(page.locator("#result .notice.bad")).to_contain_text("1 worker(s) missing required PPE", timeout=20000)
    expect(page.locator("#result img")).to_be_visible()
    expect(page.locator(".job").first).to_contain_text("photo.jpg")


def test_settings_cameras_and_site_name(page, server):
    page.goto(page.base + "/#/settings/general")
    page.fill("input[name='general.site_name']", "North Yard")
    with page.expect_response(lambda r: r.url.endswith("/api/settings") and r.request.method == "PUT"):
        page.get_by_role("button", name="Save changes").click()
    expect(page.locator("#site-name")).to_have_text("North Yard")
    assert server.app.state.ctx.store.get().general.site_name == "North Yard"

    page.goto(page.base + "/#/settings/detection")
    page.locator("input[name='compliance.required_ppe.mask']").check()
    page.fill("input[name='compliance.violation_delay']", "4")
    with page.expect_response(lambda r: r.url.endswith("/api/settings") and r.request.method == "PUT"):
        page.get_by_role("button", name="Save changes").click()
    c = server.app.state.ctx.store.get().compliance
    assert c.required_ppe.mask and c.violation_delay == 4

    page.goto(page.base + "/#/settings/cameras")
    page.get_by_role("button", name="Add camera").click()
    page.fill("#cam-form input[name=name]", "Back Lot")
    expect(page.locator("#cam-form input[name=id]")).to_have_value("back-lot")
    page.fill("#cam-form input[name=source]", "rtsp://10.0.0.5/stream1")
    page.locator("#cam-form").get_by_role("button", name="Add camera").click()
    expect(page.locator("tr[data-id=back-lot]")).to_be_visible()
    page.locator("tr[data-id=back-lot] [data-del]").click()
    page.locator("#confirm-ok").click()
    expect(page.locator("tr[data-id=back-lot]")).to_have_count(0)

    for tab in ("recording", "alerts", "security", "system"):
        page.goto(page.base + f"/#/settings/{tab}")
        expect(page.locator(".tabs .active")).to_be_visible()
    expect(page.locator("dl.meta").first).to_contain_text("PPE Guard")


def test_mobile_menu(browser, server):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    pg.goto(server.url + "/#/live")
    expect(pg.locator("#sidebar")).not_to_be_in_viewport()
    pg.locator("#menu-btn").click()
    expect(pg.locator("#sidebar")).to_be_in_viewport()
    pg.locator(".nav a[data-nav=analytics]").click()
    expect(pg.locator("h1")).to_have_text("Analytics")
    expect(pg.locator("#sidebar")).not_to_be_in_viewport()
    ctx.close()


def test_login_flow(browser, tmp_path):
    with Server(tmp_path / "locked", password=PASSWORD) as s:
        ctx = browser.new_context()
        pg = ctx.new_page()
        pg.goto(s.url + "/#/violations")
        expect(pg).to_have_url(re.compile(r"/login"))
        pg.fill("#password", "wrong password")
        pg.get_by_role("button", name="Sign in").click()
        expect(pg.locator("#error")).to_contain_text("Wrong password")
        pg.fill("#password", PASSWORD)
        pg.get_by_role("button", name="Sign in").click()
        expect(pg.locator("h1")).to_have_text("Live")
        expect(pg.locator("#logout-btn")).to_be_visible()
        pg.locator("#logout-btn").click()
        expect(pg).to_have_url(re.compile(r"/login"))
        ctx.close()
