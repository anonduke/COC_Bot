"""Permanent settings: definitions, defaults and validation.

Values live in the store's `settings` table; anything not set there uses the
default here. `phase` says which roadmap phase starts using a setting, so the
dashboard can show which ones take effect today.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Setting:
    key: str
    group: str
    label: str
    type: str          # bool, int, time, text
    default: object
    help: str
    phase: int
    min: int | None = None
    max: int | None = None


SETTINGS = [
    Setting("automation_paused", "Automation", "Pause automation", "bool", False,
            "Stops all automatic sessions. Manual actions on this page still work.", 8),
    Setting("player_tag", "Automation", "Player tag", "text", "",
            "The village to manage, e.g. #9JCUV0CCV. Captures of any other village are rejected.", 2),
    Setting("active_start", "Schedule", "Active hours start", "time", "07:00",
            "Sessions only start inside the active hours (local time).", 8),
    Setting("active_end", "Schedule", "Active hours end", "time", "23:30",
            "End of the active hours. Set start = end for around the clock.", 8),
    Setting("group_window_min", "Schedule", "Group builders freeing within", "int", 45,
            "Minutes. Builders that free close together are handled in one session.", 8, 0, 240),
    Setting("jitter_min", "Schedule", "Start-time jitter", "int", 5,
            "Up to this many minutes of random delay before a session, so timing is less regular.", 8, 0, 60),
    Setting("health_check_hours", "Schedule", "Health check every", "int", 12,
            "Hours. Re-read the village even when no builder is due, to notice manual play.", 8, 1, 72),
    Setting("farm_max_attacks", "Farming", "Max attacks per session", "int", 30,
            "Farming stops after this many attacks even if the target isn't reached.", 7, 1, 200),
    Setting("farm_max_minutes", "Farming", "Max farming minutes per session", "int", 60,
            "Farming time budget per session.", 7, 5, 600),
    Setting("farm_margin_pct", "Farming", "Safety margin", "int", 10,
            "Percent farmed above the cost of the next upgrades.", 7, 0, 100),
    Setting("upgrade_lookahead", "Upgrades", "Plan items to farm for", "int", 2,
            "How many upcoming queue steps the farming target covers.", 7, 1, 10),
    Setting("dry_run", "Upgrades", "Dry run", "bool", True,
            "Open upgrade dialogs and read costs, but never press Confirm.", 5),
]
BY_KEY = {s.key: s for s in SETTINGS}
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def validate(key, value):
    """Return the value coerced to the setting's type, or raise ValueError."""
    s = BY_KEY.get(key)
    if s is None:
        raise ValueError(f"unknown setting {key!r}")
    if s.type == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{s.label}: expected true/false")
        return value
    if s.type == "int":
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"{s.label}: expected a whole number")
        try:
            v = int(value)
        except ValueError:
            raise ValueError(f"{s.label}: expected a whole number") from None
        if (s.min is not None and v < s.min) or (s.max is not None and v > s.max):
            raise ValueError(f"{s.label}: must be between {s.min} and {s.max}")
        return v
    if s.type == "time":
        if not isinstance(value, str) or not _TIME.match(value):
            raise ValueError(f"{s.label}: expected HH:MM")
        return value
    if not isinstance(value, str) or len(value) > 200:
        raise ValueError(f"{s.label}: expected text")
    v = value.strip()
    if key == "player_tag" and v and not re.match(r"^#[0-9A-Z]{3,15}$", v.upper()):
        raise ValueError("Player tag: expected something like #9JCUV0CCV")
    return v.upper() if key == "player_tag" else v
