"""What can be upgraded (by builders, the laboratory and the pet house), what
each step costs, and a preview of how an ordered upgrade queue would play out.

Table semantics (verified on game 18.647.5 / 19.32.4):
  buildings / traps: row L = cost and time to REACH level L (row 1 = build);
                     TownHallLevel on row L = Town Hall needed for level L.
  heroes, characters (troops + siege machines), spells, pets:
                     row L = cost and time to go FROM L to L+1 (last row empty;
                     live: Hog Rider row 11 = 115 000 DE, 5 d = its 11->12 dialog).
                     Heroes: RequiredTownHallLevel / RequiredHeroTavernLevel on
                     row L; troops/spells: LaboratoryLevel on row L; pets: the
                     same column holds the Pet House level.
  The resource column is filled on the first row only; later rows inherit it.
"""
from dataclasses import dataclass

TOWN_HALL = 1000001
HERO_HALL = 1000071
LABORATORY = 1000007
PET_HOUSE = 1000068
# export section -> (worker, catalog kind)
SECTIONS = {
    "buildings": ("builder", "building"), "traps": ("builder", "trap"),
    "heroes": ("builder", "hero"),
    "units": ("lab", "troop"), "spells": ("lab", "spell"), "siege_machines": ("lab", "siege"),
    "pets": ("pet_house", "pet"),
}
BUILDER_SECTIONS = tuple(SECTIONS)          # all home-village upgrade sections
FROM_ROW_TABLES = ("heroes", "characters", "spells", "pets")
WORKER_NAMES = {"builder": "Builders", "lab": "Laboratory", "pet_house": "Pet House"}


@dataclass(frozen=True)
class Step:
    data: int
    from_level: int
    to_level: int
    seconds: int
    resource: str
    cost: int


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _resource(obj, upto_row, column):
    res = ""
    for r in obj.rows[:upto_row]:
        if r.get(column):
            res = r[column]
    return res


def is_hero(obj):
    return obj.table == "heroes"


def step(gd, data_id, from_level):
    """Cost and time of one upgrade from_level -> from_level + 1, or None."""
    obj = gd.get(data_id)
    if obj is None:
        return None
    to = from_level + 1
    if obj.table in FROM_ROW_TABLES:
        row = obj.level(from_level)
        if row is None or not row.get("UpgradeCost"):
            return None
        secs = _int(row.get("UpgradeTimeH")) * 3600 + _int(row.get("UpgradeTimeM")) * 60
        return Step(data_id, from_level, to, secs,
                    _resource(obj, from_level, "UpgradeResource"), _int(row.get("UpgradeCost")))
    row = obj.level(to)
    if row is None:
        return None
    secs = (_int(row.get("BuildTimeD")) * 86400 + _int(row.get("BuildTimeH")) * 3600
            + _int(row.get("BuildTimeM")) * 60 + _int(row.get("BuildTimeS")))
    return Step(data_id, from_level, to, secs, _resource(obj, to, "BuildResource"),
                _int(row.get("BuildCost")))


def max_level(gd, data_id, th_level, hero_hall_level=None, lab_level=None, pet_house_level=None):
    """Highest level allowed at this Town Hall / Hero Hall / Laboratory /
    Pet House level."""
    obj = gd.get(data_id)
    if obj is None:
        return None
    best = 0
    for lvl, row in enumerate(obj.rows, start=1):
        if is_hero(obj):
            ok = (_int(row.get("RequiredTownHallLevel")) <= th_level and
                  (hero_hall_level is None or
                   _int(row.get("RequiredHeroTavernLevel")) <= hero_hall_level))
        elif obj.table == "pets":
            ok = pet_house_level is not None and _int(row.get("LaboratoryLevel")) <= pet_house_level
        elif obj.table in ("characters", "spells"):
            ok = lab_level is not None and _int(row.get("LaboratoryLevel")) <= lab_level
        else:
            ok = _int(row.get("TownHallLevel")) <= th_level
        if ok:
            best = lvl
    return best


def cap(gd, snapshot, data_id):
    """max_level with every gating building's level taken from the snapshot."""
    return max_level(gd, data_id, snapshot.level_of(TOWN_HALL) or 1,
                     snapshot.level_of(HERO_HALL), snapshot.level_of(LABORATORY),
                     snapshot.level_of(PET_HOUSE))


def section_of(snapshot, data_id):
    for e in snapshot.entries:
        if e.data == data_id and e.section in SECTIONS:
            return e.section
    return None


def instances(snapshot, data_id):
    """Current level of every copy of an object, counting a copy that is
    upgrading as already at its next level (the builder is on it). Returns
    (levels sorted ascending, number upgrading)."""
    levels, upgrading = [], 0
    for e in snapshot.entries:
        if e.data != data_id or e.section not in BUILDER_SECTIONS or e.level is None:
            continue
        if e.upgrading:
            upgrading += 1
            levels.append(e.level + 1)
        else:
            levels.extend([e.level] * e.count)
    return sorted(levels), upgrading


