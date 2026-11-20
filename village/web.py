"""Dashboard web server (python -m village web).

Standard library only: ThreadingHTTPServer + a JSON API + static files in
village/static/. Listens on the LAN so the dashboard works from a phone.

Every state-changing request is a POST with the header `X-Village: 1`. A
browser only sends a custom header to another origin after a CORS preflight,
which this server never approves, so other websites can't trigger actions.
There is no login: it is meant for a home network.
"""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import adb, gamedata
from .builders import BUILDERS_HUT, LABORATORY, PET_HOUSE, WORKERS, forecast
from .catalog import HERO_HALL, TOWN_HALL, WORKER_NAMES, cap, catalog, preview, step
from .settings import SETTINGS, validate
from .store import Store, test_key

PREVIEW_STEPS = 600   # queue steps simulated for the preview
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
IMG = os.path.join(STATIC, "img")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(REPO, "data", "screens", "dryrun")
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
                 ".png": "image/png", ".ico": "image/x-icon"}


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# --- shared, cached state ---------------------------------------------------

_gd_cache = {}
_gd_lock = threading.Lock()


def game_data(version):
    if not version:
        return None
    with _gd_lock:
        if version not in _gd_cache:
            try:
                _gd_cache[version] = gamedata.GameData.load(gamedata.static_dir(version))
            except OSError:
                _gd_cache[version] = None
        return _gd_cache[version]


class EmulatorWatch:
    """Polls the emulator every `interval` seconds in a daemon thread."""

    def __init__(self, interval=20):
        self.interval = interval
        self.status = {"state": "unknown", "detail": "", "checked_at": None}
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            self.status = self.check()
            time.sleep(self.interval)

    @staticmethod
    def check():
        now = time.time()
        try:
            focus = adb.focused_window()
        except adb.AdbError as e:
            return {"state": "offline", "detail": str(e)[:200], "checked_at": now}
        if "Application Not Responding" in focus:
            state = "dialog"
        elif f"{adb.COC_PACKAGE}/" in focus:
            state = "game"
        else:
            state = "other"
        return {"state": state, "detail": focus, "checked_at": now}


# --- payload builders ---------------------------------------------------------

def _job_dict(gd, j, kind):
    s = step(gd, j.data, j.from_level) if gd else None
    duration = s.seconds if s else None
    return {"kind": kind, "id": j.data, "name": gd.name(j.data) if gd else f"#{j.data}",
            "from": j.from_level, "to": (j.from_level or 0) + 1,
            "ends_at": j.ends_at, "duration": duration,
            "helper": j.helper, "extra": j.extra}


def player_payload(store, snap):
    """Name (the account's display name first, else read from the game
    screen), clan, XP level."""
    seen = store.kv_get(store.akey("player", snap.tag)) or {}
    acct = store.account(snap.tag) or {}
    name = (acct.get("display_name") or "").strip()
    return {"name": name or seen.get("name") or acct.get("village"), "read_name": seen.get("name"),
            "clan": seen.get("clan"), "xp": seen.get("xp"), "tag": snap.tag}


