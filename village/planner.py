"""Planner (Phase 6): which upgrade should a free builder start next?

candidates() walks the upgrade queue in order (queue order = priority) and
returns, per enabled item, the step for its lowest copy that is idle (not
being upgraded) and below the item's target level. Items whose every copy is
busy or at target give nothing, so a hero being upgraded never blocks the
queue. Each candidate says whether the game tables think it is affordable
now; the tables are an upper bound (events discount prices), so "affordable"
is certain, "not affordable" is a hint and the upgrade dialog decides.
"""
from dataclasses import dataclass

from .catalog import BUILDER_SECTIONS, HERO_HALL, TOWN_HALL, max_level, step

RESOURCE_KEYS = {"Gold": "gold", "Elixir": "elixir", "DarkElixir": "dark_elixir"}
FILL_KEYS = {"gold": "gold_fill", "elixir": "elixir_fill", "dark_elixir": "dark_fill"}


@dataclass(frozen=True)
class Candidate:
    item_id: int
    data_id: int
    name: str
    from_level: int
    seconds: int
    resource: str          # gold / elixir / dark_elixir
    cost: int              # from the game tables (upper bound)
    affordable: bool | None  # None = resource unknown
    capacity: int | None   # storage capacity estimate, if known
    reason: str = ""       # why it can't be done even after farming


def capacity(value, fill):
    """Storage capacity from the shown amount and the bar fill fraction."""
    if value is None or not fill or fill < 0.05:
        return None
    return int(value / fill)


def candidates(snapshot, gd, plan_items, resources=None, fills=None, exclude=()):
    """Candidates in queue order. `resources` {gold, elixir, dark_elixir},
    `fills` {gold_fill, ...}; `exclude` (data_id, from_level) keys already
    tried in this session."""
    th = snapshot.level_of(TOWN_HALL) or 1
    hh = snapshot.level_of(HERO_HALL)
    out = []
    for item in plan_items:
        if not item.get("enabled", True):
            continue
        d = item["data_id"]
        cap = max_level(gd, d, th, hh) or 0
        target = min(item.get("target_level") or cap, cap)
        idle = sorted(lvl for e in snapshot.entries
                      if e.data == d and e.section in BUILDER_SECTIONS and e.level is not None
                      and not e.upgrading for lvl in [e.level] * e.count)
        idle = [lvl for lvl in idle if lvl < target and (d, lvl) not in exclude]
        if not idle:
            continue
        s = step(gd, d, idle[0])
        if s is None:
            continue
        res = RESOURCE_KEYS.get(s.resource, s.resource.lower())
        have = (resources or {}).get(res)
        cap_est = capacity(have, (fills or {}).get(FILL_KEYS.get(res, "")))
        reason = ""
        if cap_est is not None and s.cost > cap_est:
            reason = f"costs {s.cost:,} {res}, more than the storage holds (~{cap_est:,})"
        out.append(Candidate(item["id"], d, gd.name(d), idle[0], s.seconds, res, s.cost,
                             None if have is None else have >= s.cost, cap_est, reason))
    return out


def farm_target(c, margin_pct):
    """Amount of c.resource to farm for, capped just below the storage."""
    want = int(c.cost * (1 + margin_pct / 100))
    if c.capacity:
        want = min(want, int(c.capacity * 0.99))
    return want
