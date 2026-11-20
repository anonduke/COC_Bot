"""Session runner (Phases 7-8): one visit to the game.

  1. emulator ready, home screen                (Phase 4)
  2. capture the village (sync in)               (Phase 2)
  3. resolve upgrades left unverified by an earlier, interrupted session:
     never repeat them, look at the export instead
  4. while a builder is free and the queue has a candidate:   (Phase 6)
       not affordable and farming on  -> farm for it, re-capture  (Phase 7)
       dry run on                     -> open + close the dialog  (Phase 5)
       otherwise: journal INTENT, confirm, re-capture, diff:      (Phase 5b)
                  VERIFIED if the export shows it started, else NOT_APPLIED
       gem guard: any gem drop pauses automation and ends the session
  5. optionally close the emulator

Game actions come in through `Game`, so the logic is tested with fakes.
"""
import json
import time
import traceback
from dataclasses import dataclass, field

from . import planner
from .builders import WORKERS, forecast
from .diff import diff
from .settings import effective


@dataclass
class Game:
    """The actions a session needs. Real ones: real_game()."""
    ensure_ready: object      # () -> None, raises on failure
    capture: object           # () -> capture.Capture
    upgrade: object           # (name, from_level, confirm) -> upgrader.DryRun
    farm: object              # (params) -> (status, message)
    gamedata: object = None
    stop_emulator: object = None   # () -> None


@dataclass
class Outcome:
    status: str = "done"                # done / failed
    lines: list = field(default_factory=list)
    upgrades: int = 0

    def add(self, line):
        self.lines.append(line)


def _save(store, cap, game):
    """Store a capture. Always with the game version: the dashboard finds names
    and costs through the latest snapshot's version."""
    version = game.gamedata.version if game.gamedata else None
    return store.save_snapshot(cap.snapshot, cap.resources, game_version=version,
                               captured_at=cap.home_at, counter=cap.counter,
                               player=getattr(cap, "player", None))


def _state(cap):
    r = cap.resources
    return {"gold": r.gold, "elixir": r.elixir, "dark_elixir": r.dark_elixir}


def _fills(cap):
    return getattr(cap, "fills", None) or {}


def _verified(store, before_id, after_snapshot, data_id, from_level):
    """True if the export shows the upgrade of data_id from from_level started
    (or already completed) since the snapshot `before_id`."""
    before = store.get(before_id)
    if before is None:
        return False
    d = diff(before.snapshot, after_snapshot)
    return any(c.data == data_id and c.level == from_level
               for c in (*d.started, *d.leveled, *d.finished))


def resolve_pending(store, snapshot_id, snapshot, out):
    """INTENT actions from earlier sessions (crash between Confirm and the
    check): decide from the export, never by repeating the action."""
    for a in store.actions(status="intent"):
        ok = _verified(store, a["before_snapshot"], snapshot, a["data_id"], a["from_level"])
        store.update_action(a["id"], "verified" if ok else "not_applied",
                            (a["detail"] + "\n" if a["detail"] else "")
                            + f"resolved from snapshot #{snapshot_id} after an interruption")
        out.add(f"earlier upgrade {a['name']} {a['from_level']}->{a['from_level'] + 1}: "
                f"{'verified' if ok else 'not applied'} (resolved after interruption)")


WORKER_LABEL = {"builder": "builder", "lab": "laboratory", "pet_house": "pet house"}


def _run_worker(store, game, sid, cfg, worker, state, tried, dry, gems0, out, log, max_steps):
    """Fill the free REAL slots of one worker from the queue (Goblin slots
    are never used: the export-based forecast doesn't count them)."""
    dry_runs = 0          # dry runs don't use a slot, so count them against `free`
    for _ in range(max_steps):
        cap = state["cap"]
        free = forecast(cap.snapshot, game.gamedata, kind=worker).free(time.time())
        if free - dry_runs < 1:
            if not dry_runs:
                out.add(f"no free {WORKER_LABEL[worker]}")
            return
        all_c = planner.candidates(cap.snapshot, game.gamedata, store.plan(),
                                   _state(cap), _fills(cap), tried, worker=worker)
        cands = [c for c in all_c if not c.reason]
        if not cands:
            out.add(f"{WORKER_LABEL[worker]}: nothing in the queue to start")
            for c in all_c:
                out.add(f"{c.name} {c.from_level}->{c.from_level + 1}: {c.reason}")
            return
        c = cands[0]
        tried.add((c.data_id, c.from_level))
        label = f"{c.name} {c.from_level}->{c.from_level + 1}"

        if c.affordable is False and cfg["farm_enabled"] and not dry:
            target = planner.farm_target(c, cfg["farm_margin_pct"])
            log(f"session: farming for {label}: {c.resource} to {target:,}")
            status, msg = game.farm({
                "mode": "target", c.resource: target,
                "max_attacks": cfg["farm_max_attacks"], "max_minutes": cfg["farm_max_minutes"]})
            out.add(f"farmed for {label}: {status}, {msg}")
            store.add_action(sid, "farm", status, c.data_id, c.name, c.from_level,
                             detail=f"target {c.resource} {target:,}: {msg}")
            game.ensure_ready()
            state["cap"] = game.capture()
            state["snap_id"] = _save(store, state["cap"], game)
            if status not in ("done",):
                return

        if dry:
            r = game.upgrade(c.name, c.from_level, False, worker)
            store.add_action(sid, "dry_run", "dry_run", c.data_id, c.name, c.from_level,
                             state["snap_id"], r.reason + "".join("\n" + p for p in r.screenshots))
            out.add(f"dry run {label}: {'found' if r.found else 'not found'}"
                    + ("" if r.affordable in (None, True) else ", not affordable"))
            dry_runs += 1
            continue

        aid = store.add_action(sid, "upgrade", "intent", c.data_id, c.name, c.from_level,
                               state["snap_id"])
        r = game.upgrade(c.name, c.from_level, True, worker)
        detail = r.reason + "".join("\n" + p for p in r.screenshots)
        if not r.confirm_tapped:
            store.update_action(aid, "skipped", detail)
            out.add(f"{label}: not started ({r.reason})")
            if r.found and r.affordable is False:
                return              # not enough resources: later items are no cheaper bet
            continue
        cap2 = game.capture()
        snap2 = _save(store, cap2, game)
        ok = _verified(store, state["snap_id"], cap2.snapshot, c.data_id, c.from_level)
        store.update_action(aid, "verified" if ok else "not_applied", detail)
        out.add(f"{label}: {'started (verified by export)' if ok else 'NOT confirmed by export'}")
        out.upgrades += ok
        gems = cap2.resources.gems
        if gems0 is not None and gems is not None and gems < gems0:
            store.set_setting("automation_paused", True)
            store.notify("error", "gem guard",
                         f"Gems dropped {gems0:,} -> {gems:,} during a session: automation paused",
                         "Check the after_confirm screenshots under Alerts / Sessions.")
            out.add("GEM GUARD: gems dropped, automation paused")
            out.status = "failed"
            state["stop"] = True
            return
        state["cap"], state["snap_id"] = cap2, snap2
        if not ok:
            return


