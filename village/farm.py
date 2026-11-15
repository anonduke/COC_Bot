"""Farming runs: loop the existing attack until a goal or a budget is reached.

Started from the dashboard (python -m village farm --run-id N). The attack
itself is Ranked_Copy.attack(), unchanged; this module only decides when to
attack again and when to stop.

Goals (params["mode"]):
  full     until the chosen storages are full (bar fill >= 98.5 %):
           params["full"] in {"gold", "elixir", "both", "any"}
  attacks  a fixed number of successful attacks (params["attacks"])
  target   until gold/elixir/dark elixir reach params["gold"/"elixir"/"dark_elixir"]
Budgets, always on: params["max_attacks"], params["max_minutes"], and 6 failed
attacks in a row.

A stop request (dashboard Stop button) is honoured between attacks, never in
the middle of a battle. While a run is active it holds the capture lock, so
captures and dry runs wait instead of driving the game at the same time.

Ranked_Copy drives the real mouse on the LDPlayer window (pyautogui), so the
window must be visible and the PC not in use while farming.
"""
import json
import os
import sys
import time
import traceback

MODES = ("full", "attacks", "target")
FULL_CHOICES = ("gold", "elixir", "both", "any")
MAX_FAILS_IN_A_ROW = 6
DEFAULTS = {"mode": "full", "full": "both", "attacks": 10, "gold": None, "elixir": None,
            "dark_elixir": None, "max_attacks": 60, "max_minutes": 120, "ranked": False,
            "event_troops": False, "walls": False, "wall_every": 5}


def validate(params):
    """Clean dashboard input into a params dict, or raise ValueError."""
    if not isinstance(params, dict):
        raise ValueError("send a JSON object")
    p = dict(DEFAULTS)
    unknown = set(params) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown option(s): {', '.join(sorted(unknown))}")
    p.update(params)
    if p["mode"] not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    if p["full"] not in FULL_CHOICES:
        raise ValueError(f"full must be one of {', '.join(FULL_CHOICES)}")

    def num(key, lo, hi, optional=False):
        v = p[key]
        if v in (None, "") and optional:
            p[key] = None
            return
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise ValueError(f"{key} must be a number")
        try:
            v = int(v)
        except ValueError:
            raise ValueError(f"{key} must be a whole number") from None
        if not lo <= v <= hi:
            raise ValueError(f"{key} must be between {lo:,} and {hi:,}")
        p[key] = v

    num("attacks", 1, 500)
    num("max_attacks", 1, 500)
    num("max_minutes", 5, 24 * 60)
    num("wall_every", 1, 50)
    for k in ("gold", "elixir", "dark_elixir"):
        num(k, 0, 100_000_000, optional=True)
    for k in ("ranked", "event_troops", "walls"):
        if not isinstance(p[k], bool):
            raise ValueError(f"{k} must be true or false")
    if p["mode"] == "target" and not any(p[k] for k in ("gold", "elixir", "dark_elixir")):
        raise ValueError("set at least one of gold, elixir or dark elixir to reach")
    if p["mode"] == "attacks":
        p["max_attacks"] = max(p["max_attacks"], p["attacks"])
    return p


def goal_reached(p, state, attacks):
    """(reached, why). `state` has gold/elixir/dark_elixir values and
    *_fill bar fractions; missing readings never count as reached."""
    if p["mode"] == "attacks":
        return attacks >= p["attacks"], f"{attacks} attacks done"
    if not state:
        return False, ""
    if p["mode"] == "full":
        from .resources import FULL_AT
        full = {k: (state.get(f"{k}_fill") or 0) >= FULL_AT for k in ("gold", "elixir")}
        which = p["full"]
        ok = (all(full.values()) if which == "both" else any(full.values()) if which == "any"
              else full[which])
        names = [k for k, v in full.items() if v]
        return ok, f"storage full: {', '.join(names)}" if ok else ""
    wanted = {k: p[k] for k in ("gold", "elixir", "dark_elixir") if p[k]}
    if any(state.get(k) is None for k in wanted):
        return False, ""
    ok = all(state[k] >= v for k, v in wanted.items())
    return ok, "targets reached: " + ", ".join(f"{k} {state[k]:,}" for k in wanted) if ok else ""


def run_loop(p, attack, read_state, stop_requested, report, upgrade_walls=None,
             clock=time.time):
    """The farming loop, with the game behind callables so it can be tested.

    attack() -> bool, read_state() -> dict|None, stop_requested() -> bool,
    report(**progress), upgrade_walls() -> int (walls upgraded).
    Returns (status, message) with status in done / stopped / failed."""
    start = clock()
    attacks = fails = in_a_row = walls = 0
    state = read_state()
    report(attacks=0, fails=0, walls=0, state=state, start_state=state)
    while True:
        reached, why = goal_reached(p, state, attacks)
        if reached:
            return "done", why
        if stop_requested():
            return "stopped", f"stopped from the dashboard after {attacks} attacks"
        if attacks >= p["max_attacks"]:
            return "done", f"attack budget used ({attacks})"
        if clock() - start >= p["max_minutes"] * 60:
            return "done", f"time budget used ({p['max_minutes']} min)"
        if in_a_row >= MAX_FAILS_IN_A_ROW:
            return "failed", f"{in_a_row} attacks in a row failed"
        try:
            ok = bool(attack())
        except Exception as e:  # noqa: BLE001 - one bad attack must not end the run
            ok = False
            report(message=f"attack raised {type(e).__name__}: {e}")
        if ok:
            attacks, in_a_row = attacks + 1, 0
        else:
            fails, in_a_row = fails + 1, in_a_row + 1
        new = read_state()
        if new:
            state = new
        if ok and upgrade_walls and attacks % p["wall_every"] == 0:
            walls += upgrade_walls() or 0
        report(attacks=attacks, fails=fails, walls=walls, state=state,
               message=f"attack {'ok' if ok else 'failed'} ({attacks} done, {fails} failed)")


