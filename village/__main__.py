"""Command line: python -m village <command>

    static            decode the game's data tables for the installed version
    capture           read the village export from the game and store it
                      (--repeat N [--pause S] for a reliability or 24 h run)
    show [--id N]     summarise the latest (or a given) stored snapshot
    builders          builder forecast from the latest snapshot
    verify [--hours]  check forecasts and builder counters across stored snapshots
    dryrun            find an upgrade in the Builder menu, open its dialog,
                      close it again (never confirms): --queue or --name/--from
    web               dashboard on http://<this PC>:8765/ (LAN)
"""
import argparse
import os
import sys
import time

from . import gamedata
from .builders import forecast, verify_series
from .power import keep_display_on
from .store import Store, test_key


def _fmt_eta(seconds):
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return f"{d}d {h}h {m}m" if d else f"{h}h {m}m"


def _clock(ts):
    return time.strftime("%a %H:%M", time.localtime(ts))


def _load_gamedata(version):
    """GameData for a game version, or None if it was never decoded."""
    if not version:
        return None
    try:
        return gamedata.GameData.load(gamedata.static_dir(version))
    except OSError:
        return None


def _namer(gd):
    return gd.name if gd else (lambda i: f"#{i}")


def counter_check(snap, counter, at, gd=None):
    """Compare the screen counter with the free builders computed from the
    export at time `at`. Returns (matches, computed_free) or (None, None) if
    there is no counter reading."""
    if counter is None:
        return None, None
    computed = forecast(snap, gd, counter).free(at)
    return computed == counter[0], computed


def cmd_static(args):
    gd = gamedata.ensure_static_data()
    print(f"game {gd.version}: {len(gd.objects)} objects")


def cmd_capture(args):
    from . import adb
    from .capture import CaptureError, TagMismatch, capture_export
    store = None if args.no_save else Store()
    version = adb.game_version()
    gd = _load_gamedata(version)
    ok = matches = checked = 0
    from .accounts import SwitchError, capture_for

    def capture_one():
        log = lambda m: print(m, flush=True)  # noqa: E731
        try:
            return capture_export(expected_tag=args.tag, screen_dir=args.screens, log=log)
        except TagMismatch:
            # The game is on another linked account: switch back to the
            # expected one when its Supercell ID name is known.
            if not store or not (store.account(args.tag) or {}).get("scid"):
                raise
            return capture_for(store, args.tag, log)

    with keep_display_on():
        for i in range(1, args.repeat + 1):
            t0 = time.time()
            try:
                cap = capture_one()
            except (CaptureError, SwitchError, adb.AdbError, OSError) as e:
                print(f"[{i}/{args.repeat}] {time.strftime('%H:%M')} FAILED after "
                      f"{time.time() - t0:.0f}s: {e}", flush=True)
                if store and "already running" not in str(e):
                    store.notify("error", "capture", "Village capture failed", str(e))
            else:
                ok += 1
                snap, res = cap.snapshot, cap.resources
                sid = store.save_snapshot(snap, res, game_version=version,
                                          captured_at=cap.home_at, counter=cap.counter,
                                          player=cap.player)                     if store else None
                match, computed = counter_check(snap, cap.counter, cap.home_at, gd)
                if match is not None:
                    checked += 1
                    matches += match
                    if not match and store:
                        store.notify("warning", "builders",
                                     f"Builder counter shows {cap.counter[0]} free, "
                                     f"forecast says {computed}",
                                     "A job may have ended during the capture; "
                                     "run `python -m village verify` for details.")
                    builders = (f"counter {cap.counter[0]}/{cap.counter[1]} vs computed "
                                f"{computed} {'OK' if match else 'MISMATCH'}")
                else:
                    builders = "counter unreadable"
                print(f"[{i}/{args.repeat}] {time.strftime('%H:%M')} ok in "
                      f"{time.time() - t0:.0f}s  {builders}  gold {res.gold} elixir {res.elixir} "
                      f"dark {res.dark_elixir} gems {res.gems}"
                      + (f"  saved #{sid}" if sid else ""), flush=True)
            if i < args.repeat:
                time.sleep(args.pause)
    print(f"{ok}/{args.repeat} captures succeeded; builder counter matched {matches}/{checked}")
    return 0 if ok == args.repeat and matches == checked else 1