def state_payload(store):
    stored = store.latest()
    if stored is None:
        return {"now": time.time(), "snapshot": None}
    snap = stored.snapshot
    gd = game_data(stored.game_version)
    f = forecast(snap, gd, stored.counter)
    now = time.time()
    builders = []
    for j in f.jobs:
        d = _job_dict(gd, j, "builder")
        d["ends_at"] = j.ends_at
        d["timer_end"] = j.timer_end
        builders.append(d)
    helpers = []
    for e in snap.section("helpers"):
        helpers.append({"id": e.data, "name": gd.name(e.data) if gd else f"#{e.data}",
                        "level": e.level,
                        "ready_at": snap.timestamp + e.helper_cooldown if e.helper_cooldown else None})

    # Resource change versus about a day earlier (nearest snapshot with values).
    day_ago = store.db.execute(
        "SELECT gold, elixir, dark_elixir, gems, export_ts FROM snapshots "
        "WHERE tag = ? AND export_ts <= ? AND gold IS NOT NULL ORDER BY export_ts DESC LIMIT 1",
        (snap.tag, snap.timestamp - 20 * 3600)).fetchone()
    r = stored.resources
    return {
        "now": now,
        "snapshot": {"id": stored.id, "tag": snap.tag, "exported_at": snap.timestamp,
                     "captured_at": stored.captured_at, "game_version": stored.game_version,
                     "town_hall": snap.level_of(TOWN_HALL), "hero_hall": snap.level_of(HERO_HALL),
                     "lab_level": snap.level_of(LABORATORY),
                     "pet_house_level": snap.level_of(PET_HOUSE),
                     "builder_huts": sorted((e.level for e in snap.section("buildings")
                                             if e.data == BUILDERS_HUT and e.level is not None
                                             for _ in range(e.count)), reverse=True)},
        "player": player_payload(store, snap),
        "resources": {"gold": r.gold, "elixir": r.elixir, "dark_elixir": r.dark_elixir,
                      "gems": r.gems},
        "resources_before": dict(day_ago) if day_ago else None,
        "builders": {"total": f.total, "source": f.total_source, "free_now": f.free(now),
                     "next_free_at": f.next_free_at(now), "jobs": builders},
        "lab": [_job_dict(gd, j, "lab") for j in forecast(snap, gd, kind="lab").jobs],
        "pets": [_job_dict(gd, j, "pet_house") for j in forecast(snap, gd, kind="pet_house").jobs],
        "workers": {w: {"total": fw.total, "free_now": fw.free(now),
                        "next_free_at": fw.next_free_at(now)}
                    for w in WORKERS for fw in [forecast(snap, gd, kind=w)]},
        "helpers": helpers,
        "counter": stored.counter,
        "unread": store.unread_count(),
        "snapshots": store.count(),
    }


def history_payload(store, hours):
    since = time.time() - hours * 3600
    rows = store.resource_history(since)
    keys = ("t", "gold", "elixir", "dark_elixir", "gems", "free", "total")
    return {"hours": hours, "points": [dict(zip(keys, r)) for r in rows]}


def _latest_with_gd(store):
    stored = store.latest()
    if stored is None:
        raise ApiError(409, "no village snapshot yet: run a capture first")
    gd = game_data(stored.game_version)
    if gd is None:
        raise ApiError(409, "game data not decoded: run python -m village static")
    return stored, gd


def catalog_payload(store):
    stored, gd = _latest_with_gd(store)
    return {"items": catalog(stored.snapshot, gd)}


def plan_payload(store):
    items = store.plan()
    stored = store.latest()
    gd = game_data(stored.game_version) if stored else None
    if stored is None or gd is None:
        return {"items": [dict(i, name=f"#{i['data_id']}") for i in items], "schedule": [],
                "current": [], "builders": 0, "slots": {}, "worker_names": WORKER_NAMES,
                "now": time.time()}
    snap = stored.snapshot
    now = time.time()
    fc = {w: forecast(snap, gd, kind=w) for w in WORKERS}
    planned, lanes = preview(snap, gd, items, fc, now, PREVIEW_STEPS)
    by_item = {}
    for p in planned:
        by_item.setdefault(p.item_id, []).append(p)
    sec_worker = {c["id"]: c["worker"] for c in catalog(snap, gd)}
    out = []
    for i in items:
        steps = by_item.get(i["id"], [])
        cost = {}
        for p in steps:
            cost[p.step.resource] = cost.get(p.step.resource, 0) + p.step.cost
        out.append(dict(i, name=gd.name(i["data_id"]), max_level=cap(gd, snap, i["data_id"]),
                        worker=sec_worker.get(i["data_id"], "builder"),
                        steps=len(steps), work_seconds=sum(p.step.seconds for p in steps),
                        cost=cost, starts_at=steps[0].start if steps else None,
                        done_at=max((p.end for p in steps), default=None)))
    schedule = [{"item_id": p.item_id, "name": gd.name(p.step.data), "from": p.step.from_level,
                 "to": p.step.to_level, "worker": p.worker, "lane": p.lane, "start": p.start,
                 "end": p.end, "resource": p.step.resource, "cost": p.step.cost}
                for p in planned[:160]]
    # Current real jobs (Goblin jobs don't hold a real slot), per worker.
    current = [{"name": gd.name(j.data), "from": j.from_level, "to": j.from_level + 1,
                "end": j.ends_at, "worker": w} for w, f in fc.items() for j in f.real_jobs()]
    return {"items": out, "schedule": schedule, "current": current,
            "builders": fc["builder"].total,
            "slots": {w: f.total for w, f in fc.items()}, "worker_names": WORKER_NAMES,
            "now": now, "truncated": len(planned) >= PREVIEW_STEPS}


