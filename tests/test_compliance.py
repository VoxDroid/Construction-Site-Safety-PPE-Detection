from ppe_guard.compliance import (
    ComplianceEngine,
    Detection,
    ItemStatus,
    PPEItem,
    WorkerState,
    associate,
    classify,
    evaluate_still,
)
from ppe_guard.config import ComplianceSettings

PERSON_A = (100, 100, 200, 400)  # x1, y1, x2, y2
PERSON_B = (300, 100, 400, 400)
HEAD_A = (125, 95, 175, 140)
TORSO_A = (110, 170, 190, 280)
HEAD_B = (325, 95, 375, 140)
TORSO_B = (310, 170, 390, 280)


def person(box, tid=None):
    return Detection("Person", 0.9, box, tid)


def item(cls, box, conf=0.8):
    return Detection(cls, conf, box)


def test_classify_dataset_names_and_aliases():
    assert classify("Hardhat") == ("worn", PPEItem.HELMET)
    assert classify("NO-Hardhat") == ("missing", PPEItem.HELMET)
    assert classify("NO-Safety Vest") == ("missing", PPEItem.VEST)  # broken in v1 multi mode
    assert classify("Safety Vest") == ("worn", PPEItem.VEST)
    assert classify("no_mask") == ("missing", PPEItem.MASK)
    assert classify("Person") == ("person", None)
    assert classify("Safety Cone") == ("other", None)
    assert classify("machinery") == ("other", None)


def test_items_are_assigned_to_the_right_person():
    obs = associate([
        person(PERSON_A, 1), person(PERSON_B, 2),
        item("Hardhat", HEAD_A), item("Safety Vest", TORSO_A),
        item("NO-Hardhat", HEAD_B), item("Safety Vest", TORSO_B),
    ])
    by_key = {o.key: o for o in obs}
    assert by_key["t1"].items[PPEItem.HELMET] is ItemStatus.WORN
    assert by_key["t1"].items[PPEItem.VEST] is ItemStatus.WORN
    assert by_key["t2"].items[PPEItem.HELMET] is ItemStatus.MISSING
    assert by_key["t2"].items[PPEItem.VEST] is ItemStatus.WORN
    assert by_key["t1"].items[PPEItem.MASK] is ItemStatus.UNKNOWN


def test_helmet_at_feet_is_not_counted():
    # a hard hat lying on the ground inside the person box should not count as worn
    obs = associate([person(PERSON_A), item("Hardhat", (130, 360, 170, 395))])
    assert obs[0].items[PPEItem.HELMET] is ItemStatus.UNKNOWN


def test_unassigned_no_helmet_creates_synthetic_worker():
    obs = associate([Detection("NO-Hardhat", 0.7, (600, 50, 650, 100), 9)])
    assert len(obs) == 1
    assert obs[0].synthetic and obs[0].key == "u9"
    assert obs[0].items[PPEItem.HELMET] is ItemStatus.MISSING


def test_higher_confidence_evidence_wins():
    obs = associate([person(PERSON_A), item("Hardhat", HEAD_A, 0.4), item("NO-Hardhat", HEAD_A, 0.9)])
    assert obs[0].items[PPEItem.HELMET] is ItemStatus.MISSING


def test_still_image_verdicts():
    res = evaluate_still(
        [person(PERSON_A), item("NO-Hardhat", HEAD_A), item("Safety Vest", TORSO_A),
         person(PERSON_B), item("Hardhat", HEAD_B), item("Safety Vest", TORSO_B)],
        required=[PPEItem.HELMET, PPEItem.VEST],
    )
    states = sorted(w.state.value for w in res.workers)
    assert states == ["compliant", "violation"]
    assert res.violating


def _frames(engine, dets, start, end, fps=10):
    """Feed the same detections for [start, end) seconds; return all results."""
    out, n = [], int((end - start) * fps)
    for i in range(n):
        out.append(engine.update(dets, now=start + i / fps))
    return out


def _events(results):
    return [e for r in results for e in r.events]


def test_violation_needs_to_persist_before_opening():
    eng = ComplianceEngine(ComplianceSettings(violation_delay=2.0))
    bad = [person(PERSON_A, 1), item("NO-Hardhat", HEAD_A), item("Safety Vest", TORSO_A)]
    early = _frames(eng, bad, 0, 1.0)
    assert not _events(early)
    assert early[-1].workers[0].state is WorkerState.PENDING
    later = _frames(eng, bad, 1.0, 3.0)
    opened = [e for e in _events(later) if e.kind == "opened"]
    assert len(opened) == 1
    assert opened[0].violation.missing == {PPEItem.HELMET}
    assert opened[0].snapshot