def cmd_show(args):
    store = Store()
    stored = store.get(args.id) if args.id else store.latest()
    if stored is None:
        print("no snapshots stored")
        return 1
    snap = stored.snapshot
    name = _namer(_load_gamedata(stored.game_version))
    now = time.time()
    r = stored.resources
    print(f"snapshot {stored.id}  {snap.tag}  exported "
          f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(snap.timestamp))}"
          f"  game {stored.game_version or '?'}")
    print(f"TH {snap.level_of(1000001)}  gold {r.gold}  elixir {r.elixir}  "
          f"dark {r.dark_elixir}  gems {r.gems}")
    for kind in ("builder", "lab", "pet_house", "bb_builder", "star_lab", "unknown"):
        jobs = snap.jobs(kind)
        if jobs:
            print(f"{kind}: {len(jobs)} job(s)")
            for j in jobs:
                flags = " +helper" if j.helper else ""
                print(f"  {name(j.data)} {j.from_level}->{(j.from_level or 0) + 1}  "
                      f"done in {_fmt_eta(j.ends_at - now)}{flags}")
    return 0


def cmd_builders(args):
    stored = Store().latest()
    if stored is None:
        print("no snapshots stored")
        return 1
    gd = _load_gamedata(stored.game_version)
    name = _namer(gd)
    f = forecast(stored.snapshot, gd, stored.counter)
    now = time.time()
    age = now - stored.snapshot.timestamp
    print(f"from snapshot {stored.id}, exported {_fmt_eta(age)} ago")
    print(f"builders: {f.total} ({'in-game counter' if f.total_source == 'counter' else 'from export, may miss an idle extra builder'})")
    print(f"free now: {f.free(now)}")
    nxt = f.next_free_at(now)
    if nxt is not None and nxt > now:
        print(f"next free: {_clock(nxt)} (in {_fmt_eta(nxt - now)})")
    for j in f.jobs:
        state = "done" if j.ends_at <= now else f"{_clock(j.ends_at)}  in {_fmt_eta(j.ends_at - now)}"
        notes = []
        if j.extra:
            notes.append("extra builder")
        if j.helper:
            notes.append(f"apprentice; plain timer {_clock(j.timer_end)}")
        print(f"  {name(j.data):22} {j.from_level}->{j.from_level + 1}  {state}"
              + (f"  ({', '.join(notes)})" if notes else ""))
    return 0


def cmd_verify(args):
    store = Store()
    since = time.time() - args.hours * 3600 if args.hours else None
    stored = store.all(since=since)
    if len(stored) < 2:
        print("need at least two snapshots")
        return 1
    gd = _load_gamedata(stored[-1].game_version)
    name = _namer(gd)
    print(f"{len(stored)} snapshots, {_clock(stored[0].snapshot.timestamp)} to "
          f"{_clock(stored[-1].snapshot.timestamp)}")

    findings = verify_series([s.snapshot for s in stored], gd, args.tolerance)
    bad = [f for f in findings if f.kind != "ok"]
    for f in sorted(findings, key=lambda f: (f.kind == "ok", f.key)):
        section, data, lvl = f.key
        print(f"  {f.kind:5} {name(data)} {lvl}->{lvl + 1}: {f.detail}")

    checked = mismatched = 0
    for prev, s in zip([None] + stored[:-1], stored):
        match, computed = counter_check(s.snapshot, s.counter, s.captured_at, gd)
        if match is None:
            continue
        checked += 1
        if not match:
            # A job that ended between the home screenshot and the export is
            # already gone from the export but was still busy on screen.
            ended = prev and [j for j in forecast(prev.snapshot, gd).jobs
                              if s.captured_at < j.ends_at <= s.snapshot.timestamp]
            reason = "a job ended during the capture" if ended else "unexplained"
            mismatched += reason == "unexplained"
            print(f"  counter #{s.id}: screen {s.counter[0]}/{s.counter[1]}, "
                  f"computed {computed} ({reason})")
    print(f"forecast: {len(findings) - len(bad)} ok, {len(bad)} flagged; "
          f"builder counter: {checked - mismatched}/{checked} explained")
    return 0 if not bad and not mismatched else 1


def next_queue_step(store, gd):
    """(name, from_level, item) of the first step the queue would start, from
    the same preview the dashboard shows; None if the queue has nothing to do."""
    from .catalog import preview
    stored = store.latest()
    if stored is None or gd is None:
        return None
    f = forecast(stored.snapshot, gd, stored.counter)
    planned, _ = preview(stored.snapshot, gd, store.plan(), f, time.time(), 50)
    if not planned:
        return None
    p = min(planned, key=lambda p: p.start)
    return gd.name(p.step.data), p.step.from_level, p


def _save_test(store, data_id, result):
    """Result of a dashboard "Test" (dry run) for one item, shown on the page."""
    result["at"] = int(time.time())
    store.kv_set(test_key(store.active_tag(), data_id), result)