HIDDEN_SETTINGS = {"player_tag"}   # the active account: changed by switching accounts


def settings_payload(store):
    stored = store.settings()
    return {"settings": [{"key": s.key, "group": s.group, "label": s.label, "type": s.type,
                          "help": s.help, "phase": s.phase, "min": s.min, "max": s.max,
                          "default": s.default, "value": stored.get(s.key, s.default),
                          "custom": s.key in stored} for s in SETTINGS
                         if s.key not in HIDDEN_SETTINGS]}


# --- actions ------------------------------------------------------------------

_capture_proc = None
_capture_lock = threading.Lock()


def _start_game_job(args, what):
    """Run `python -m village <args>` in the background. Only one job that
    drives the game at a time (the capture lock enforces it across processes)."""
    from .capture import is_capture_running
    global _capture_proc
    with _capture_lock:
        if (_capture_proc and _capture_proc.poll() is None) or is_capture_running():
            raise ApiError(409, "the bot is already using the game (capture or dry run running)")
        log = open(os.path.join(REPO, "data", "web_jobs.log"), "a", encoding="utf-8")
        log.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} {what} started from the dashboard\n")
        log.flush()
        _capture_proc = subprocess.Popen([sys.executable, "-m", "village", *args], cwd=REPO,
                                         stdout=log, stderr=subprocess.STDOUT,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        log.close()  # the child keeps its own handle
    return {"started": True}


def start_capture(store):
    tag = store.settings().get("player_tag", "") or ""
    return _start_game_job(["capture"] + (["--tag", tag] if tag else []), "capture")


def start_dryrun(store, body=None):
    """Dry run (opens the upgrade dialog, never confirms) of one item
    (body {"data_id": N}) or of the queue's next step."""
    data_id = _int_or_none((body or {}).get("data_id"), "data_id")
    if data_id is None:
        if not store.plan():
            raise ApiError(409, "the upgrade queue is empty")
        return _start_game_job(["dryrun", "--queue"], "dry run")
    stored, gd = _latest_with_gd(store)
    if gd.get(data_id) is None:
        raise ApiError(400, f"unknown object {data_id}")
    item = next((c for c in catalog(stored.snapshot, gd) if c["id"] == data_id), None)
    if item is not None and not item["steps_left"]:
        raise ApiError(409, f"{item['name']} is already at the highest level available now "
                            f"({item['max_level']}); there is nothing to test")
    result = _start_game_job(["dryrun", "--id", str(data_id)], f"test of {gd.name(data_id)}")
    store.kv_set(test_key(store.active_tag(), data_id), {"state": "running", "at": int(time.time())})
    return result


def test_results(store):
    """{data_id: last Test result} of the active account, for the page."""
    prefix = test_key(store.active_tag(), "")
    rows = store.db.execute("SELECT key, value FROM kv WHERE key LIKE ? || '%'", (prefix,)).fetchall()
    out = {}
    for k, v in rows:
        if not k[len(prefix):].isdigit():
            continue
        r = json.loads(v)
        # a test that never reported back (process died) shouldn't spin forever
        if r.get("state") == "running" and time.time() - r.get("at", 0) > 15 * 60:
            r = {"state": "done", "found": False, "reason": "the test did not finish", "at": r["at"]}
        out[int(k[len(prefix):])] = r
    return out


# --- accounts -------------------------------------------------------------------

JOB_STALE = 15 * 60      # a running account job without progress for this long is dead


def accounts_payload(store):
    from .orchestrator import pid_alive
    active = store.active_tag()
    out = []
    for a in store.accounts():
        latest = store.latest(tag=a["tag"])
        seen = store.kv_get(store.akey("player", a["tag"])) or {}
        th = latest.snapshot.level_of(TOWN_HALL) if latest else a["town_hall"]
        out.append({"tag": a["tag"], "scid": a["scid"], "village": a["village"],
                    "display_name": a["display_name"],
                    "name": a["display_name"] or seen.get("name") or a["village"] or a["scid"] or a["tag"],
                    "town_hall": th, "xp": seen.get("xp"), "clan": seen.get("clan"),
                    "synced_at": latest.snapshot.timestamp if latest else None,
                    "can_switch": bool(a["scid"]) or a["tag"] == active,
                    "active": a["tag"] == active})
    job = store.kv_get("account_job")
    if job and job.get("state") == "running":
        last = max([job.get("started_at", 0)] + [t for t, _ in job.get("steps", [])])
        if not pid_alive(job.get("pid")) or time.time() - last > JOB_STALE:
            job.update(state="failed", step="Failed", ended_at=int(time.time()),
                       message="the account job stopped without finishing")
            store.kv_set("account_job", job)
    return {"active": active, "accounts": out, "job": job, "now": time.time()}


def _job_running(store):
    job = accounts_payload(store)["job"]
    return bool(job and job.get("state") == "running")


def account_switch(store, body):
    tag = body.get("tag")
    acct = store.account(tag) if isinstance(tag, str) else None
    if acct is None:
        raise ApiError(400, "unknown account")
    if not acct["scid"] and tag != store.active_tag():
        raise ApiError(409, "this account's Supercell ID name is not known: use Find accounts first")
    if _job_running(store):
        raise ApiError(409, "an account switch is already in progress")
    if farm_payload(store)["current"]:
        raise ApiError(409, "a farming run is active: stop it before switching accounts")
    orch = store.kv_get("orchestrator") or {}
    if orch.get("running"):
        raise ApiError(409, "a session is running: switch after it ends")
    result = _start_game_job(["account", "switch", "--tag", tag], f"switch to {tag}")
    store.kv_set("account_job", {"kind": "switch", "target": tag, "state": "running",
                                 "step": "Starting", "steps": [], "started_at": int(time.time()),
                                 "ended_at": None, "message": "", "pid": _capture_proc.pid})
    return result


def account_discover(store):
    if _job_running(store):
        raise ApiError(409, "an account job is already in progress")
    if farm_payload(store)["current"]:
        raise ApiError(409, "a farming run is active: stop it first")
    result = _start_game_job(["account", "discover"], "find accounts")
    store.kv_set("account_job", {"kind": "discover", "target": None, "state": "running",
                                 "step": "Starting", "steps": [], "started_at": int(time.time()),
                                 "ended_at": None, "message": "", "pid": _capture_proc.pid})
    return result


def _kill(pid):
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=30)


