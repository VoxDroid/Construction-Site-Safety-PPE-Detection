"""Per-worker PPE compliance.

The detector finds people and PPE items as separate boxes. This module turns those boxes
into a verdict *per worker*:

1. **Association**: every PPE box (``Hardhat``, ``NO-Hardhat``, ``Safety Vest``, ...) is
   assigned to the person box that contains most of it, using a body-region check
   (helmets and masks near the head, vests on the torso). Overlapping workers do not
   borrow each other's equipment.
2. **Temporal smoothing**: each worker keeps a short history per PPE item. A violation
   opens only when the item has been reported missing for most of the last
   ``violation_delay`` seconds, so a single bad frame never raises an alert.
3. **Event lifecycle**: an open violation is updated while it lasts and closed once the
   worker has been compliant for ``clear_after`` seconds or has left the frame.
4. **Zones** (optional, per camera): PPE can be required only inside some areas, and
   restricted areas raise a violation when anyone stays inside them.

Everything here is plain Python with no model dependency, so it can be unit-tested.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum

from ppe_guard.config import ComplianceSettings, RequiredPPE, Zone
from ppe_guard.geometry import foot_point, point_in_polygon


class PPEItem(str, Enum):
    HELMET = "helmet"
    VEST = "vest"
    MASK = "mask"


class ItemStatus(str, Enum):
    WORN = "worn"
    MISSING = "missing"
    UNKNOWN = "unknown"


class WorkerState(str, Enum):
    COMPLIANT = "compliant"
    PENDING = "pending"  # something looks missing, waiting out the delay
    VIOLATION = "violation"
    UNKNOWN = "unknown"  # required items not visible yet
    UNMONITORED = "unmonitored"  # outside every PPE zone, so nothing is required


def _norm(name: str) -> str:
    return name.strip().lower().replace("_", " ").replace("-", " ")


# Class names from the CSS dataset plus common aliases from other PPE datasets.
_POSITIVE = {
    "hardhat": PPEItem.HELMET,
    "hard hat": PPEItem.HELMET,
    "helmet": PPEItem.HELMET,
    "safety vest": PPEItem.VEST,
    "vest": PPEItem.VEST,
    "mask": PPEItem.MASK,
    "face mask": PPEItem.MASK,
}
_NEGATIVE = {
    "no hardhat": PPEItem.HELMET,
    "no hard hat": PPEItem.HELMET,
    "no helmet": PPEItem.HELMET,
    "no safety vest": PPEItem.VEST,
    "no vest": PPEItem.VEST,
    "no mask": PPEItem.MASK,
    "no face mask": PPEItem.MASK,
}
_PERSON = {"person", "worker"}

# Where on the person box (as a fraction of its height, from the top) the centre of each
# item may lie. Generous on purpose: people bend, crouch and are partly occluded.
_REGIONS = {
    PPEItem.HELMET: (-0.15, 0.45),
    PPEItem.MASK: (-0.05, 0.50),
    PPEItem.VEST: (0.10, 0.90),
}


def classify(name: str) -> tuple[str, PPEItem | None]:
    """Return ``("person"|"worn"|"missing"|"other", item)`` for a detector class name."""
    n = _norm(name)
    if n in _PERSON:
        return "person", None
    if n in _NEGATIVE:
        return "missing", _NEGATIVE[n]
    if n in _POSITIVE:
        return "worn", _POSITIVE[n]
    return "other", None


Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Detection:
    """One detector output box in pixel ``xyxy`` coordinates."""

    cls: str
    conf: float
    box: Box
    track_id: int | None = None


def _area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _containment(inner: Box, outer: Box) -> float:
    """Fraction of ``inner`` that lies inside ``outer``."""
    ix1, iy1 = max(inner[0], outer[0]), max(inner[1], outer[1])
    ix2, iy2 = min(inner[2], outer[2]), min(inner[3], outer[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    a = _area(inner)
    return inter / a if a > 0 else 0.0


def _in_region(item: PPEItem, ppe: Box, person: Box) -> bool:
    h = person[3] - person[1]
    if h <= 0:
        return False
    cy = (ppe[1] + ppe[3]) / 2
    rel = (cy - person[1]) / h
    lo, hi = _REGIONS[item]
    return lo <= rel <= hi


@dataclass
class Observation:
    """What one frame says about one worker."""

    key: str
    box: Box
    items: dict[PPEItem, ItemStatus]
    conf: float
    synthetic: bool = False  # no person box; inferred from an unassigned NO-* detection


def associate(detections: Sequence[Detection], min_overlap: float = 0.5) -> list[Observation]:
    """Group PPE detections under the person they belong to.

    A ``NO-*`` box that matches no person still produces an observation, because the model
    sometimes finds a bare head without finding the person under it.
    """
    persons: list[Detection] = []
    ppe: list[tuple[Detection, str, PPEItem]] = []
    for d in detections:
        kind, item = classify(d.cls)
        if kind == "person":
            persons.append(d)
        elif item is not None:
            ppe.append((d, kind, item))

    obs: list[Observation] = []
    for i, p in enumerate(persons):
        key = f"t{p.track_id}" if p.track_id is not None else f"p{i + 1}"
        obs.append(
            Observation(key=key, box=p.box, items=dict.fromkeys(PPEItem, ItemStatus.UNKNOWN), conf=p.conf)
        )
    # best (confidence) evidence per (worker, item) so far
    best_conf: dict[tuple[int, PPEItem], float] = {}

    for j, (d, kind, item) in enumerate(ppe):
        best_idx, best_score = -1, 0.0
        for i, p in enumerate(persons):
            score = _containment(d.box, p.box)
            if score >= min_overlap and score > best_score and _in_region(item, d.box, p.box):
                best_idx, best_score = i, score
        status = ItemStatus.WORN if kind == "worn" else ItemStatus.MISSING
        if best_idx >= 0:
            k = (best_idx, item)
            if d.conf > best_conf.get(k, -1.0):
                best_conf[k] = d.conf
                obs[best_idx].items[item] = status
        elif status is ItemStatus.MISSING:
            key = f"u{d.track_id}" if d.track_id is not None else f"u{j + 1}"
            items = dict.fromkeys(PPEItem, ItemStatus.UNKNOWN)
            items[item] = ItemStatus.MISSING
            obs.append(Observation(key=key, box=d.box, items=items, conf=d.conf, synthetic=True))
    return obs


@dataclass
class Violation:
    id: str
    worker_key: str
    missing: set[PPEItem]
    started_at: float
    last_seen: float
    ended_at: float | None = None
    kind: str = "ppe"  # "ppe" (missing equipment) or "zone" (restricted zone entered)
    zone_id: str | None = None
    zone_name: str | None = None
    snapshots: int = 0
    last_snapshot_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "worker_key": self.worker_key,
            "missing": sorted(i.value for i in self.missing),
            "zone_id": self.zone_id,
            "zone_name": self.zone_name,
            "started_at": self.started_at,
            "last_seen": self.last_seen,
            "ended_at": self.ended_at,
        }


@dataclass
class ViolationEvent:
    kind: str  # "opened" | "updated" | "closed"
    violation: Violation
    snapshot: bool = False  # the caller should save a snapshot for this event


@dataclass
class _ItemHistory:
    samples: deque = field(default_factory=deque)  # (t, missing: bool)

    def add(self, t: float, missing: bool) -> None:
        self.samples.append((t, missing))

    def prune(self, before: float) -> None:
        while self.samples and self.samples[0][0] < before:
            self.samples.popleft()

    def missing_ratio(self) -> float:
        if not self.samples:
            return 0.0
        return sum(1 for _, m in self.samples if m) / len(self.samples)

    def span(self) -> float:
        return self.samples[-1][0] - self.samples[0][0] if len(self.samples) > 1 else 0.0


@dataclass
class _Worker:
    key: str
    first_seen: float
    last_seen: float
    box: Box
    synthetic: bool
    history: dict[PPEItem, _ItemHistory] = field(
        default_factory=lambda: {i: _ItemHistory() for i in PPEItem}
    )
    last_items: dict[PPEItem, ItemStatus] = field(default_factory=dict)
    compliant_since: float | None = None
    violation: Violation | None = None
    # restricted zones
    restricted_since: float | None = None
    outside_since: float | None = None
    intrusion: Violation | None = None


@dataclass
class WorkerView:
    """Per-frame summary of a worker, for drawing and the dashboard."""

    key: str
    box: Box
    state: WorkerState
    items: dict[PPEItem, ItemStatus]
    missing: list[PPEItem]
    violation_id: str | None
    synthetic: bool
    required: list[PPEItem] = field(default_factory=list)
    zones: list[str] = field(default_factory=list)  # names of the zones the worker stands in
    intrusion: str | None = None  # restricted zone name, while a zone violation is open

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "box": [round(v, 1) for v in self.box],
            "state": self.state.value,
            "items": {k.value: v.value for k, v in self.items.items()},
            "required": [r.value for r in self.required],
            "missing": [m.value for m in self.missing],
            "violation_id": self.violation_id,
            "synthetic": self.synthetic,
            "zones": self.zones,
            "intrusion": self.intrusion,
        }


@dataclass
class FrameResult:
    workers: list[WorkerView]
    events: list[ViolationEvent]

    @property
    def violating(self) -> bool:
        return any(w.state is WorkerState.VIOLATION for w in self.workers)


def required_items(required: RequiredPPE) -> list[PPEItem]:
    return [PPEItem(k) for k, v in required.model_dump().items() if v]


def evaluate_still(
    detections: Sequence[Detection],
    required: Sequence[PPEItem],
    min_overlap: float = 0.5,
    strict: bool = False,
) -> FrameResult:
    """Judge a single image (no time dimension): a required item seen missing is a violation."""
    views = []
    for ob in associate(detections, min_overlap):
        missing = [
            i for i in required
            if ob.items[i] is ItemStatus.MISSING
            or (strict and not ob.synthetic and ob.items[i] is ItemStatus.UNKNOWN)
        ]
        unknown = any(ob.items[i] is ItemStatus.UNKNOWN for i in required)
        state = (WorkerState.VIOLATION if missing
                 else WorkerState.UNKNOWN if unknown else WorkerState.COMPLIANT)
        views.append(WorkerView(ob.key, ob.box, state, ob.items, missing, None, ob.synthetic,
                                required=list(required)))
    return FrameResult(views, [])


# A violation opens when an item was reported missing in at least this share of the
# samples in the delay window.
_OPEN_RATIO = 0.6
# The worker counts as compliant again once the share drops below this.
_CLEAR_RATIO = 0.25
# Longest gap between two frames that still counts as continuous observation time.
_MAX_DT = 1.0


@dataclass
class Activity:
    """Worker-time seen by one engine since the last :meth:`ComplianceEngine.take_activity`."""

    worker_seconds: float = 0.0  # time workers were monitored
    violation_seconds: float = 0.0  # part of it spent with an open violation
    peak_workers: int = 0


class ComplianceEngine:
    """Stateful per-worker compliance tracker for one video stream."""

    def __init__(
        self,
        settings: ComplianceSettings | None = None,
        clock=time.time,
        zones: Sequence[Zone] = (),
    ) -> None:
        self.settings = settings or ComplianceSettings()
        self._clock = clock
        self._workers: dict[str, _Worker] = {}
        self._zones: list[Zone] = []
        self._last_update: float | None = None
        self._activity = Activity()
        self.set_zones(zones)

    @property
    def required(self) -> list[PPEItem]:
        return required_items(self.settings.required_ppe)

    @property
    def zones(self) -> list[Zone]:
        return self._zones

    def update_settings(self, settings: ComplianceSettings) -> None:
        self.settings = settings

    def set_zones(self, zones: Sequence[Zone]) -> None:
        self._zones = [z for z in zones if z.enabled]

    def open_violations(self) -> list[Violation]:
        out = []
        for w in self._workers.values():
            out += [v for v in (w.violation, w.intrusion) if v is not None]
        return out

    def take_activity(self) -> Activity:
        """Return the worker-time accumulated since the last call and start a new count."""
        a, self._activity = self._activity, Activity()
        return a

    def reset(self) -> list[ViolationEvent]:
        """Forget all workers, closing any open violations."""
        now = self._clock()
        events = []
        for w in self._workers.values():
            events += self._close_all(w, now)
        self._workers.clear()
        self._last_update = None
        return events

    # ---- zones --------------------------------------------------------------------------

    def _zones_at(self, box: Box, frame_size: tuple[int, int] | None) -> list[Zone]:
        if not self._zones or frame_size is None:
            return []
        x, y = foot_point(box, frame_size)
        return [z for z in self._zones if point_in_polygon(x, y, z.points)]

    def _required_for(self, inside: list[Zone]) -> list[PPEItem] | None:
        """Items required for a worker standing in ``inside``; ``None`` = not monitored."""
        if not any(z.kind == "ppe" for z in self._zones):
            return self.required
        here = [z for z in inside if z.kind == "ppe"]
        if not here:
            return None
        items: set[PPEItem] = set()
        for z in here:
            items |= set(required_items(z.required_ppe) if z.required_ppe else self.required)
        return [i for i in PPEItem if i in items]

    # ---- main loop ----------------------------------------------------------------------

    def update(
        self,
        detections: Iterable[Detection],
        now: float | None = None,
        frame_size: tuple[int, int] | None = None,
    ) -> FrameResult:
        """Process one frame. ``frame_size`` (width, height) is needed for zones."""
        now = self._clock() if now is None else now
        s = self.settings
        events: list[ViolationEvent] = []
        views: list[WorkerView] = []
        dt = 0.0 if self._last_update is None else min(max(now - self._last_update, 0.0), _MAX_DT)
        self._last_update = now

        for ob in associate(list(detections), s.min_overlap):
            w = self._workers.get(ob.key)
            if w is None:
                w = _Worker(ob.key, now, now, ob.box, ob.synthetic)
                self._workers[ob.key] = w
            w.last_seen, w.box, w.last_items = now, ob.box, ob.items

            for item in PPEItem:
                st = ob.items[item]
                if st is ItemStatus.UNKNOWN and s.treat_unseen_as_missing and not ob.synthetic:
                    st = ItemStatus.MISSING
                if st is not ItemStatus.UNKNOWN:
                    w.history[item].add(now, st is ItemStatus.MISSING)
                w.history[item].prune(now - max(s.violation_delay, 0.5))

            inside = self._zones_at(ob.box, frame_size)
            required = self._required_for(inside)
            view = self._evaluate(w, required or [], now, events, monitored=required is not None)
            if not w.synthetic:
                self._check_restricted(w, [z for z in inside if z.kind == "restricted"], now, events)
            if w.intrusion is not None:
                view.state = WorkerState.VIOLATION
                view.intrusion = w.intrusion.zone_name
                view.violation_id = view.violation_id or w.intrusion.id
            view.zones = [z.name for z in inside]
            views.append(view)

            if not w.synthetic and (required is not None or w.intrusion is not None):
                self._activity.worker_seconds += dt
                if view.state is WorkerState.VIOLATION:
                    self._activity.violation_seconds += dt

        self._activity.peak_workers = max(
            self._activity.peak_workers, sum(1 for v in views if not v.synthetic)
        )

        # Workers not seen this frame: close their violations after lost_timeout.
        for key in list(self._workers):
            w = self._workers[key]
            if w.last_seen == now:
                continue
            if now - w.last_seen >= s.lost_timeout:
                events += self._close_all(w, w.last_seen)
                del self._workers[key]

        return FrameResult(views, events)

    def _snapshot_due(self, v: Violation, now: float) -> bool:
        s = self.settings
        if v.snapshots < s.max_snapshots and now - v.last_snapshot_at >= s.snapshot_interval:
            v.snapshots += 1
            v.last_snapshot_at = now
            return True
        return False

    def _check_restricted(self, w: _Worker, zones: list[Zone], now: float, events: list) -> None:
        s = self.settings
        if zones:
            w.outside_since = None
            if w.intrusion is None:
                if w.restricted_since is None:
                    w.restricted_since = now
                if now - w.restricted_since >= s.violation_delay:
                    z = zones[0]
                    w.intrusion = Violation(
                        id=uuid.uuid4().hex[:12], worker_key=w.key, missing=set(),
                        started_at=now, last_seen=now, kind="zone", zone_id=z.id,
                        zone_name=z.name, snapshots=1, last_snapshot_at=now,
                    )
                    events.append(ViolationEvent("opened", w.intrusion, snapshot=True))
            else:
                w.intrusion.last_seen = now
                if self._snapshot_due(w.intrusion, now):
                    events.append(ViolationEvent("updated", w.intrusion, snapshot=True))
        else:
            w.restricted_since = None
            if w.intrusion is not None:
                w.intrusion.last_seen = now
                if w.outside_since is None:
                    w.outside_since = now
                if now - w.outside_since >= s.clear_after:
                    v, w.intrusion, w.outside_since = w.intrusion, None, None
                    v.ended_at = now
                    events.append(ViolationEvent("closed", v))

    def _evaluate(
        self, w: _Worker, required: list[PPEItem], now: float, events: list, monitored: bool = True
    ) -> WorkerView:
        s = self.settings
        delay = s.violation_delay
        observed_long_enough = now - w.first_seen >= delay * 0.8

        missing_now: list[PPEItem] = []
        pending = False
        any_unknown = False
        for item in required:
            h = w.history[item]
            ratio = h.missing_ratio()
            if not h.samples:
                any_unknown = True
                # The item went out of view mid-violation: keep the violation open rather
                # than counting "can't see it" as compliant.
                if w.violation is not None and item in w.violation.missing:
                    pending = True
                continue
            if ratio >= _OPEN_RATIO and observed_long_enough and h.span() >= delay * 0.5:
                missing_now.append(item)
            elif ratio >= _CLEAR_RATIO:
                pending = True

        if missing_now:
            w.compliant_since = None
            if w.violation is None:
                w.violation = Violation(
                    id=uuid.uuid4().hex[:12],
                    worker_key=w.key,
                    missing=set(missing_now),
                    started_at=now,
                    last_seen=now,
                    snapshots=1,
                    last_snapshot_at=now,
                )
                events.append(ViolationEvent("opened", w.violation, snapshot=True))
            else:
                v = w.violation
                v.last_seen = now
                grew = not set(missing_now) <= v.missing
                v.missing |= set(missing_now)
                snap = self._snapshot_due(v, now)
                if grew or snap:
                    events.append(ViolationEvent("updated", v, snapshot=snap))
            state = WorkerState.VIOLATION
        else:
            if w.violation is not None:
                if pending:
                    w.compliant_since = None
                elif w.compliant_since is None:
                    w.compliant_since = now
                if w.compliant_since is not None and now - w.compliant_since >= s.clear_after:
                    events.append(self._close(w, now))
            if w.violation is not None:
                state = WorkerState.VIOLATION  # still within the clear-after grace period
                w.violation.last_seen = now
            elif not monitored:
                state = WorkerState.UNMONITORED
            elif pending:
                state = WorkerState.PENDING
            elif any_unknown:
                state = WorkerState.UNKNOWN
            else:
                state = WorkerState.COMPLIANT

        return WorkerView(
            key=w.key,
            box=w.box,
            state=state,
            items={i: w.last_items.get(i, ItemStatus.UNKNOWN) for i in PPEItem},
            missing=sorted(w.violation.missing, key=lambda i: i.value) if w.violation else [],
            violation_id=w.violation.id if w.violation else None,
            synthetic=w.synthetic,
            required=list(required),
        )

    def _close(self, w: _Worker, at: float) -> ViolationEvent:
        v = w.violation
        assert v is not None
        v.ended_at = at
        w.violation = None
        w.compliant_since = None
        return ViolationEvent("closed", v)

    def _close_all(self, w: _Worker, at: float) -> list[ViolationEvent]:
        events = []
        if w.violation is not None:
            events.append(self._close(w, at))
        if w.intrusion is not None:
            v, w.intrusion, w.outside_since = w.intrusion, None, None
            v.ended_at = at
            events.append(ViolationEvent("closed", v))
        return events
