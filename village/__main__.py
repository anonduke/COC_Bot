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
import sys
import time

from . import gamedata
from .builders import forecast, verify_series
from .power import keep_display_on
from .store import Store


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
    from .capture import CaptureError, capture_export
    store = None if args.no_save else Store()
    version = adb.game_version()
    gd = _load_gamedata(version)
    ok = matches = checked = 0
    with keep_display_on():
        for i in range(1, args.repeat + 1):
            t0 = time.time()
            try:
                cap = capture_export(expected_tag=args.tag, screen_dir=args.screens,
                                     log=lambda m: print(m, flush=True))
            except (CaptureError, adb.AdbError, OSError) as e:
                print(f"[{i}/{args.repeat}] {time.strftime('%H:%M')} FAILED after "
                      f"{time.time() - t0:.0f}s: {e}", flush=True)
                if store and "already running" not in str(e):
                    store.notify("error", "capture", "Village capture failed", str(e))
            else:
                ok += 1
                snap, res = cap.snapshot, cap.resources
                sid = store.save_snapshot(snap, res, game_version=version,
                                          captured_at=cap.home_at, counter=cap.counter)                     if store else None
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


def cmd_dryrun(args):
    from . import adb
    from .capture import CaptureError
    from .upgrader import UpgradeError, dry_run
    store = Store()
    if args.queue:
        stored = store.latest()
        gd = _load_gamedata(stored.game_version if stored else None)
        nxt = next_queue_step(store, gd)
        if nxt is None:
            print("the queue has nothing to do")
            return 1
        name, from_level, p = nxt
        print(f"next queue step: {name} {from_level} -> {from_level + 1} "
              f"(planned start {_clock(p.start)})")
    else:
        if not (args.name and args.from_level):
            print("use --queue, or --name NAME --from LEVEL")
            return 2
        name, from_level = args.name, args.from_level
    with keep_display_on():
        try:
            r = dry_run(name, from_level, log=lambda m: print(m, flush=True))
        except (CaptureError, UpgradeError, adb.AdbError, OSError) as e:
            print(f"dry run failed: {e}")
            store.notify("error", "upgrade", f"Dry run for {name} failed", str(e))
            return 1
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
    d.add_argument("--name", help='building/hero name as in game, e.g. "Wizard Tower"')
    d.add_argument("--from", dest="from_level", type=int, help="current level of that copy")
    d.set_defaults(func=cmd_dryrun)
    fm = sub.add_parser("farm", help="run a farming job queued from the dashboard")
    fm.add_argument("--run-id", type=int, required=True)
    fm.set_defaults(func=cmd_farm)
    w = sub.add_parser("web")
    w.add_argument("--host", default="0.0.0.0", help="0.0.0.0 = reachable from the LAN")
    w.add_argument("--port", type=int, default=8765)
    w.set_defaults(func=cmd_web)
    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