def restart_emulator(store, body):
    """Close LDPlayer completely and start it and the game again. If the bot
    is using the game, refuse unless body.force: then the session / farming
    run / test holding it is stopped first (a hung emulator hangs them too)."""
    from .capture import is_capture_running
    from .orchestrator import pid_alive
    global _capture_proc
    if _job_running(store):
        raise ApiError(409, "a switch or restart is already in progress")
    force = body.get("force") is True
    running = [x for x in store.sessions(20, tag=None)
               if x["status"] == "running" and pid_alive(x["pid"])]
    farm = farm_payload(store)["current"]
    web_job = _capture_proc is not None and _capture_proc.poll() is None
    busy = running or farm or web_job or is_capture_running()
    if busy and not force:
        what = ("a session" if running else "a farming run" if farm
                else "a capture, test or other job")
        raise ApiError(409, f"{what} is using the game right now")
    if busy:
        for x in running:
            _kill(x["pid"])
            store.end_session(x["id"], "interrupted",
                              (x["summary"] + "\n" if x["summary"] else "")
                              + "stopped: LDPlayer restarted from the dashboard")
        if farm:
            if farm.get("pid") and pid_alive(farm["pid"]):
                _kill(farm["pid"])
            store.update_farm_run(farm["id"], status="stopped", ended_at=int(time.time()),
                                  message="stopped: LDPlayer restarted from the dashboard")
        if web_job:
            _kill(_capture_proc.pid)
            _capture_proc.wait(timeout=30)
        time.sleep(2)         # the OS releases the game lock when the holder exits
    result = _start_game_job(["emulator", "restart"], "LDPlayer restart")
    store.kv_set("account_job", {"kind": "restart", "target": store.active_tag(), "state": "running",
                                 "step": "Starting", "steps": [], "started_at": int(time.time()),
                                 "ended_at": None, "message": "", "pid": _capture_proc.pid})
    store.notify("info", "emulator", "LDPlayer restart started from the dashboard"
                 + (" (stopped what was using the game)" if busy else ""), tag=None)
    return result


