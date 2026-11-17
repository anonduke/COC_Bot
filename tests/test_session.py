import copy
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from tests.test_catalog import CANNON, KING, TH, gd
from village import session
from village.export import parse_export
from village.store import Resources, Store
from village.upgrader import DryRun

HUT = 1000015


class FakeGame:
    """A village that changes when an upgrade is really confirmed."""

    def __init__(self, raw, gold=10_000, dark=1_000, gems=500, builders=(1, 2),
                 upgrade_takes=True, popup=False, dark_fill=0.5):
        self.raw = raw
        self.res = {"gold": gold, "elixir": 0, "dark_elixir": dark, "gems": gems}
        self.free, self.total = builders
        self.upgrade_takes, self.popup, self.dark_fill = upgrade_takes, popup, dark_fill
        self.calls = []
        self.ts = 1_000_000
        self.game = session.Game(self.ensure_ready, self.capture, self.upgrade, self.farm, gd())

    def ensure_ready(self):
        self.calls.append("ready")

    def capture(self):
        self.ts += 60
        raw = copy.deepcopy(self.raw)
        raw["timestamp"] = self.ts
        cap = SimpleNamespace(snapshot=parse_export(json.dumps(raw)), home_at=self.ts,
                              resources=Resources(self.res["gold"], self.res["elixir"],
                                                  self.res["dark_elixir"], self.res["gems"]),
                              counter=(self.free, self.total),
                              fills={"gold_fill": 0.5, "elixir_fill": 0.5,
                                     "dark_fill": self.dark_fill})
        self.calls.append("capture")
        return cap

    def _find(self, data_id):
        for sec in ("buildings", "heroes"):
            for e in self.raw.get(sec, []):
                if e["data"] == data_id and "timer" not in e:
                    return sec, e
        return None, None

    def upgrade(self, name, from_level, confirm):
        self.calls.append(("upgrade", name, from_level, confirm))
        r = DryRun(name, from_level, found=True, affordable=True)
        data_id = {"Cannon": CANNON, "Barbarian King": KING}[name]
        if not confirm:
            r.reason = "dry run"
            return r
        r.confirm_tapped = True
        if self.popup:
            self.res["gems"] -= 50           # simulate the worst case
        if self.upgrade_takes:
            sec, e = self._find(data_id)
            if e.get("cnt", 1) > 1:
                e["cnt"] -= 1
                self.raw[sec].append({"data": data_id, "lvl": from_level, "timer": 3600})
            else:
                e["timer"] = 3600
            self.free -= 1
            r.confirmed = True
        return r

    def farm(self, params):
        self.calls.append(("farm", params))
        for k in ("gold", "elixir", "dark_elixir"):
            if params.get(k):
                self.res[k] = params[k]
        return "done", "targets reached"


