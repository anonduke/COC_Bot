"""Builder tracker: who is busy until when, and when the next builder frees.

All functions take the time explicitly (`now`, epoch seconds) so they can be
tested with a fake clock. Forecasts assume nothing new is started after the
snapshot.

Total builders: the in-game counter ("1/7") is authoritative. The export alone
can't see an idle extra builder: it lists Builder's Huts and B.O.B's Hut, and
a job on the extra (7th) builder carries `extra: true`, but an idle extra
builder leaves no trace. So without a counter reading the total is a lower
bound.

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
    extra: bool           # runs on the extra (7th) builder

    @property
    def key(self):
        return (self.section, self.data, self.from_level)


@dataclass
class BuilderForecast:
    taken_at: int                     # export timestamp
    total: int
    total_source: str                 # "counter" or "export" (lower bound)
    jobs: list = field(default_factory=list)  # BuilderJob, sorted by ends_at

    def busy(self, now):
        return sum(1 for j in self.jobs if j.ends_at > now)

    def free(self, now):
        return max(0, self.total - self.busy(now))

    def next_free_at(self, now):
        """When the next builder is free: `now` if one already is, else the
        earliest predicted end (None if there are no builders at all)."""
        if self.free(now) > 0:
            return now
        ends = [j.ends_at for j in self.jobs if j.ends_at > now]
        return min(ends) if ends else None

    def frees_between(self, start, end):
        """Jobs predicted to finish in (start, end]."""
        return [j for j in self.jobs if start < j.ends_at <= end]


def total_from_export(snapshot):
    huts = sum(e.count for e in snapshot.section("buildings") if e.data == BUILDERS_HUT)
    bob = 1 if any(e.data == BOBS_HUT for e in snapshot.section("buildings")) else 0
    extra = 1 if any(j.extra for j in snapshot.builder_jobs()) else 0
    return huts + bob + extra


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


def forecast(snapshot, gamedata=None, counter=None):
    """BuilderForecast for the home village. `counter` is the (free, total)
    read from the screen, if available. `gamedata` is needed only to model
    helper boosts; without it helper jobs use the plain timer."""
    jobs = []
    for j in snapshot.builder_jobs():
        timer_end = j.ends_at
        ends_at = float(timer_end)
        if j.helper:
            windows = helper_windows(snapshot, gamedata, BUILDER_APPRENTICE, timer_end)
            ends_at = finish_time(snapshot.timestamp, timer_end - snapshot.timestamp, windows)
        jobs.append(BuilderJob(j.section, j.data, j.from_level, timer_end, ends_at,
                               j.helper, j.extra))
    jobs.sort(key=lambda j: j.ends_at)
    if counter is not None:
        total, source = counter[1], "counter"
    else:
        total, source = total_from_export(snapshot), "export"
    return BuilderForecast(snapshot.timestamp, total, source, jobs)


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