def account_update(store, body):
    tag = body.get("tag")
    if not isinstance(tag, str) or store.account(tag) is None:
        raise ApiError(400, "unknown account")
    name = body.get("display_name")
    if name is not None and (not isinstance(name, str) or len(name.strip()) > 30):
        raise ApiError(400, "display_name: text up to 30 characters")
    store.upsert_account(tag, display_name=(name or "").strip() or None)
    return {"ok": True}


ORCH_ALIVE = 90        # seconds without an orchestrator heartbeat = not running


def automation_payload(store):
    from .settings import effective
    cfg = effective(store)
    orch = store.kv_get("orchestrator") or {}
    now = time.time()
    alive = bool(orch) and now - orch.get("heartbeat", 0) < ORCH_ALIVE
    return {"now": now, "alive": alive, "orchestrator": orch,
            "paused": bool(cfg["automation_paused"]), "dry_run": bool(cfg["dry_run"]),
            "farm_enabled": bool(cfg["farm_enabled"]),
            "pending_commands": len(store.pending_commands()),
            "sessions": store.sessions(15)}


def automation_update(store, body):
    clean = {}
    for key in ("paused", "dry_run", "farm_enabled"):
        if key in body:
            if not isinstance(body[key], bool):
                raise ApiError(400, f"{key} must be true or false")
            clean["automation_paused" if key == "paused" else key] = body[key]
    if not clean:
        raise ApiError(400, "send paused, dry_run and/or farm_enabled")
    for k, v in clean.items():
        store.set_setting(k, v)
    if clean.get("dry_run") is False:
        store.notify("warning", "web", "Dry run turned OFF: sessions will now really start "
                     "upgrades from the queue", "Turned off from the dashboard.")
    return {"saved": sorted(clean)}


def automation_run_now(store):
    if any(c["kind"] == "run_session" for c in store.pending_commands()):
        raise ApiError(409, "a session is already requested")
    store.add_command("run_session")
    alive = automation_payload(store)["alive"]
    return {"queued": True, "orchestrator_running": alive}


FARM_STALE = 20 * 60   # a running farm with no progress for this long is dead


def farm_payload(store):
    """The active account's runs; `current` is a run of ANY account (the game
    is busy either way)."""
    runs = store.farm_runs(10)
    every = store.farm_runs(10, tag=None)
    now = time.time()
    for r in every:
        if r["status"] in ("queued", "running"):
            last = r["heartbeat_at"] or r["created_at"]
            if now - last > FARM_STALE:
                store.update_farm_run(r["id"], status="failed", ended_at=int(now),
                                      message=f"no progress for {FARM_STALE // 60} min "
                                              "(the farming process stopped)")
                r.update(status="failed", ended_at=int(now))
                for mine in runs:
                    if mine["id"] == r["id"]:
                        mine.update(status="failed", ended_at=int(now))
    current = next((r for r in every if r["status"] in ("queued", "running")), None)
    return {"current": current, "runs": runs, "now": now}