# --- wiring to the game and the store --------------------------------------------

def _state_from_screen():
    """Resources and bar fills read from an adb screenshot of the home screen."""
    from . import adb
    from .resources import bar_fill, read_resources
    try:
        img = adb.screencap()
    except adb.AdbError:
        return None
    r = read_resources(img)
    if r.gold is None and r.elixir is None:
        return None          # not on the home screen
    return {"gold": r.gold, "elixir": r.elixir, "dark_elixir": r.dark_elixir, "gems": r.gems,
            "gold_fill": bar_fill(img, "gold"), "elixir_fill": bar_fill(img, "elixir"),
            "dark_fill": bar_fill(img, "dark_elixir"), "at": int(time.time())}


def run(run_id):
    """Entry point for `python -m village farm --run-id N`."""
    from .capture import CaptureError, capture_lock
    from .power import keep_display_on
    from .store import Store

    store = Store()
    row = store.farm_run(run_id)
    if row is None or row["status"] != "queued":
        print(f"farm run {run_id} is not queued")
        return 2
    p = row["params"]
    store.update_farm_run(run_id, status="running", started_at=int(time.time()),
                          heartbeat_at=int(time.time()), pid=os.getpid(),
                          message="starting")

    def report(**kw):
        kw["heartbeat_at"] = int(time.time())
        store.update_farm_run(run_id, **kw)

    def stop_requested():
        r = store.farm_run(run_id)
        return bool(r and r["stop_requested"])

    status, message = "failed", ""
    try:
        with capture_lock(wait=30), keep_display_on():
            repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            os.chdir(repo)                      # Ranked_Copy uses relative paths
            if repo not in sys.path:
                sys.path.insert(0, repo)
            import threading
            import Ranked_Copy as R
            R.CONFIG["ranked_mode"] = p["ranked"]
            R.CONFIG["event_troops"] = p["event_troops"]
            R.cleanup_session()
            # Keep this run's evidence: Ranked_Copy's log and, for every failed
            # attack, a screenshot plus the window that had the foreground.
            import logging
            import pyautogui
            run_dir = os.path.join(repo, "data", "farm_runs", f"run_{run_id}")
            os.makedirs(run_dir, exist_ok=True)
            fh = logging.FileHandler(os.path.join(run_dir, "bot.log"), encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(funcName)s - %(message)s"))
            logging.getLogger().addHandler(fh)
            logging.getLogger().setLevel(logging.DEBUG)
            monitor_stop = threading.Event()
            threading.Thread(target=R._bot_monitor, args=(monitor_stop,), daemon=True).start()
            R.activate_game_window()
            report(message="farming")

            def evidence(tag):
                try:
                    import ctypes
                    u = ctypes.windll.user32
                    buf = ctypes.create_unicode_buffer(256)
                    u.GetWindowTextW(u.GetForegroundWindow(), buf, 256)
                    name = time.strftime("%H%M%S") + f"_{tag}.png"
                    pyautogui.screenshot(os.path.join(run_dir, name))
                    logging.warning(f"[FARM] {tag}: foreground window {buf.value!r}, saved {name}")
                    return buf.value
                except Exception as e:  # noqa: BLE001 - evidence must never break the run
                    logging.warning(f"[FARM] could not save evidence: {e}")
                    return "?"

            def attack():
                R._update_heartbeat()
                try:
                    ok = R.attack()
                except Exception:
                    logging.error(traceback.format_exc())
                    evidence("exception")
                    raise
                R._update_heartbeat()
                if ok:
                    R.attack_count += 1
                else:
                    fg = evidence("failed")
                    report(message=f"attack failed (foreground window: {fg!r}); see {run_dir}")
                return ok

            def walls():
                before = R.walls_this_session
                R.run_wall_upgrades()
                return R.walls_this_session - before

            try:
                status, message = run_loop(p, attack, _state_from_screen, stop_requested,
                                           report, walls if p["walls"] else None)
                if status == "failed":
                    message += f"; screenshots and log in {run_dir}"
            finally:
                monitor_stop.set()
                logging.getLogger().removeHandler(fh)
                fh.close()
    except CaptureError as e:
        status, message = "failed", f"could not start: {e}"
    except Exception as e:  # noqa: BLE001
        status, message = "failed", f"{type(e).__name__}: {e}"
        traceback.print_exc()
    store.update_farm_run(run_id, status=status, message=message, ended_at=int(time.time()),
                          heartbeat_at=int(time.time()))
    final = store.farm_run(run_id)
    severity = {"done": "info", "stopped": "info"}.get(status, "error")
    store.notify(severity, "farm", f"Farming {status}: {message}",
                 f"{final['attacks']} attacks, {final['fails']} failed, "
                 f"{final['walls']} walls upgraded\n" + json.dumps(final["state"] or {}))
    print(f"farm run {run_id}: {status}: {message}")
    return 0 if status in ("done", "stopped") else 1