def village(**extra):
    raw = {"tag": "#T", "timestamp": 1_000_000,
           "buildings": [{"data": TH, "lvl": 3, "cnt": 1}, {"data": HUT, "lvl": 1, "cnt": 2},
                         {"data": CANNON, "lvl": 1, "cnt": 2}],
           "heroes": [{"data": KING, "lvl": 1}]}
    raw.update(extra)
    return raw


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "v.db"))
        self.store.set_setting("dry_run", False)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def run_with(self, fake, trigger="test"):
        return session.run_session(self.store, fake.game, trigger, log=lambda m: None)

    def test_fills_free_builders_in_queue_order_and_verifies(self):
        self.store.add_plan_item(KING)
        self.store.add_plan_item(CANNON)
        fake = FakeGame(village(), builders=(2, 2))
        sid, out = self.run_with(fake)
        self.assertEqual(out.status, "done")
        self.assertEqual(out.upgrades, 2)
        ups = [c for c in fake.calls if isinstance(c, tuple) and c[0] == "upgrade"]
        self.assertEqual([c[1] for c in ups], ["Barbarian King", "Cannon"])
        self.assertEqual([a["status"] for a in self.store.actions(session_id=sid)],
                         ["verified", "verified"])
        # every snapshot of the session carries the game version (dashboard names)
        self.assertEqual({s.game_version for s in self.store.all()}, {"t"})

    def test_dry_run_never_confirms(self):
        self.store.set_setting("dry_run", True)
        self.store.add_plan_item(KING)
        self.store.add_plan_item(CANNON)
        fake = FakeGame(village())
        sid, out = self.run_with(fake)
        ups = [c for c in fake.calls if isinstance(c, tuple) and c[0] == "upgrade"]
        self.assertTrue(ups)
        self.assertTrue(all(c[3] is False for c in ups))
        self.assertEqual({a["kind"] for a in self.store.actions(session_id=sid)}, {"dry_run"})

    def test_dry_run_uses_one_check_per_free_builder(self):
        self.store.set_setting("dry_run", True)
        for d in (KING, CANNON):
            self.store.add_plan_item(d)
        fake = FakeGame(village(), builders=(1, 2))
        self.run_with(fake)
        ups = [c for c in fake.calls if isinstance(c, tuple) and c[0] == "upgrade"]
        self.assertEqual(len(ups), 1)

    def test_not_applied_is_journaled_and_stops(self):
        self.store.add_plan_item(KING)
        self.store.add_plan_item(CANNON)
        fake = FakeGame(village(), upgrade_takes=False)
        sid, out = self.run_with(fake)
        acts = self.store.actions(session_id=sid)
        self.assertEqual([a["status"] for a in acts], ["not_applied"])
        self.assertEqual(out.upgrades, 0)

    def test_farms_when_not_affordable(self):
        self.store.add_plan_item(KING)                  # costs 50 dark elixir
        fake = FakeGame(village(), dark=10, dark_fill=0.1)   # capacity ~100
        sid, out = self.run_with(fake)
        farms = [c for c in fake.calls if isinstance(c, tuple) and c[0] == "farm"]
        self.assertEqual(len(farms), 1)
        self.assertEqual(farms[0][1]["dark_elixir"], 55)    # cost 50 + 10 % margin
        kinds = [(a["kind"], a["status"]) for a in self.store.actions(session_id=sid)]
        self.assertEqual(kinds, [("farm", "done"), ("upgrade", "verified")])

    def test_storage_too_small_is_not_farmed_for(self):
        self.store.add_plan_item(KING)                  # costs 50, storage holds ~20
        fake = FakeGame(village(), dark=10, dark_fill=0.5)
        sid, out = self.run_with(fake)
        self.assertFalse([c for c in fake.calls if isinstance(c, tuple)])
        self.assertIn("nothing in the queue to start", out.lines)

    def test_no_free_builder_does_nothing(self):
        self.store.add_plan_item(KING)
        fake = FakeGame(village(), builders=(0, 2))
        sid, out = self.run_with(fake)
        self.assertFalse([c for c in fake.calls if isinstance(c, tuple)])
        self.assertIn("no free builder", out.lines)

    def test_gem_guard_pauses_automation(self):
        self.store.add_plan_item(KING)
        self.store.add_plan_item(CANNON)
        fake = FakeGame(village(), popup=True)
        sid, out = self.run_with(fake)
        self.assertEqual(out.status, "failed")
        self.assertIs(self.store.settings()["automation_paused"], True)
        ups = [c for c in fake.calls if isinstance(c, tuple) and c[0] == "upgrade"]
        self.assertEqual(len(ups), 1)                      # stopped right after
        self.assertTrue(any(n["source"] == "gem guard" for n in self.store.notifications()))

    def test_interrupted_intent_is_resolved_not_repeated(self):
        # A previous session journaled INTENT, then died before verifying.
        fake = FakeGame(village())
        before = fake.capture()
        bid = self.store.save_snapshot(before.snapshot)
        old = self.store.start_session("test")
        aid = self.store.add_action(old, "upgrade", "intent", KING, "Barbarian King", 1, bid)
        fake.raw["heroes"][0]["timer"] = 3000           # it did start in the game
        sid, out = self.run_with(fake)
        self.assertEqual(next(a for a in self.store.actions() if a["id"] == aid)["status"], "verified")
        self.assertFalse([c for c in fake.calls if isinstance(c, tuple) and c[0] == "upgrade"])

    def test_errors_end_the_session_as_failed(self):
        fake = FakeGame(village())
        fake.game.ensure_ready = lambda: (_ for _ in ()).throw(RuntimeError("emulator down"))
        sid, out = self.run_with(fake)
        self.assertEqual(out.status, "failed")
        self.assertEqual(self.store.sessions()[0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
