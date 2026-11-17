"""Orchestrator (Phase 8): the long-running process that decides when to run
a session, runs it as a child process, and reports its state for the
dashboard (kv "orchestrator").

Scheduling (next_due, pure, tested with a fake clock):
  * no snapshot yet                            -> now ("first look")
  * a builder free and the queue has work      -> now ("builder free")
      ...unless recent sessions made no progress: back off 30 min, then 2 h
  * otherwise the next builder to free, grouped with any others freeing within
    group_window_min (one session for all of them)
  * health check: the village was last read more than health_check_hours ago
  * outside active hours: moved to the next start of the active window
  * jitter: a random 0..jitter_min minutes, fixed per due time
Commands from the dashboard (table `commands`): run_session runs now, even
while paused. "Pause automation" (setting) stops scheduled sessions only.

The state is recomputed from the database on every tick, so a restart or a
missed wake-up is harmless.
"""
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta

from .builders import forecast
from .settings import effective

TICK = 20
SESSION_TIMEOUT = 3 * 3600
BACKOFF = (30 * 60, 2 * 3600)
FAIL_BACKOFF = (10 * 60, 30 * 60, 2 * 3600)   # after failed sessions (any trigger)
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def in_active_hours(ts, start, end):
    """start/end 'HH:MM'; start == end means always; windows may cross midnight."""
    if start == end:
        return True
    t = datetime.fromtimestamp(ts).strftime("%H:%M")
    return start <= t < end if start < end else (t >= start or t < end)


def next_active_start(ts, start, end):
    """ts itself if inside the window, else the next window start."""
    if in_active_hours(ts, start, end):
        return ts
    d = datetime.fromtimestamp(ts)
    h, m = map(int, start.split(":"))
    cand = d.replace(hour=h, minute=m, second=0, microsecond=0)
    if cand.timestamp() <= ts:
        cand += timedelta(days=1)
    return cand.timestamp()


def next_due(now, latest, gamedata, has_work, cfg, no_progress=0, last_end=None):
    """(due_ts, reason): the moment a session became (or becomes) due.

    The value is STABLE: when it is in the past it stays at that past moment
    (when the builder was seen free, capture time + health interval, ...),
    it is never clamped to `now`. Live 2026-11-17: clamping made "due"
    change every tick, the jitter was re-drawn every tick, and an overdue
    session never started. Active hours are applied by the caller."""
    if latest is None:
        return 0, "first look at the village"
    snap = latest.snapshot
    options = []
    f = forecast(snap, gamedata, latest.counter)
    if has_work(snap):
        if f.free(now) > 0:
            if f.free(latest.captured_at) > 0:
                due = latest.captured_at                 # seen free at the last capture
            else:
                due = min((j.ends_at for j in f.jobs if j.ends_at <= now),
                          default=latest.captured_at)
            reason = "builder free"
            if no_progress and last_end:
                retry = last_end + BACKOFF[min(no_progress, len(BACKOFF)) - 1]
                if retry > due:
                    due, reason = retry, "retry (no progress last time)"
            options.append((due, reason))
        else:
            ends = sorted(j.ends_at for j in f.jobs if j.ends_at > now)
            if ends:
                window = cfg["group_window_min"] * 60
                group = [e for e in ends if e <= ends[0] + window]
                options.append((group[-1], f"{len(group)} builder(s) free"
                                if len(group) > 1 else "builder free"))
    options.append((latest.captured_at + cfg["health_check_hours"] * 3600, "health check"))
    return min(options)