def test_single_glitch_frame_does_not_open_violation():
    eng = ComplianceEngine(ComplianceSettings(violation_delay=2.0))
    good = [person(PERSON_A, 1), item("Hardhat", HEAD_A), item("Safety Vest", TORSO_A)]
    bad = [person(PERSON_A, 1), item("NO-Hardhat", HEAD_A), item("Safety Vest", TORSO_A)]
    results = []
    for i in range(50):  # 5 s at 10 fps, one bad frame every 10
        results.append(eng.update(bad if i % 10 == 0 else good, now=i / 10))
    assert not _events(results)


def test_violation_closes_after_compliance_and_counts_once():
    s = ComplianceSettings(violation_delay=1.0, clear_after=2.0)
    eng = ComplianceEngine(s)
    bad = [person(PERSON_A, 1), item("NO-Hardhat", HEAD_A)]
    good = [person(PERSON_A, 1), item("Hardhat", HEAD_A), item("Safety Vest", TORSO_A)]
    ev = _events(_frames(eng, bad, 0, 3)) + _events(_frames(eng, good, 3, 7))
    kinds = [e.kind for e in ev if e.kind != "updated"]
    assert kinds == ["opened", "closed"]
    closed = ev[-1].violation
    assert closed.ended_at is not None and closed.ended_at > closed.started_at


def test_violation_closes_when_worker_leaves():
    eng = ComplianceEngine(ComplianceSettings(violation_delay=1.0, lost_timeout=1.5))
    bad = [person(PERSON_A, 1), item("NO-Hardhat", HEAD_A)]
    ev = _events(_frames(eng, bad, 0, 2)) + _events(_frames(eng, [], 2, 5))
    assert [e.kind for e in ev if e.kind != "updated"] == ["opened", "closed"]


def test_two_workers_tracked_independently():
    eng = ComplianceEngine(ComplianceSettings(violation_delay=1.0))
    dets = [person(PERSON_A, 1), item("Hardhat", HEAD_A), item("Safety Vest", TORSO_A),
            person(PERSON_B, 2), item("Hardhat", HEAD_B), item("NO-Safety Vest", TORSO_B)]
    res = _frames(eng, dets, 0, 2)
    opened = [e for e in _events(res) if e.kind == "opened"]
    assert [e.violation.worker_key for e in opened] == ["t2"]
    assert opened[0].violation.missing == {PPEItem.VEST}
    states = {w.key: w.state for w in res[-1].workers}
    assert states == {"t1": WorkerState.COMPLIANT, "t2": WorkerState.VIOLATION}


def test_unrequired_items_are_ignored():
    s = ComplianceSettings(violation_delay=0.5)
    s.required_ppe.mask = False
    eng = ComplianceEngine(s)
    dets = [person(PERSON_A, 1), item("Hardhat", HEAD_A), item("Safety Vest", TORSO_A),
            item("NO-Mask", (135, 120, 165, 150))]
    assert not _events(_frames(eng, dets, 0, 2))


def test_strict_mode_flags_unseen_items():
    s = ComplianceSettings(violation_delay=1.0, treat_unseen_as_missing=True)
    eng = ComplianceEngine(s)
    dets = [person(PERSON_A, 1), item("Hardhat", HEAD_A)]  # vest never seen
    opened = [e for e in _events(_frames(eng, dets, 0, 2)) if e.kind == "opened"]
    assert opened and opened[0].violation.missing == {PPEItem.VEST}
    # without strict mode the same scene is "unknown", not a violation
    eng2 = ComplianceEngine(ComplianceSettings(violation_delay=1.0))
    res = _frames(eng2, dets, 0, 2)
    assert not _events(res)
    assert res[-1].workers[0].state is WorkerState.UNKNOWN


def test_snapshots_are_rate_limited():
    s = ComplianceSettings(violation_delay=0.5, snapshot_interval=2.0, max_snapshots=3)
    eng = ComplianceEngine(s)
    bad = [person(PERSON_A, 1), item("NO-Hardhat", HEAD_A)]
    snaps = [e for e in _events(_frames(eng, bad, 0, 20)) if e.snapshot]
    assert len(snaps) == 3


def test_reset_closes_open_violations():
    eng = ComplianceEngine(ComplianceSettings(violation_delay=0.5))
    _frames(eng, [person(PERSON_A, 1), item("NO-Hardhat", HEAD_A)], 0, 2)
    ev = eng.reset()
    assert [e.kind for e in ev] == ["closed"]
