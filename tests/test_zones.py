"""Zones: where PPE is required, no-entry areas, and worker-time accounting."""

import pytest
from pydantic import ValidationError

from ppe_guard.compliance import ComplianceEngine, Detection, WorkerState
from ppe_guard.config import CameraConfig, ComplianceSettings, RequiredPPE, Zone
from ppe_guard.geometry import foot_point, point_in_polygon

FRAME = (1000, 1000)
LEFT = [(0, 0), (0.5, 0), (0.5, 1), (0, 1)]
RIGHT = [(0.5, 0), (1, 0), (1, 1), (0.5, 1)]


def worker(x, tid=1, helmet=None):
    """A worker whose feet are at (x, 0.9) of a 1000x1000 frame."""
    px = x * 1000
    dets = [Detection("Person", 0.9, (px - 50, 300, px + 50, 900), tid)]
    head = (px - 20, 300, px + 20, 350)
    if helmet is True:
        dets.append(Detection("Hardhat", 0.8, head))
    elif helmet is False:
        dets.append(Detection("NO-Hardhat", 0.8, head))
    return dets


def run(engine, frames, start=0.0, dt=0.1):
    events, t = [], start
    for dets in frames:
        res = engine.update(dets, now=t, frame_size=FRAME)
        events += res.events
        t += dt
    return res, events, t


def settings(**kw):
    base = dict(required_ppe=RequiredPPE(helmet=True, vest=False), violation_delay=1.0, clear_after=1.0,
                lost_timeout=1.0)
    return ComplianceSettings(**{**base, **kw})


def test_point_in_polygon_and_foot_point():
    sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert point_in_polygon(0.5, 0.5, sq)
    assert not point_in_polygon(1.5, 0.5, sq)
    tri = [(0, 0), (1, 0), (0, 1)]
    assert point_in_polygon(0.2, 0.2, tri) and not point_in_polygon(0.8, 0.8, tri)
    assert not point_in_polygon(0.5, 0.5, [(0, 0), (1, 1)])  # degenerate
    assert foot_point((100, 200, 300, 800), (1000, 1000)) == (0.2, 0.8)


def test_zone_validation():
    with pytest.raises(ValidationError):
        Zone(id="a", name="A", points=[(0, 0), (1, 1)])  # fewer than 3 points
    with pytest.raises(ValidationError):
        Zone(id="a", name="A", points=[(0, 0), (2, 0), (0, 1)])  # outside 0-1
    with pytest.raises(ValidationError):
        Zone(id="Bad Id", name="A", points=LEFT)
    z = Zone(id="a", name="A", points=[(-0.005, 0), (1.004, 0), (0, 1)])
    assert z.points[0] == (0.0, 0.0) and z.points[1] == (1.0, 0.0)  # clamped
    with pytest.raises(ValidationError):
        CameraConfig(id="c", name="C", source="0", zones=[Zone(id="a", name="A", points=LEFT),
                                                          Zone(id="a", name="B", points=RIGHT)])


def test_ppe_only_checked_inside_ppe_zones():
    eng = ComplianceEngine(settings(), zones=[Zone(id="work", name="Work", points=LEFT)])
    # bare-headed worker on the right: outside the PPE zone, so never a violation
    res, events, _ = run(eng, [worker(0.75, helmet=False)] * 30)
    assert events == []
    assert res.workers[0].state is WorkerState.UNMONITORED
    assert res.workers[0].required == []
    # the same worker walks into the zone
    res, events, _ = run(eng, [worker(0.25, helmet=False)] * 30, start=3.0)
    assert [e.kind for e in events] == ["opened"]
    assert res.workers[0].zones == ["Work"]


def test_leaving_the_ppe_zone_closes_the_violation():
    eng = ComplianceEngine(settings(), zones=[Zone(id="work", name="Work", points=LEFT)])
    _, ev1, t = run(eng, [worker(0.25, helmet=False)] * 25)
    assert [e.kind for e in ev1] == ["opened"]
    _, ev2, _ = run(eng, [worker(0.75, helmet=False)] * 25, start=t)
    assert [e.kind for e in ev2] == ["closed"]


def test_zone_can_override_required_ppe():
    zone = Zone(id="paint", name="Paint shop", points=LEFT,
                required_ppe=RequiredPPE(helmet=False, vest=False, mask=True))
    eng = ComplianceEngine(settings(), zones=[zone])
    res, events, _ = run(eng, [worker(0.25, helmet=False)] * 30)
    assert events == []  # helmet not required in here
    assert [i.value for i in res.workers[0].required] == ["mask"]


def test_restricted_zone_raises_and_clears_intrusion():
    eng = ComplianceEngine(settings(required_ppe=RequiredPPE(helmet=False, vest=False)),
                           zones=[Zone(id="pit", name="Pit", kind="restricted", points=RIGHT)])
    _, ev, t = run(eng, [worker(0.75, helmet=True)] * 5)  # 0.4 s inside: not yet
    assert ev == []
    res, ev, t = run(eng, [worker(0.75, helmet=True)] * 10, start=t)
    assert [e.kind for e in ev] == ["opened"]
    v = ev[0].violation
    assert v.kind == "zone" and v.zone_id == "pit" and v.zone_name == "Pit" and not v.missing
    assert res.workers[0].state is WorkerState.VIOLATION and res.workers[0].intrusion == "Pit"
    _, ev, _ = run(eng, [worker(0.25, helmet=True)] * 15, start=t)
    assert [e.kind for e in ev] == ["closed"]


def test_intrusion_closes_when_worker_disappears():
    eng = ComplianceEngine(settings(), zones=[Zone(id="pit", name="Pit", kind="restricted", points=RIGHT)])
    _, ev, t = run(eng, [worker(0.75, helmet=True)] * 15)
    assert [e.kind for e in ev] == ["opened"]
    _, ev, _ = run(eng, [[]] * 15, start=t)
    assert [e.kind for e in ev] == ["closed"]


def test_disabled_zones_are_ignored():
    eng = ComplianceEngine(settings(), zones=[Zone(id="w", name="W", points=LEFT, enabled=False)])
    _, events, _ = run(eng, [worker(0.75, helmet=False)] * 30)
    assert [e.kind for e in events] == ["opened"]  # no active PPE zone: checked everywhere


def test_zones_need_frame_size():
    eng = ComplianceEngine(settings(), zones=[Zone(id="w", name="W", points=LEFT)])
    res = eng.update(worker(0.25, helmet=False), now=0.0)  # no frame_size
    assert res.workers[0].state is WorkerState.UNMONITORED


def test_activity_counts_monitored_and_violating_time():
    eng = ComplianceEngine(settings())
    run(eng, [worker(0.5, helmet=False)] * 31)  # 3 s, violation from ~1 s
    a = eng.take_activity()
    assert a.worker_seconds == pytest.approx(3.0, abs=0.01)
    assert 1.5 < a.violation_seconds < 2.5
    assert a.peak_workers == 1
    assert eng.take_activity().worker_seconds == 0  # reset after taking


def test_activity_excludes_unmonitored_workers_and_long_gaps():
    eng = ComplianceEngine(settings(), zones=[Zone(id="w", name="W", points=LEFT)])
    run(eng, [worker(0.75, helmet=True)] * 11)
    assert eng.take_activity().worker_seconds == 0
    eng2 = ComplianceEngine(settings())
    eng2.update(worker(0.5, helmet=True), now=0, frame_size=FRAME)
    eng2.update(worker(0.5, helmet=True), now=50, frame_size=FRAME)  # stream stalled
    assert eng2.take_activity().worker_seconds == 1.0  # capped