class Orchestrator:
    def __init__(self, store, log=print, spawn=None, clock=time.time, game_busy=None):
        self.store, self.log, self.clock = store, log, clock
        self.spawn = spawn or self._spawn
        if game_busy is None:
            from .capture import is_capture_running as game_busy
        self.game_busy = game_busy
        self.proc = None
        self.current = None        # (trigger, started_at)
        self._jitter = {}

    # --- child sessions -------------------------------------------------------
    def _spawn(self, trigger):
        logdir = os.path.join(REPO, "data", "sessions")
        os.makedirs(logdir, exist_ok=True)
        out = open(os.path.join(logdir, time.strftime("%Y%m%d_%H%M%S_") + trigger + ".log"),
                   "w", encoding="utf-8")
        p = subprocess.Popen([sys.executable, "-m", "village", "session", "--trigger", trigger],
                             cwd=REPO, stdout=out, stderr=subprocess.STDOUT,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out.close()
        return p

    def start_session(self, trigger):
        self.log(f"starting session ({trigger})")
        self.proc = self.spawn(trigger)
        self.current = (trigger, self.clock())

    def check_session(self):
        if self.proc is None:
            return
        if self.proc.poll() is None:
            if self.clock() - self.current[1] > SESSION_TIMEOUT:
                self.log("session timed out: killing it")
                self.proc.kill()
                self.store.notify("error", "orchestrator", "A session ran over 3 hours and was stopped")
            return
        code = self.proc.returncode
        self.log(f"session ({self.current[0]}) ended with code {code}")
        last = self.store.kv_get("last_session") or {}
        progress = last.get("upgrades", 0) > 0 or "builder" not in self.current[0]
        streak = 0 if progress and code == 0 else self.store.kv_get("no_progress", 0) + 1
        self.store.kv_set("no_progress", streak)
        # A failed session leaves its due time in the past: without this the
        # next tick would start it again (seen live 2026-11-17).
        fails = 0 if code == 0 else self.store.kv_get("fail_streak", {}).get("n", 0) + 1
        self.store.kv_set("fail_streak", {"n": fails, "at": self.clock()})
        self.proc, self.current = None, None

    # --- scheduling -----------------------------------------------------------
    def _has_work(self, gd, cfg):
        from . import planner
        plan = self.store.plan()

        def has_work(snap):
            return bool(plan) and any(not c.reason for c in planner.candidates(snap, gd, plan))
        return has_work

    def compute(self):
        from .web import game_data
        cfg = effective(self.store)
        latest = self.store.latest()
        gd = game_data(latest.game_version) if latest else None
        last = self.store.kv_get("last_session") or {}
        due, reason = next_due(self.clock(), latest, gd, self._has_work(gd, cfg) if gd else
                               (lambda s: False), cfg, self.store.kv_get("no_progress", 0),
                               last.get("ended_at"))
        fails = self.store.kv_get("fail_streak", {})
        if fails.get("n"):
            retry = fails["at"] + FAIL_BACKOFF[min(fails["n"], len(FAIL_BACKOFF)) - 1]
            if retry > due:
                due, reason = retry, f"retry after {fails['n']} failed session(s)"
        key = (int(due), reason)
        if key not in self._jitter:                  # drawn once per due moment
            self._jitter = {key: random.uniform(0, cfg["jitter_min"] * 60)}
        return due + self._jitter[key], reason, cfg

    def tick(self):
        self.check_session()
        now = self.clock()
        state = {"pid": os.getpid(), "heartbeat": now, "running": None, "next_due": None,
                 "next_reason": "", "paused": False}
        for cmd in self.store.pending_commands():
            if cmd["kind"] == "run_session":
                if self.proc is None:
                    self.start_session("manual")
                    self.store.finish_command(cmd["id"], "started")
                elif now - cmd["created_at"] > 600:
                    self.store.finish_command(cmd["id"], "dropped: a session was running")
            else:
                self.store.finish_command(cmd["id"], "unknown command")
        due, reason, cfg = self.compute()
        state["paused"] = bool(cfg["automation_paused"])
        active = in_active_hours(now, cfg["active_start"], cfg["active_end"])
        if self.proc is None and not state["paused"] and due <= now and active:
            if self.game_busy():
                reason += " (waiting: the game is busy, e.g. a farming run)"
            else:
                self.start_session(reason.split(" (")[0])
        if self.current:
            state["running"] = {"trigger": self.current[0], "since": self.current[1]}
        if not state["paused"]:
            # For display: when it will actually start (active hours applied).
            state["next_due"] = next_active_start(max(due, now), cfg["active_start"],
                                                  cfg["active_end"])
            state["next_reason"] = reason
        self.store.kv_set("orchestrator", state)
        return state

    def run_forever(self):
        from .power import keep_display_on
        self.log("orchestrator started")
        self._cleanup_stale()
        with keep_display_on():
            while True:
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001
                    self.log(f"tick failed: {e}")
                time.sleep(TICK)

    def _cleanup_stale(self):
        """Sessions marked running whose process is gone: interrupted."""
        for s in self.store.sessions(50):
            if s["status"] == "running" and not pid_alive(s["pid"]):
                self.store.end_session(s["id"], "interrupted",
                                       (s["summary"] + "\n" if s["summary"] else "")
                                       + "interrupted (the orchestrator restarted)")


def pid_alive(pid):
    """True if a process with this id is still running (Windows)."""
    if not pid:
        return False
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, int(pid))      # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    try:
        return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        k.CloseHandle(h)


def main():
    from .store import Store
    Orchestrator(Store(), log=lambda m: print(time.strftime("%H:%M:%S"), m, flush=True)).run_forever()