def run_session(store, game, trigger, log=print, max_steps=10):
    out = Outcome()
    sid = store.start_session(trigger)
    cfg = effective(store)
    try:
        log("session: getting the game ready")
        game.ensure_ready()
        cap = game.capture()
        snap_id = _save(store, cap, game)
        out.add(f"captured village (snapshot #{snap_id})")
        resolve_pending(store, snap_id, cap.snapshot, out)
        gems0 = cap.resources.gems
        tried = set()
        dry = bool(cfg["dry_run"])
        state = {"cap": cap, "snap_id": snap_id, "stop": False}
        for worker in WORKERS:
            if state["stop"]:
                break
            _run_worker(store, game, sid, cfg, worker, state, tried, dry, gems0, out, log, max_steps)

        if cfg["stop_emulator_between"] and game.stop_emulator:
            game.stop_emulator()
            out.add("emulator closed")
    except Exception as e:  # noqa: BLE001 - the session reports, the orchestrator carries on
        out.status = "failed"
        out.add(f"error: {type(e).__name__}: {e}")
        log(traceback.format_exc())
    summary = "\n".join(out.lines)
    store.end_session(sid, out.status, summary)
    store.kv_set(store.akey("last_session"), {"id": sid, "status": out.status, "upgrades": out.upgrades,
                                  "ended_at": int(time.time()), "trigger": trigger})
    if out.status != "done":
        store.notify("error", "session", f"Session #{sid} ({trigger}) failed", summary)
    elif out.upgrades:
        store.notify("info", "session", f"Session #{sid}: {out.upgrades} upgrade(s) started", summary)
    return sid, out


# --- real game wiring ------------------------------------------------------------

def real_game(store, log=print):
    from . import accounts, adb, capture, emulator, farm, gamedata, upgrader
    from .resources import bar_fill

    tag = store.active_tag()
    game = Game(ensure_ready=None, capture=None, upgrade=None, farm=None)

    def ready():
        emulator.ensure_ready(log)
        if game.gamedata is None:   # needs adb, so only once the emulator is up
            game.gamedata = gamedata.ensure_static_data()

    def do_capture():
        # On another account (switched by hand)? capture_for switches back.
        cap = (accounts.capture_for(store, tag, log) if tag
               else capture.capture_export(log=log))
        img = adb.screencap()   # back on the home screen: read the storage bars
        cap.fills = {"gold_fill": bar_fill(img, "gold"), "elixir_fill": bar_fill(img, "elixir"),
                     "dark_fill": bar_fill(img, "dark_elixir")}
        return cap

    def do_upgrade(name, from_level, confirm, worker="builder"):
        fn = upgrader.upgrade if confirm else upgrader.dry_run
        return fn(name, from_level, log=log, worker=worker)

    def do_farm(params):
        p = farm.validate(params)
        run_id = store.create_farm_run(p)
        farm.run(run_id)
        r = store.farm_run(run_id)
        return r["status"], r["message"]

    game.ensure_ready, game.capture, game.upgrade, game.farm = ready, do_capture, do_upgrade, do_farm
    game.stop_emulator = lambda: emulator.stop(log)
    return game


def main(trigger):
    """python -m village session --trigger X (started by the orchestrator)."""
    from .capture import capture_lock
    from .power import keep_display_on
    from .store import Store
    store = Store()
    store.pin()       # one account for the whole session, whatever the dashboard does
    with capture_lock(wait=60), keep_display_on():
        sid, out = run_session(store, real_game(store), trigger,
                               log=lambda m: print(m, flush=True))
    print(json.dumps({"session": sid, "status": out.status, "upgrades": out.upgrades}))
    print("\n".join(out.lines))
    return 0 if out.status == "done" else 1
