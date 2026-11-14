"""What can be upgraded with builders, what each step costs, and a preview
of how an ordered upgrade queue would play out across the builders.

Table semantics (verified on game 18.647.5):
  buildings / traps: row L = cost and time to REACH level L (row 1 = build);
                     TownHallLevel on row L = Town Hall needed for level L.
  heroes:            row L = cost and time to go FROM L to L+1 (last row empty);
                     RequiredTownHallLevel / RequiredHeroTavernLevel on row L =
                     what level L needs.
  The resource column is filled on the first row only; later rows inherit it.

The queue preview is the start of the Phase 6 planner: strict queue order, the
earliest-free builder takes the next step, no resource or farming limits yet.
"""
from dataclasses import dataclass

TOWN_HALL = 1000001
HERO_HALL = 1000071
BUILDER_SECTIONS = ("buildings", "traps", "heroes")


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
    if is_hero(obj):
        row = obj.level(from_level)
        if row is None or not row.get("UpgradeCost"):
            return None
        return Step(data_id, from_level, to, _int(row.get("UpgradeTimeH")) * 3600,
                    _resource(obj, from_level, "UpgradeResource"), _int(row.get("UpgradeCost")))
    row = obj.level(to)
    if row is None:
        return None
    secs = (_int(row.get("BuildTimeD")) * 86400 + _int(row.get("BuildTimeH")) * 3600
            + _int(row.get("BuildTimeM")) * 60 + _int(row.get("BuildTimeS")))
    return Step(data_id, from_level, to, secs, _resource(obj, to, "BuildResource"),
                _int(row.get("BuildCost")))


def max_level(gd, data_id, th_level, hero_hall_level=None):
    """Highest level allowed at this Town Hall (and Hero Hall for heroes)."""
    obj = gd.get(data_id)
    if obj is None:
        return None
    best = 0
    for lvl, row in enumerate(obj.rows, start=1):
        if is_hero(obj):
            ok = (_int(row.get("RequiredTownHallLevel")) <= th_level and
                  (hero_hall_level is None or
                   _int(row.get("RequiredHeroTavernLevel")) <= hero_hall_level))
        else:
            ok = _int(row.get("TownHallLevel")) <= th_level
        if ok:
            best = lvl
    return best


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
    th = snapshot.level_of(TOWN_HALL) or 1
    hh = snapshot.level_of(HERO_HALL)
    seen, out = set(), []
    for e in snapshot.entries:
        if e.section not in BUILDER_SECTIONS or e.data in seen or e.level is None:
            continue
        if e.parent is not None:   # Crafting Station modules
            continue
        seen.add(e.data)
        obj = gd.get(e.data)
        if obj is None:
            continue
        levels, upgrading = instances(snapshot, e.data)
        cap = max_level(gd, e.data, th, hh)
        missing = sum(cap - lvl for lvl in levels if lvl < cap)
        nxt = step(gd, e.data, levels[0]) if levels and levels[0] < cap else None
        out.append({
            "id": e.data, "name": gd.name(e.data), "kind": "hero" if is_hero(obj) else
            ("trap" if e.section == "traps" else "building"),
            "count": len(levels), "levels": levels, "upgrading": upgrading,
            "min_level": levels[0] if levels else None, "max_level": cap,
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
    th = snapshot.level_of(TOWN_HALL) or 1
    cap = max_level(gd, data_id, th, snapshot.level_of(HERO_HALL)) or 0
    target = min(target_level or cap, cap)
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
    lane: int
    start: float
    end: float


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


def preview(snapshot, gd, items, builder_forecast, now, horizon_steps=600):
    """Simulate the queue. `items` are dicts with id, data_id, target_level,
    enabled, in queue order (= priority).

    Event simulation: the earliest-free builder takes the first queue item
    that has a copy below its target which is free at that moment (lowest
    level copy first, so copies stay even). A copy is busy while it is being
    upgraded, so one hero never gets two builders. If nothing is free yet, the
    builder waits for whichever copy frees first. Returns (planned steps,
    lane busy-until list); lanes start busy until their current job ends."""
    lanes = sorted([max(now, j.ends_at) for j in builder_forecast.jobs]
                   + [now] * max(0, builder_forecast.total - len(builder_forecast.jobs)))
    lanes = lanes or [now]
    th = snapshot.level_of(TOWN_HALL) or 1
    hh = snapshot.level_of(HERO_HALL)
    copies, targets = {}, []
    for item in items:
        if not item.get("enabled", True):
            continue
        d = item["data_id"]
        cap = max_level(gd, d, th, hh) or 0
        target = min(item.get("target_level") or cap, cap)
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
        planned.append(Planned(item_id, s, lane, start, end))
    return planned, lanes
