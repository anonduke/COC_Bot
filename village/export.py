"""Parse the in-game data export (Settings -> More Settings -> Data Export).

The export is JSON with a player `tag`, a `timestamp` (epoch seconds, when the
game produced it) and one list per section. Entries look like
    {"data": id, "lvl": n, "cnt": k}            k copies at level n
    {"data": id, "lvl": n, "timer": secs}       one copy upgrading n -> n+1,
                                                secs remaining at `timestamp`
plus flags seen on live exports:
    gear_up            building has been geared up (e.g. Mortar)
    extra              set on some timed jobs; meaning not confirmed yet
    helper_recurrent   a helper (Builder's Apprentice / Lab Assistant) is assigned
    helper_cooldown    (helpers section) seconds until the helper is ready
    types/modules      Crafting Station: archetypes, each with module levels
Keys ending in "2" are the Builder Base. house_parts/skins/sceneries are
plain ID lists (cosmetics).
"""
import json
from dataclasses import dataclass

# Which work queue a timed entry belongs to.
JOB_KIND = {
    "buildings": "builder",
    "traps": "builder",
    "heroes": "builder",
    "units": "lab",
    "siege_machines": "lab",
    "spells": "lab",
    "pets": "pet_house",
    "buildings2": "bb_builder",
    "traps2": "bb_builder",
    "heroes2": "bb_builder",
    "units2": "star_lab",
}
ID_LIST_SECTIONS = ("house_parts", "skins", "sceneries", "skins2", "sceneries2")


class ExportError(ValueError):
    pass


@dataclass(frozen=True)
class Entry:
    section: str
    data: int
    level: int | None       # None for objects without levels (decos, obstacles)
    count: int = 1
    timer: int | None = None  # seconds left at the export timestamp
    gear_up: bool = False
    extra: bool = False
    helper_recurrent: bool = False
    helper_cooldown: int | None = None
    parent: int | None = None  # Crafting Station archetype for modules

    @property
    def upgrading(self):
        return self.timer is not None


@dataclass(frozen=True)
class Job:
    kind: str          # builder, lab, pet_house, bb_builder, star_lab, unknown
    section: str
    data: int
    from_level: int | None
    ends_at: int       # epoch seconds
    helper: bool       # a helper is assigned
    extra: bool


@dataclass
class Snapshot:
    tag: str
    timestamp: int
    entries: list
    raw: dict

    def section(self, name):
        return [e for e in self.entries if e.section == name]

    def jobs(self, kind=None):
        jobs = [Job(JOB_KIND.get(e.section, "unknown"), e.section, e.data, e.level,
                    self.timestamp + e.timer, e.helper_recurrent, e.extra)
                for e in self.entries if e.upgrading]
        jobs.sort(key=lambda j: j.ends_at)
        return jobs if kind is None else [j for j in jobs if j.kind == kind]

    def builder_jobs(self):
        return self.jobs("builder")

    def helper_cooldowns(self):
        """{helper id: epoch seconds when it is ready again} for helpers on cooldown."""
        return {e.data: self.timestamp + e.helper_cooldown
                for e in self.section("helpers") if e.helper_cooldown}

    def level_of(self, data_id):
        """Highest level of an object (e.g. Town Hall), or None if absent."""
        levels = [e.level for e in self.entries if e.data == data_id and e.level is not None]
        return max(levels) if levels else None


def _entry(section, item, parent=None):
    if not isinstance(item, dict) or not isinstance(item.get("data"), int):
        raise ExportError(f"{section}: unexpected entry {item!r}")
    return Entry(
        section=section,
        data=item["data"],
        level=item.get("lvl"),
        count=item.get("cnt", 1),
        timer=item.get("timer"),
        gear_up=bool(item.get("gear_up")),
        extra=bool(item.get("extra")),
        helper_recurrent=bool(item.get("helper_recurrent")),
        helper_cooldown=item.get("helper_cooldown"),
        parent=parent,
    )


def parse_export(text):
    """Parse export JSON text (a leading BOM is tolerated) into a Snapshot.
    Raises ExportError if it is not a village export."""
    try:
        raw = json.loads(text.lstrip("﻿"))
    except (json.JSONDecodeError, AttributeError) as e:
        raise ExportError(f"not JSON: {e}") from e
    if not isinstance(raw, dict):
        raise ExportError("export is not a JSON object")
    tag, ts = raw.get("tag"), raw.get("timestamp")
    if not (isinstance(tag, str) and tag.startswith("#")):
        raise ExportError(f"missing or bad tag: {tag!r}")
    if not isinstance(ts, int):
        raise ExportError(f"missing or bad timestamp: {ts!r}")
    if not isinstance(raw.get("buildings"), list):
        raise ExportError("no buildings section")

    entries = []
    for section, items in raw.items():
        if section in ID_LIST_SECTIONS or not isinstance(items, list):
            continue
        for item in items:
            entries.append(_entry(section, item))
            for archetype in item.get("types", []):
                entries.append(_entry(section, archetype, parent=item["data"]))
                for module in archetype.get("modules", []):
                    entries.append(_entry(section, module, parent=archetype["data"]))
    return Snapshot(tag, ts, entries, raw)