def farm_start(store, body):
    from .farm import validate as validate_farm
    try:
        params = validate_farm(body)
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    if farm_payload(store)["current"]:
        raise ApiError(409, "a farming run is already active")
    run_id = store.create_farm_run(params)
    try:
        _start_game_job(["farm", "--run-id", str(run_id)], f"farming run {run_id}")
    except ApiError:
        store.update_farm_run(run_id, status="failed", ended_at=int(time.time()),
                              message="not started: the bot was busy with the game")
        raise
    return {"id": run_id}


def farm_stop(store):
    current = farm_payload(store)["current"]
    if not current:
        raise ApiError(409, "no farming run is active")
    store.update_farm_run(current["id"], stop_requested=1,
                          message="stop requested: finishing the current attack")
    return {"ok": True}


def _int_or_none(v, name):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise ApiError(400, f"{name} must be a number")
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ApiError(400, f"{name} must be a number") from None


def plan_add(store, body):
    data_id = _int_or_none(body.get("data_id"), "data_id")
    if data_id is None:
        raise ApiError(400, "data_id is required")
    stored, gd = _latest_with_gd(store)
    if gd.get(data_id) is None:
        raise ApiError(400, f"unknown object {data_id}")
    target = _int_or_none(body.get("target_level"), "target_level")
    return {"id": store.add_plan_item(data_id, target)}


def plan_update(store, body):
    item_id = _int_or_none(body.get("id"), "id")
    fields = {}
    if "target_level" in body:
        fields["target_level"] = _int_or_none(body["target_level"], "target_level")
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise ApiError(400, "enabled must be true or false")
        fields["enabled"] = body["enabled"]
    if "note" in body:
        if not isinstance(body["note"], str) or len(body["note"]) > 200:
            raise ApiError(400, "note must be text up to 200 characters")
        fields["note"] = body["note"]
    if not store.update_plan_item(item_id, **fields):
        raise ApiError(404, "no such queue item")
    return {"ok": True}


def plan_order(store, body):
    ids = body.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise ApiError(400, "ids must be a list of numbers")
    try:
        store.reorder_plan(ids)
    except ValueError as e:
        raise ApiError(409, str(e)) from None
    return {"ok": True}


def settings_update(store, body):
    if not isinstance(body, dict) or not body:
        raise ApiError(400, "send {key: value}")
    clean = {}
    for k, v in body.items():
        if k in HIDDEN_SETTINGS:
            raise ApiError(400, "switch accounts with the account menu at the top")
        try:
            clean[k] = validate(k, v)
        except ValueError as e:
            raise ApiError(400, str(e)) from None
    for k, v in clean.items():
        store.set_setting(k, v)
    return {"saved": sorted(clean)}