def cmd_dryrun(args):
    from . import adb
    from .capture import CaptureError
    from .upgrader import UpgradeError, dry_run
    store = Store()
    store.pin()                      # results belong to the account active now
    data_id = args.id
    if data_id:
        from .catalog import SECTIONS, instances, section_of
        stored = store.latest()
        gd = _load_gamedata(stored.game_version if stored else None)
        sec = section_of(stored.snapshot, data_id) if gd else None
        idle = sorted(e.level for e in stored.snapshot.entries
                      if e.data == data_id and e.section in SECTIONS and e.level is not None
                      and not e.upgrading) if sec else []
        if not idle:
            msg = ("every copy is being upgraded right now" if sec and instances(
                stored.snapshot, data_id)[1] else "not found in the village")
            _save_test(store, data_id, {"state": "done", "found": False, "reason": msg})
            print(msg)
            return 1
        name, from_level, worker = gd.name(data_id), idle[0], SECTIONS[sec][0]
        from .catalog import cap
        top = cap(gd, stored.snapshot, data_id) or 0
        if from_level >= top:
            msg = (f"already at the highest level available now ({top}); "
                   "the game doesn't list it in the Build menu")
            _save_test(store, data_id, {"state": "done", "found": False, "reason": msg})
            print(msg)
            return 1
        print(f"testing {name} {from_level} -> {from_level + 1} ({worker})")
    elif args.queue:
        stored = store.latest()
        gd = _load_gamedata(stored.game_version if stored else None)
        nxt = next_queue_step(store, gd)
        if nxt is None:
            print("the queue has nothing to do")
            return 1
        name, from_level, p = nxt
        worker = p.worker
        print(f"next queue step: {name} {from_level} -> {from_level + 1} ({worker}) "
              f"(planned start {_clock(p.start)})")
    else:
        if not (args.name and args.from_level):
            print("use --queue, or --name NAME --from LEVEL")
            return 2
        name, from_level, worker = args.name, args.from_level, args.worker
    from .accounts import SwitchError, ensure_account
    from .capture import capture_lock
    with keep_display_on():
        try:
            with capture_lock(wait=30):
                if store.active_tag():       # the game may be on another account
                    ensure_account(store, store.active_tag(), lambda m: print(m, flush=True))
                r = dry_run(name, from_level, log=lambda m: print(m, flush=True), worker=worker)
        except (CaptureError, UpgradeError, SwitchError, adb.AdbError, OSError) as e:
            print(f"dry run failed: {e}")
            store.notify("error", "upgrade", f"Dry run for {name} failed", str(e))
            if data_id:
                _save_test(store, data_id, {"state": "done", "found": False, "reason": str(e)})
            return 1
    if data_id:
        _save_test(store, data_id, {
            "state": "done", "found": r.found, "affordable": r.affordable,
            "name": name, "from": from_level, "to": from_level + 1, "worker": worker,
            "reason": r.reason, "title": r.title,
            "screenshots": [os.path.basename(p) for p in r.screenshots]})
    print(f"{'FOUND' if r.found else 'NOT FOUND'}: {r.reason}")
    for row, title in r.rows_opened:
        print(f"  opened {row!r}: {title!r}")
    for p in r.screenshots:
        print(f"  screenshot {p}")
    store.notify("info" if r.found and r.affordable else "warning", "upgrade",
                 f"Dry run: {name} {from_level} -> {from_level + 1} "
                 + ("found, not confirmed" if r.found else "not found")
                 + (" (not enough resources)" if r.found and not r.affordable else ""),
                 r.reason + "".join(f"\n{row} -> {title}" for row, title in r.rows_opened)
                 + "".join(f"\n{p}" for p in r.screenshots))
    return 0 if r.found else 1


def cmd_farm(args):
    from .farm import run
    return run(args.run_id)


def cmd_session(args):
    from .session import main as session_main
    return session_main(args.trigger)


def cmd_orchestrator(args):
    from .orchestrator import main as orch_main
    orch_main()
    return 0


def cmd_serve(args):
    """Supervisor: run the dashboard and the orchestrator as child processes
    and restart either one if it exits (crash, killed). This is what the
    logon autostart runs, so the bot is reachable from the browser 24/7."""
    import subprocess
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    logdir = os.path.join(repo, "data", "logs")
    os.makedirs(logdir, exist_ok=True)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    specs = {"web": ["web", "--port", str(args.port)], "orchestrator": ["orchestrator"]}
    procs, restarts = {}, {k: [] for k in specs}

    def launch(name):
        out = open(os.path.join(logdir, f"{name}.log"), "a", encoding="utf-8")
        out.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} starting {name}\n")
        out.flush()
        procs[name] = subprocess.Popen([sys.executable, "-m", "village", *specs[name]], cwd=repo,
                                       stdout=out, stderr=subprocess.STDOUT, creationflags=flags)
        out.close()

    for name in specs:
        launch(name)
    print(f"serving: dashboard on port {args.port} + orchestrator (logs in {logdir})", flush=True)
    try:
        while True:
            time.sleep(10)
            for name, p in procs.items():
                if p.poll() is not None:
                    now = time.time()
                    restarts[name] = [t for t in restarts[name] if now - t < 600] + [now]
                    if len(restarts[name]) > 5:      # crash loop: slow down
                        time.sleep(60)
                    print(f"{name} exited ({p.returncode}); restarting", flush=True)
                    launch(name)
    except KeyboardInterrupt:
        for p in procs.values():
            p.terminate()
    return 0


STARTUP_CMD = "COC_Village_Bot.cmd"


def cmd_autostart(args):
    """Start `serve` at logon via a script in the user's Startup folder (no
    admin rights needed). --remove deletes it again."""
    startup = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup")
    path = os.path.join(startup, STARTUP_CMD)
    if args.remove:
        if os.path.exists(path):
            os.remove(path)
            print(f"removed {path}")
        else:
            print("autostart was not installed")
        return 0
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'@echo off\r\ncd /d "{repo}"\r\nstart "" "{exe}" -m village serve\r\n')
    print(f"installed {path}\n  runs: {exe} -m village serve  (in {repo})")
    return 0


def cmd_account(args):
    """Jobs started from the dashboard's account menu."""
    from . import accounts
    store = Store()
    log = lambda m: print(m, flush=True)  # noqa: E731
    with keep_display_on():
        if args.action == "switch":
            return accounts.run_switch(store, args.tag, log)
        return accounts.run_discover(store, log)


def cmd_emulator(args):
    """`emulator restart`: close LDPlayer (force if hung) and start it and the game again."""
    from . import accounts
    with keep_display_on():
        return accounts.run_restart(Store(), lambda m: print(m, flush=True))


def cmd_web(args):
    from .web import serve
    httpd = serve(args.host, args.port)
    print(f"dashboard on http://{args.host}:{args.port}/  (Ctrl+C to stop)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m village")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("static").set_defaults(func=cmd_static)
    c = sub.add_parser("capture")
    c.add_argument("--repeat", type=int, default=1)
    c.add_argument("--pause", type=float, default=5, help="seconds between captures")
    c.add_argument("--tag", help="expected player tag, e.g. #ABC123")
    c.add_argument("--no-save", action="store_true")
    c.add_argument("--screens", help="save each home screenshot here (OCR test set)")
    c.set_defaults(func=cmd_capture)
    s = sub.add_parser("show")
    s.add_argument("--id", type=int)
    s.set_defaults(func=cmd_show)
    sub.add_parser("builders").set_defaults(func=cmd_builders)
    v = sub.add_parser("verify")
    v.add_argument("--hours", type=float, help="only snapshots from the last N hours")
    v.add_argument("--tolerance", type=float, default=120, help="seconds")
    v.set_defaults(func=cmd_verify)
    d = sub.add_parser("dryrun", help="open an upgrade dialog and close it; never confirms")
    d.add_argument("--queue", action="store_true", help="the next step of the upgrade queue")
    d.add_argument("--id", type=int, help="test one item by its game ID (lowest idle copy)")
    d.add_argument("--name", help='building/hero name as in game, e.g. "Wizard Tower"')
    d.add_argument("--from", dest="from_level", type=int, help="current level of that copy")
    d.add_argument("--worker", default="builder", choices=("builder", "lab", "pet_house"),
                   help="Build panel tab: builder, lab (troops/spells/sieges) or pet_house")
    d.set_defaults(func=cmd_dryrun)
    se = sub.add_parser("session", help="one session: capture, farm, upgrade (orchestrator runs it)")
    se.add_argument("--trigger", default="manual")
    se.set_defaults(func=cmd_session)
    sub.add_parser("orchestrator", help="the scheduler loop").set_defaults(func=cmd_orchestrator)
    sv = sub.add_parser("serve", help="dashboard + orchestrator, restarted if they exit")
    sv.add_argument("--port", type=int, default=8765)
    sv.set_defaults(func=cmd_serve)
    au = sub.add_parser("install-autostart", help="start `serve` when you log on")
    au.add_argument("--remove", action="store_true")
    au.set_defaults(func=cmd_autostart)
    fm = sub.add_parser("farm", help="run a farming job queued from the dashboard")
    fm.add_argument("--run-id", type=int, required=True)
    fm.set_defaults(func=cmd_farm)
    ac = sub.add_parser("account", help="switch the game account / find the linked accounts")
    ac.add_argument("action", choices=("switch", "discover"))
    ac.add_argument("--tag", help="account to switch to (switch)")
    ac.set_defaults(func=cmd_account)
    em = sub.add_parser("emulator", help="restart LDPlayer and the game (force-closes a hung one)")
    em.add_argument("action", choices=("restart",))
    em.set_defaults(func=cmd_emulator)
    w = sub.add_parser("web")
    w.add_argument("--host", default="0.0.0.0", help="0.0.0.0 = reachable from the LAN")
    w.add_argument("--port", type=int, default=8765)
    w.set_defaults(func=cmd_web)
    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