def catalog(snapshot, gd):
    """Upgradable objects in the village: one dict per type."""
    seen, out = set(), []
    for e in snapshot.entries:
        if e.section not in SECTIONS or e.data in seen or e.level is None:
            continue
        if e.parent is not None:   # Crafting Station modules
            continue
        seen.add(e.data)
        obj = gd.get(e.data)
        if obj is None:
            continue
        levels, upgrading = instances(snapshot, e.data)
        top = cap(gd, snapshot, e.data)
        if not top:          # nothing unlockable at the current building levels
            continue
        missing = sum(top - lvl for lvl in levels if lvl < top)
        nxt = step(gd, e.data, levels[0]) if levels and levels[0] < top else None
        worker, kind = SECTIONS[e.section]
        out.append({
            "id": e.data, "name": gd.name(e.data), "kind": kind, "worker": worker,
            "count": len(levels), "levels": levels, "upgrading": upgrading,
            "min_level": levels[0] if levels else None, "max_level": top,
            "steps_left": missing,
            "next": None if nxt is None else {"to": nxt.to_level, "seconds": nxt.seconds,
                                               "resource": nxt.resource, "cost": nxt.cost},
        })
    out.sort(key=lambda c: (c["steps_left"] == 0, c["kind"], c["name"]))
    return out


def expand(snapshot, gd, data_id, target_level=None, levels=None):
    """Steps that bring every copy of data_id to target_level (default: max
    for the current Town Hall/Hero Hall), lowest copy first, one level at a
    time, so copies stay even. `levels` (mutated) lets a caller carry the
    simulated levels from one queue item to the next."""
    top = cap(gd, snapshot, data_id) or 0
    target = min(target_level or top, top)
    if levels is None:
        levels, _ = instances(snapshot, data_id)
    steps = []
    while levels and levels[0] < target:
        s = step(gd, data_id, levels[0])
        if s is None:
            break
        steps.append(s)
        levels[0] += 1
        levels.sort()
    return steps


@dataclass(frozen=True)
class Planned:
    item_id: int
    step: Step
    lane: int            # slot index within the worker
    start: float
    end: float
    worker: str = "builder"


def _copies(snapshot, data_id, now):
    """[[level, free_at], ...] for every copy: a copy being upgraded is at its
    next level and only free once that job ends."""
    out = []
    for e in snapshot.entries:
        if e.data != data_id or e.section not in BUILDER_SECTIONS or e.level is None:
            continue
        if e.upgrading:
            out.append([e.level + 1, max(now, snapshot.timestamp + e.timer)])
        else:
            out.extend([e.level, now] for _ in range(e.count))
    return out


def preview(snapshot, gd, items, forecasts=None, now=0, horizon_steps=600):
    """Simulate the queue. `items` are dicts with id, data_id, target_level,
    enabled, in queue order (= priority). Each worker (builders, laboratory,
    pet house) is simulated separately with its real slots; `forecasts` is a
    BuilderForecast (builders) or {worker: BuilderForecast}; missing workers
    are forecast from the snapshot.

    Event simulation: the earliest-free slot takes the first queue item that
    has a copy below its target which is free at that moment (lowest level
    copy first, so copies stay even). A copy is busy while it is being
    upgraded, so one hero never gets two builders. If nothing is free yet, the
    slot waits for whichever copy frees first. Returns (planned steps sorted
    by start, {worker: lane busy-until list}); lanes start busy until their
    current (real, non-Goblin) job ends."""
    from .builders import BuilderForecast, forecast
    if isinstance(forecasts, BuilderForecast):
        forecasts = {forecasts.kind: forecasts}
    forecasts = dict(forecasts or {})
    by_worker = {}
    for item in items:
        if not item.get("enabled", True):
            continue
        sec = section_of(snapshot, item["data_id"])
        if sec is None:
            continue
        by_worker.setdefault(SECTIONS[sec][0], []).append(item)
    planned, all_lanes = [], {}
    for worker, its in by_worker.items():
        f = forecasts.get(worker) or forecast(snapshot, gd, kind=worker)
        real = f.real_jobs()
        lanes = sorted([max(now, j.ends_at) for j in real]
                       + [now] * max(0, f.total - len(real)))
        if not lanes:
            continue
        p, lanes = _simulate(snapshot, gd, its, lanes, now, horizon_steps - len(planned), worker)
        planned += p
        all_lanes[worker] = lanes
    planned.sort(key=lambda x: (x.start, x.worker, x.lane))
    return planned, all_lanes


def _simulate(snapshot, gd, items, lanes, now, horizon_steps, worker):
    copies, targets = {}, []
    for item in items:
        d = item["data_id"]
        top = cap(gd, snapshot, d) or 0
        target = min(item.get("target_level") or top, top)
        copies.setdefault(d, _copies(snapshot, d, now))
        targets.append((item["id"], d, target))

    planned = []
    while len(planned) < horizon_steps:
        lane = min(range(len(lanes)), key=lambda i: lanes[i])
        t = lanes[lane]
        choice = None   # (start, item_id, data_id, copy)
        for item_id, d, target in targets:
            eligible = [c for c in copies[d] if c[0] < target and step(gd, d, c[0])]
            if not eligible:
                continue
            ready = [c for c in eligible if c[1] <= t]
            if ready:
                choice = (t, item_id, d, min(ready, key=lambda c: c[0]))
                break
            c = min(eligible, key=lambda c: (c[1], c[0]))
            if choice is None or c[1] < choice[0]:
                choice = (c[1], item_id, d, c)
        if choice is None:
            break
        start, item_id, d, c = choice
        s = step(gd, d, c[0])
        end = start + s.seconds
        c[0], c[1] = c[0] + 1, end
        lanes[lane] = end
        planned.append(Planned(item_id, s, lane, start, end, worker))
    return planned, lanes