# --- HTTP plumbing ------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "VillageDashboard/1"
    watch = None     # set by serve()
    db_path = None   # None = the default database

    def log_message(self, fmt, *args):  # quiet: no per-request console spam
        pass

    def _send(self, status, body, ctype="application/json; charset=utf-8", cache="no-store"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _static(self, rel):
        path = os.path.normpath(os.path.join(STATIC, rel))
        if not path.startswith(STATIC + os.sep) or not os.path.isfile(path):
            return self._send(404, {"error": "not found"})
        # Game artwork never changes: let the browser keep it for a day.
        cache = "max-age=86400" if path.startswith(IMG + os.sep) else "no-store"
        with open(path, "rb") as f:
            self._send(200, f.read(), CONTENT_TYPES.get(os.path.splitext(path)[1],
                                                         "application/octet-stream"), cache)

    def _shot(self, name):
        """Dry-run dialog screenshots (data/screens/dryrun/*.png)."""
        path = os.path.normpath(os.path.join(SHOTS, name))
        if (not path.startswith(SHOTS + os.sep) or not path.endswith(".png")
                or not os.path.isfile(path)):
            return self._send(404, {"error": "not found"})
        with open(path, "rb") as f:
            self._send(200, f.read(), "image/png")

    def _dispatch(self, method):
        url = urlparse(self.path)
        route = url.path.rstrip("/") or "/"
        if method == "GET" and route.startswith("/shots/dryrun/"):
            return self._shot(route[len("/shots/dryrun/"):])
        if method == "GET" and not route.startswith("/api/"):
            return self._static("index.html" if route == "/" else route.lstrip("/"))
        store = Store(self.db_path) if self.db_path else Store()
        try:
            body = {}
            if method == "POST":
                if self.headers.get("X-Village") != "1":
                    raise ApiError(403, "missing X-Village header")
                length = int(self.headers.get("Content-Length") or 0)
                if length > 64 * 1024:
                    raise ApiError(413, "request too large")
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    raise ApiError(400, "body must be JSON") from None
                if not isinstance(body, dict):
                    raise ApiError(400, "body must be a JSON object")
            routes = {
                ("GET", "/api/state"): lambda: state_payload(store),
                ("GET", "/api/history"): lambda: history_payload(
                    store, min(24 * 30, float(parse_qs(url.query).get("hours", ["72"])[0]))),
                ("GET", "/api/catalog"): lambda: catalog_payload(store),
                ("GET", "/api/plan"): lambda: plan_payload(store),
                ("POST", "/api/plan/add"): lambda: plan_add(store, body),
                ("POST", "/api/plan/update"): lambda: plan_update(store, body),
                ("POST", "/api/plan/delete"): lambda: {
                    "ok": store.delete_plan_item(_int_or_none(body.get("id"), "id"))},
                ("POST", "/api/plan/order"): lambda: plan_order(store, body),
                ("GET", "/api/settings"): lambda: settings_payload(store),
                ("POST", "/api/settings"): lambda: settings_update(store, body),
                ("GET", "/api/notifications"): lambda: {
                    "items": store.notifications(), "unread": store.unread_count()},
                ("POST", "/api/notifications/ack"): lambda: {
                    "acked": store.ack(_int_or_none(body.get("id"), "id"))},
                ("GET", "/api/status"): lambda: self._status(),
                ("POST", "/api/actions/capture"): lambda: start_capture(store),
                ("POST", "/api/actions/dryrun"): lambda: start_dryrun(store, body),
                ("GET", "/api/tests"): lambda: {"results": test_results(store)},
                ("GET", "/api/automation"): lambda: automation_payload(store),
                ("POST", "/api/automation"): lambda: automation_update(store, body),
                ("POST", "/api/automation/run"): lambda: automation_run_now(store),
                ("GET", "/api/farm"): lambda: farm_payload(store),
                ("POST", "/api/farm/start"): lambda: farm_start(store, body),
                ("POST", "/api/farm/stop"): lambda: farm_stop(store),
                ("GET", "/api/accounts"): lambda: accounts_payload(store),
                ("POST", "/api/accounts/switch"): lambda: account_switch(store, body),
                ("POST", "/api/accounts/discover"): lambda: account_discover(store),
                ("POST", "/api/accounts/update"): lambda: account_update(store, body),
                ("POST", "/api/actions/restart-emulator"): lambda: restart_emulator(store, body),
            }
            handler = routes.get((method, route))
            if handler is None:
                raise ApiError(404, "no such endpoint")
            self._send(200, handler())
        except ApiError as e:
            self._send(e.status, {"error": str(e)})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 - report, don't kill the server
            try:
                store.notify("error", "web", f"Dashboard error on {route}", repr(e))
            except Exception:  # noqa: BLE001
                pass
            self._send(500, {"error": "internal error (see notifications)"})
        finally:
            store.close()

    def _status(self):
        from .capture import is_capture_running
        st = dict(self.watch.status) if self.watch else {"state": "unknown"}
        st["capture_running"] = is_capture_running()
        from .winfocus import desktop_problem
        st["desktop_problem"] = desktop_problem()
        st["desktop_usable"] = st["desktop_problem"] is None
        return st

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def serve(host="0.0.0.0", port=8765, db_path=None, watch=True):
    """Create the server (call serve_forever on it). db_path/watch exist for tests."""
    Handler.db_path = db_path
    (Store(db_path) if db_path else Store()).close()  # run migrations once
    Handler.watch = EmulatorWatch() if watch else None
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd
