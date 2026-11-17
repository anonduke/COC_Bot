import json
import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace

from tests.test_catalog import KING, TH
from village import orchestrator as o
from village.export import parse_export
from village.settings import SETTINGS
from village.store import Store

H = 3600
CFG = {s.key: s.default for s in SETTINGS} | {"active_start": "00:00", "active_end": "00:00",
                                              "jitter_min": 0}


def ts(hhmm, day=15):
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 11, day, h, m).timestamp()


def latest(now, jobs=(), free=1, total=2, captured_ago=0):
    raw = {"tag": "#T", "timestamp": int(now - 10),
           "buildings": [{"data": TH, "lvl": 3, "cnt": 1}],
           "heroes": [{"data": KING, "lvl": 1, "timer": int(j - now + 10)} for j in jobs]}
    return SimpleNamespace(snapshot=parse_export(json.dumps(raw)), counter=(free, total),
                           captured_at=now - captured_ago)


WORK = lambda s: True        # noqa: E731
NO_WORK = lambda s: False    # noqa: E731


class ActiveHoursTest(unittest.TestCase):
    def test_window(self):
        self.assertTrue(o.in_active_hours(ts("12:00"), "07:00", "23:30"))
        self.assertFalse(o.in_active_hours(ts("23:45"), "07:00", "23:30"))
        self.assertTrue(o.in_active_hours(ts("01:00"), "22:00", "06:00"))     # crosses midnight
        self.assertTrue(o.in_active_hours(ts("03:00"), "05:00", "05:00"))     # always

    def test_next_start(self):
        self.assertEqual(o.next_active_start(ts("23:45"), "07:00", "23:30"), ts("07:00", 16))
        self.assertEqual(o.next_active_start(ts("05:00"), "07:00", "23:30"), ts("07:00"))
        self.assertEqual(o.next_active_start(ts("12:00"), "07:00", "23:30"), ts("12:00"))


class NextDueTest(unittest.TestCase):
    def test_first_look(self):
        self.assertEqual(o.next_due(100, None, None, WORK, CFG), (0, "first look at the village"))

    def test_free_builder_with_work_is_due_since_capture(self):
        now = ts("12:00")
        snap = latest(now, free=1, captured_ago=600)
        self.assertEqual(o.next_due(now, snap, None, WORK, CFG), (now - 600, "builder free"))
        # stable: asking again later gives the same moment (the 2026-11-17 bug)
        self.assertEqual(o.next_due(now + 300, snap, None, WORK, CFG)[0], now - 600)

    def test_free_builder_without_work_waits_for_health_check(self):
        now = ts("12:00")
        due, why = o.next_due(now, latest(now, free=1), None, NO_WORK, CFG)
        self.assertEqual((due, why), (now + CFG["health_check_hours"] * H, "health check"))

    def test_builder_freed_after_capture(self):
        now = ts("12:00")
        snap = latest(now, jobs=(now - 900,), free=0, total=1, captured_ago=3600)
        self.assertEqual(o.next_due(now, snap, None, WORK, CFG), (now - 900, "builder free"))

    def test_busy_builders_wait_for_next_free_grouped(self):
        now = ts("12:00")
        snap = latest(now, jobs=(now + 2 * H, now + 2 * H + 30 * 60, now + 9 * H), free=0, total=3)
        due, why = o.next_due(now, snap, None, WORK, CFG)
        self.assertAlmostEqual(due, now + 2 * H + 30 * 60, delta=1)    # 45 min window groups two
        self.assertIn("2 builder", why)

    def test_backoff_after_no_progress(self):
        now = ts("12:00")
        due, why = o.next_due(now, latest(now), None, WORK, CFG, no_progress=1, last_end=now - 60)
        self.assertEqual((due, why), (now - 60 + 30 * 60, "retry (no progress last time)"))
        due, _ = o.next_due(now, latest(now), None, WORK, CFG, no_progress=5, last_end=now - 60)
        self.assertEqual(due, now - 60 + 2 * H)

    def test_stale_village_triggers_health_check(self):
        now = ts("12:00")
        due, why = o.next_due(now, latest(now, free=0, captured_ago=13 * H), None, NO_WORK, CFG)
        self.assertEqual((due, why), (now - 1 * H, "health check"))


class FakeProc:
    def __init__(self, code=0):
        self.returncode, self._done = None, False
        self.code = code

    def poll(self):
        if self._done:
            self.returncode = self.code
            return self.code
        return None

    def kill(self):
        self._done = True


class TickTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "v.db"))
        self.spawned = []
        self.now = [ts("12:00")]
        self.busy = False
        self.orc = o.Orchestrator(self.store, log=lambda m: None, spawn=self._spawn,
                                  clock=lambda: self.now[0], game_busy=lambda: self.busy)
        self.store.set_setting("jitter_min", 0)
        self.store.set_setting("active_start", "00:00")
        self.store.set_setting("active_end", "00:00")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _spawn(self, trigger):
        p = FakeProc()
        self.spawned.append((trigger, p))
        return p

    def test_first_tick_starts_a_session_and_reports(self):
        state = self.orc.tick()
        self.assertEqual([t for t, _ in self.spawned], ["first look at the village"])
        self.assertEqual(state["running"]["trigger"], "first look at the village")
        self.assertEqual(self.store.kv_get("orchestrator")["pid"], os.getpid())

    def test_only_one_session_at_a_time(self):
        self.orc.tick()
        self.orc.tick()
        self.assertEqual(len(self.spawned), 1)

    def test_paused_runs_nothing_but_manual_command(self):
        self.store.set_setting("automation_paused", True)
        state = self.orc.tick()
        self.assertEqual((self.spawned, state["paused"], state["next_due"]), ([], True, None))
        self.store.add_command("run_session")
        self.orc.tick()
        self.assertEqual([t for t, _ in self.spawned], ["manual"])
        self.assertEqual(self.store.pending_commands(), [])

    def test_overdue_session_starts_despite_jitter(self):
        # Regression 2026-11-17: an overdue health check never started because
        # the jitter was re-drawn every tick against a "due" that moved.
        self.store.set_setting("jitter_min", 5)
        snap = latest(self.now[0], free=0, captured_ago=13 * H)
        self.store.save_snapshot(snap.snapshot, captured_at=snap.captured_at)
        started = False
        for _ in range(20):                       # 20 ticks of 20 s > 5 min jitter
            self.orc.tick()
            if self.spawned:
                started = True
                break
            self.now[0] += 20
        self.assertTrue(started)
        self.assertEqual(self.spawned[0][0], "health check")

    def test_outside_active_hours_waits_for_the_window(self):
        self.store.set_setting("active_start", "07:00")
        self.store.set_setting("active_end", "23:30")
        self.now[0] = ts("23:45")
        state = self.orc.tick()                   # first look is due, but not now
        self.assertEqual(self.spawned, [])
        self.assertEqual(state["next_due"], ts("07:00", 16))
        self.now[0] = ts("07:00", 16) + 1
        self.orc.tick()
        self.assertEqual(len(self.spawned), 1)

    def test_failed_session_backs_off(self):
        self.orc.tick()                                   # first look starts
        _, p = self.spawned[0]
        p.code = 1
        p.kill()                                          # ...and fails
        self.orc.tick()                                   # reaped; must NOT restart now
        self.assertEqual(len(self.spawned), 1)
        self.assertIn("retry after 1 failed", self.store.kv_get("orchestrator")["next_reason"])
        self.now[0] += 10 * 60 + 1
        self.orc.tick()
        self.assertEqual(len(self.spawned), 2)
        _, p2 = self.spawned[1]
        p2.kill()                                         # succeeds: streak cleared
        self.orc.tick()
        self.assertEqual(self.store.kv_get("fail_streak")["n"], 0)

    def test_waits_while_the_game_is_busy(self):
        self.busy = True                     # e.g. a farming run from the dashboard
        state = self.orc.tick()
        self.assertEqual(self.spawned, [])
        self.assertIn("game is busy", state["next_reason"])
        self.busy = False
        self.orc.tick()
        self.assertEqual(len(self.spawned), 1)

    def test_session_end_and_timeout(self):
        self.orc.tick()
        _, p = self.spawned[0]
        self.now[0] += o.SESSION_TIMEOUT + 1
        self.orc.check_session()         # over time: killed
        self.assertTrue(p._done)
        self.orc.check_session()         # reaped
        self.assertIsNone(self.orc.proc)


if __name__ == "__main__":
    unittest.main()
