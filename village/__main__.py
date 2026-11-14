"""Command line: python -m village <command>

    static            decode the game's data tables for the installed version
    capture           read the village export from the game and store it
                      (--repeat N for a reliability run)
    show [--id N]     summarise the latest (or a given) stored snapshot
"""
import argparse
import sys
import time

from . import gamedata
from .store import Store


def _fmt_eta(seconds):
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return f"{d}d {h}h {m}m" if d else f"{h}h {m}m"


def cmd_static(args):
    gd = gamedata.ensure_static_data()
    print(f"game {gd.version}: {len(gd.objects)} objects")


def cmd_capture(args):
    from . import adb
    from .capture import CaptureError, capture_export
    store = None if args.no_save else Store()
    version = adb.game_version()
    ok = 0
    for i in range(1, args.repeat + 1):
        t0 = time.time()
        try:
            _, snap, res = capture_export(expected_tag=args.tag, screen_dir=args.screens)
        except (CaptureError, adb.AdbError, OSError) as e:
            print(f"[{i}/{args.repeat}] FAILED after {time.time() - t0:.0f}s: {e}")
        else:
            ok += 1
            sid = store.save_snapshot(snap, res, game_version=version) if store else None
            print(f"[{i}/{args.repeat}] ok in {time.time() - t0:.0f}s  {snap.tag}  "
                  f"{len(snap.builder_jobs())} builder jobs  gold {res.gold} elixir {res.elixir} "
                  f"dark {res.dark_elixir} gems {res.gems}" + (f"  saved #{sid}" if sid else ""))
        if i < args.repeat:
            time.sleep(args.pause)
    print(f"{ok}/{args.repeat} captures succeeded")
    return 0 if ok == args.repeat else 1


def cmd_show(args):
    store = Store()
    stored = store.get(args.id) if args.id else store.latest()
    if stored is None:
        print("no snapshots stored")
        return 1
    snap = stored.snapshot
    try:
        gd = gamedata.GameData.load(gamedata.static_dir(stored.game_version))
        name = gd.name
    except OSError:
        name = lambda i: f"#{i}"  # noqa: E731 - no decoded data for that version
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
    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
