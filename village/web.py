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
from .builders import forecast
from .catalog import HERO_HALL, TOWN_HALL, catalog, max_level, preview, step
from .settings import SETTINGS, validate
from .store import Store

PREVIEW_STEPS = 600   # queue steps simulated for the preview
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
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
        "WHERE export_ts <= ? AND gold IS NOT NULL ORDER BY export_ts DESC LIMIT 1",
        (snap.timestamp - 20 * 3600,)).fetchone()
    r = stored.resources
    return {
        "now": now,
        "snapshot": {"id": stored.id, "tag": snap.tag, "exported_at": snap.timestamp,
                     "captured_at": stored.captured_at, "game_version": stored.game_version,
                     "town_hall": snap.level_of(TOWN_HALL), "hero_hall": snap.level_of(HERO_HALL)},
        "resources": {"gold": r.gold, "elixir": r.elixir, "dark_elixir": r.dark_elixir,
                      "gems": r.gems},
        "resources_before": dict(day_ago) if day_ago else None,
        "builders": {"total": f.total, "source": f.total_source, "free_now": f.free(now),
                     "next_free_at": f.next_free_at(now), "jobs": builders},
        "lab": [_job_dict(gd, j, "lab") for j in snap.jobs("lab")],
        "pets": [_job_dict(gd, j, "pet_house") for j in snap.jobs("pet_house")],
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
                "current": [], "builders": 0, "now": time.time()}
    snap = stored.snapshot
    th, hh = snap.level_of(TOWN_HALL) or 1, snap.level_of(HERO_HALL)
    now = time.time()
    f = forecast(snap, gd, stored.counter)
    planned, lanes = preview(snap, gd, items, f, now, PREVIEW_STEPS)
    by_item = {}
    for p in planned:
        by_item.setdefault(p.item_id, []).append(p)
    out = []
    for i in items:
        steps = by_item.get(i["id"], [])
        cap = max_level(gd, i["data_id"], th, hh)
        cost = {}
        for p in steps:
            cost[p.step.resource] = cost.get(p.step.resource, 0) + p.step.cost
        out.append(dict(i, name=gd.name(i["data_id"]), max_level=cap,
                        steps=len(steps), work_seconds=sum(p.step.seconds for p in steps),
                        cost=cost, starts_at=steps[0].start if steps else None,
                        done_at=max((p.end for p in steps), default=None)))
    schedule = [{"item_id": p.item_id, "name": gd.name(p.step.data), "from": p.step.from_level,
                 "to": p.step.to_level, "lane": p.lane, "start": p.start, "end": p.end,
                 "resource": p.step.resource, "cost": p.step.cost}
                for p in planned[:120]]
    # Current jobs, so the timeline can show what each lane is busy with now.
    current = [{"name": gd.name(j.data), "from": j.from_level, "to": j.from_level + 1,
                "end": j.ends_at, "extra": j.extra} for j in f.jobs]
    return {"items": out, "schedule": schedule, "current": current, "builders": f.total,
            "now": now, "truncated": len(planned) >= PREVIEW_STEPS}


def settings_payload(store):
    stored = store.settings()
    return {"settings": [{"key": s.key, "group": s.group, "label": s.label, "type": s.type,
                          "help": s.help, "phase": s.phase, "min": s.min, "max": s.max,
                          "default": s.default, "value": stored.get(s.key, s.default),
                          "custom": s.key in stored} for s in SETTINGS]}


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


def start_dryrun(store):
    """Dry run of the queue's next step: opens the upgrade dialog, never confirms."""
    if not store.plan():
        raise ApiError(409, "the upgrade queue is empty")
    return _start_game_job(["dryrun", "--queue"], "dry run")


FARM_STALE = 20 * 60   # a running farm with no progress for this long is dead


def farm_payload(store):
    runs = store.farm_runs(10)
    now = time.time()
    for r in runs:
        if r["status"] in ("queued", "running"):
            last = r["heartbeat_at"] or r["created_at"]
            if now - last > FARM_STALE:
                store.update_farm_run(r["id"], status="failed", ended_at=int(now),
                                      message=f"no progress for {FARM_STALE // 60} min "
                                              "(the farming process stopped)")
                r.update(status="failed", ended_at=int(now))
    current = next((r for r in runs if r["status"] in ("queued", "running")), None)
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

    def _send(self, status, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _static(self, rel):
        path = os.path.normpath(os.path.join(STATIC, rel))
        if not path.startswith(STATIC + os.sep) or not os.path.isfile(path):
            return self._send(404, {"error": "not found"})
        with open(path, "rb") as f:
            self._send(200, f.read(), CONTENT_TYPES.get(os.path.splitext(path)[1],
                                                         "application/octet-stream"))

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
                ("POST", "/api/actions/dryrun"): lambda: start_dryrun(store),
                ("GET", "/api/farm"): lambda: farm_payload(store),
                ("POST", "/api/farm/start"): lambda: farm_start(store, body),
                ("POST", "/api/farm/stop"): lambda: farm_stop(store),
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
