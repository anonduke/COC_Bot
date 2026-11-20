"""Builder tracker: who is busy until when, and when the next builder frees.

All functions take the time explicitly (`now`, epoch seconds) so they can be
tested with a fake clock. Forecasts assume nothing new is started after the
snapshot.

Workers: "builder" (buildings, traps, heroes), "lab" (troops, spells, siege
machines) and "pet_house" (pets). Only REAL slots are counted: builders =
Builder's Huts + B.O.B's Hut, lab = 1, pet house = 1. The game also offers a
gem-paid Goblin Builder / Goblin Researcher; their slots are included in the
in-game counters ("1/7", "1/2") and their jobs are marked `extra: true` in the
export. We never use them, so they are not counted: an `extra` job does not
occupy a real slot (live 2026-11-19: "1/7" meant 0 of 6 real builders free).

Helpers: a job with `helper_recurrent` gets the helper's boost every time the
helper comes off cooldown. Builder's Apprentice level L adds L x progress for
BoostTimeSeconds (villager_apprentices.csv), so the job runs at (1 + L) x
speed during that window. The rest between boosts is not in the tables;
HELPER_REST is an assumption, checked by `verify_series` on live data.
"""
from dataclasses import dataclass, field

BUILDERS_HUT = 1000015
BOBS_HUT = 1000064
BUILDER_APPRENTICE = 93000000
LAB_ASSISTANT = 93000001
HELPER_FOR_KIND = {"builder": BUILDER_APPRENTICE, "lab": LAB_ASSISTANT}
HELPER_REST = 23 * 3600   # assumption, see module docstring


@dataclass(frozen=True)
class BuilderJob:
    section: str
    data: int
    from_level: int
    timer_end: int        # export timestamp + timer: end if nothing speeds it up
    ends_at: float        # predicted end including helper boosts
    helper: bool
    extra: bool           # done by the gem-paid Goblin Builder/Researcher (not a real slot)

    @property
    def key(self):
        return (self.section, self.data, self.from_level)


@dataclass
class BuilderForecast:
    taken_at: int                     # export timestamp
    total: int                        # real slots of this worker
    total_source: str                 # always "export" (kept for the API)
    jobs: list = field(default_factory=list)  # BuilderJob, sorted by ends_at (goblin jobs too)
    kind: str = "builder"

    def real_jobs(self):
        return [j for j in self.jobs if not j.extra]

    def busy(self, now):
        return sum(1 for j in self.real_jobs() if j.ends_at > now)

    def free(self, now):
        return max(0, self.total - self.busy(now))

    def next_free_at(self, now):
        """When the next real slot is free: `now` if one already is, else the
        earliest predicted end of a real job (None if there are no slots)."""
        if self.free(now) > 0:
            return now
        ends = [j.ends_at for j in self.real_jobs() if j.ends_at > now]
        return min(ends) if ends else None

    def frees_between(self, start, end):
        """Real jobs predicted to finish in (start, end]."""
        return [j for j in self.real_jobs() if start < j.ends_at <= end]


LABORATORY = 1000007
PET_HOUSE = 1000068
WORKERS = ("builder", "lab", "pet_house")


def total_from_export(snapshot, kind="builder"):
    """Real slots of a worker (Goblin slots excluded)."""
    if kind == "lab":
        return 1 if snapshot.level_of(LABORATORY) else 0
    if kind == "pet_house":
        return 1 if snapshot.level_of(PET_HOUSE) else 0
    huts = sum(e.count for e in snapshot.section("buildings") if e.data == BUILDERS_HUT)
    bob = 1 if any(e.data == BOBS_HUT for e in snapshot.section("buildings")) else 0
    return huts + bob


def helper_windows(snapshot, gamedata, helper_id, horizon):
    """[(start, end, multiplier)] boost windows of a helper from the export
    time until `horizon`, assuming it is re-assigned every time it is ready."""
    entry = next((e for e in snapshot.section("helpers") if e.data == helper_id), None)
    obj = gamedata.get(helper_id) if gamedata else None
    if entry is None or obj is None or entry.level is None:
        return []
    row = obj.level(entry.level)
    if row is None:
        return []
    mult = int(row.get("BoostMultiplier") or 0)
    boost = int(row.get("BoostTimeSeconds") or 0)
    if mult <= 0 or boost <= 0:
        return []
    start = snapshot.timestamp + (entry.helper_cooldown or 0)
    windows = []
    while start < horizon:
        windows.append((start, start + boost, mult))
        start += boost + HELPER_REST
    return windows


def finish_time(start, work, windows):
    """When `work` seconds of building finish, starting at `start`, with
    speed 1 + m inside each (begin, end, m) window and 1 elsewhere."""
    t, w = float(start), float(work)
    for begin, end, m in sorted(windows):
        if end <= t:
            continue
        if begin > t:
            if t + w <= begin:
                return t + w
            w -= begin - t
            t = begin
        rate = 1 + m
        if w <= (end - t) * rate:
            return t + w / rate
        w -= (end - t) * rate
        t = end
    return t + w


def forecast(snapshot, gamedata=None, counter=None, kind="builder"):
    """BuilderForecast for one worker of the home village. Slots come from the
    export (real slots only, see the module docstring); `counter` is accepted
    for compatibility but no longer sets the total, because the in-game
    counters include the Goblin slots. `gamedata` is needed only to model
    helper boosts; without it helper jobs use the plain timer."""
    helper = HELPER_FOR_KIND.get(kind)
    jobs = []
    for j in snapshot.jobs(kind):
        timer_end = j.ends_at
        ends_at = float(timer_end)
        if j.helper and helper:
            windows = helper_windows(snapshot, gamedata, helper, timer_end)
            ends_at = finish_time(snapshot.timestamp, timer_end - snapshot.timestamp, windows)
        jobs.append(BuilderJob(j.section, j.data, j.from_level, timer_end, ends_at,
                               j.helper, j.extra))
    jobs.sort(key=lambda j: j.ends_at)
    return BuilderForecast(snapshot.timestamp, total_from_export(snapshot, kind), "export",
                           jobs, kind)


# --- verification against live data -------------------------------------

@dataclass(frozen=True)
class Finding:
    key: tuple          # (section, data, from_level)
    kind: str           # "drift", "early", "late", "ok"
    detail: str
    seconds: float      # size of the deviation (0 for ok)


def verify_series(snapshots, gamedata=None, tolerance=120):
    """Check forecasts against what later snapshots show.

    For every builder job seen in the series:
      * drift: its predicted end, as computed from each snapshot in which it
        was still running, must stay within `tolerance`;
      * finish: once a later snapshot shows it finished, the last prediction
        must lie between the last snapshot where it was running and the first
        where it was done (each widened by `tolerance`).
    Jobs of the same type and level are paired by end time. Returns a list of
    Finding (one "ok" per job that passed both checks)."""
    snaps = sorted(snapshots, key=lambda s: s.timestamp)
    history = {}   # key -> list of (timestamp, [ends...]) per snapshot
    for s in snaps:
        groups = {}
        for j in forecast(s, gamedata).jobs:
            groups.setdefault(j.key, []).append(j.ends_at)
        for key in set(groups) | set(history):
            history.setdefault(key, []).append((s.timestamp, sorted(groups.get(key, []))))

    findings = []
    for key, seen in history.items():
        width = max(len(ends) for _, ends in seen)
        for slot in range(width):
            preds = []           # (snapshot ts, predicted end) while running
            done_at = None
            for ts, ends in seen:
                # Slots are matched from the latest-ending job downwards so a
                # job finishing doesn't shift the others.
                idx = len(ends) - width + slot
                if idx >= 0:
                    if done_at is None:
                        preds.append((ts, ends[idx]))
                elif preds and done_at is None:
                    done_at = ts
            if not preds:
                continue
            spread = max(p for _, p in preds) - min(p for _, p in preds)
            ok = True
            if spread > tolerance:
                findings.append(Finding(key, "drift",
                                        f"predicted end moved by {spread:.0f}s across "
                                        f"{len(preds)} snapshots", spread))
                ok = False
            if done_at is not None:
                last_running, predicted = preds[-1]
                if predicted > done_at + tolerance:
                    findings.append(Finding(key, "early",
                                            "finished before the predicted end",
                                            predicted - done_at))
                    ok = False
                elif predicted < last_running - tolerance:
                    findings.append(Finding(key, "late",
                                            "still running after the predicted end",
                                            last_running - predicted))
                    ok = False
            if ok:
                findings.append(Finding(key, "ok",
                                        "finished as predicted" if done_at else "stable", 0))
    return findings
