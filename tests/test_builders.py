import json
import unittest

from tests.helpers import export_text, load_raw
from village import builders
from village.builders import BuilderForecast, BuilderJob, finish_time, forecast, verify_series
from village.export import parse_export
from village.gamedata import GameData, GameObject

H = 3600
T0 = 1_000_000


def make(ts, buildings=(), heroes=(), helpers=()):
    raw = {"tag": "#T", "timestamp": ts,
           "buildings": [{"data": builders.BUILDERS_HUT, "lvl": 1, "cnt": 5},
                         {"data": builders.BOBS_HUT, "lvl": 1, "cnt": 1}, *buildings],
           "heroes": list(heroes), "helpers": list(helpers)}
    return parse_export(json.dumps(raw))


def apprentice_data(levels=8):
    rows = [{"Name": "BuilderApprentice" if i == 0 else "", "BoostMultiplier": str(i + 1),
             "BoostTimeSeconds": "3600"} for i in range(levels)]
    obj = GameObject(builders.BUILDER_APPRENTICE, "villager_apprentices", "BuilderApprentice", rows)
    return GameData({obj.id: obj}, [], "test")


class TotalTest(unittest.TestCase):
    def test_fixture_total_is_seven(self):
        # 5 huts + B.O.B + a job on the extra builder = the in-game "/7".
        snap = parse_export(export_text(load_raw()))
        self.assertEqual(builders.total_from_export(snap), 7)

    def test_idle_extra_builder_is_invisible_to_export(self):
        self.assertEqual(builders.total_from_export(make(T0)), 6)

    def test_counter_wins(self):
        f = forecast(make(T0), counter=(3, 7))
        self.assertEqual((f.total, f.total_source), (7, "counter"))


class FreeTimeTest(unittest.TestCase):
    def setUp(self):
        self.snap = make(T0, buildings=[{"data": 1000009, "lvl": 18, "timer": 2 * H}],
                         heroes=[{"data": 28000000, "lvl": 61, "timer": 5 * H},
                                 {"data": 28000001, "lvl": 85, "timer": 1 * H}])
        self.f = forecast(self.snap, counter=(0, 3))

    def test_jobs_sorted_by_end(self):
        self.assertEqual([j.ends_at for j in self.f.jobs], [T0 + H, T0 + 2 * H, T0 + 5 * H])

    def test_free_over_time(self):
        self.assertEqual(self.f.free(T0), 0)
        self.assertEqual(self.f.free(T0 + H), 1)        # exactly at the end: free
        self.assertEqual(self.f.free(T0 + 3 * H), 2)
        self.assertEqual(self.f.free(T0 + 6 * H), 3)

    def test_next_free(self):
        self.assertEqual(self.f.next_free_at(T0), T0 + H)
        self.assertEqual(self.f.next_free_at(T0 + H), T0 + H)   # one already free
        empty = BuilderForecast(T0, 0, "counter", [])
        self.assertIsNone(empty.next_free_at(T0))

    def test_frees_between(self):
        self.assertEqual([j.data for j in self.f.frees_between(T0, T0 + 2 * H)],
                         [28000001, 1000009])


class HelperTest(unittest.TestCase):
    def test_finish_time_math(self):
        self.assertEqual(finish_time(0, 10 * H, []), 10 * H)
        # 9x speed during the first hour: 9h done, 1h left after it.
        self.assertEqual(finish_time(0, 10 * H, [(0, H, 8)]), 2 * H)
        # Window later than the end: no effect.
        self.assertEqual(finish_time(0, H, [(2 * H, 3 * H, 8)]), H)
        # Work ends inside the window.
        self.assertEqual(finish_time(0, 5 * H, [(H, 2 * H, 8)]), H + 4 * H / 9)

    def test_apprentice_boosts_assigned_job(self):
        snap = make(T0, heroes=[{"data": 28000002, "lvl": 55, "timer": 10 * H,
                                 "helper_recurrent": True}],
                    helpers=[{"data": builders.BUILDER_APPRENTICE, "lvl": 8}])
        job = forecast(snap, apprentice_data()).jobs[0]
        self.assertEqual(job.timer_end, T0 + 10 * H)
        self.assertEqual(job.ends_at, T0 + 2 * H)

    def test_apprentice_on_cooldown(self):
        snap = make(T0, heroes=[{"data": 28000002, "lvl": 55, "timer": 10 * H,
                                 "helper_recurrent": True}],
                    helpers=[{"data": builders.BUILDER_APPRENTICE, "lvl": 8,
                              "helper_cooldown": 4 * H}])
        # 4h at 1x, then 6h of work left: 1h at 9x covers it in 40 min.
        self.assertEqual(forecast(snap, apprentice_data()).jobs[0].ends_at, T0 + 4 * H + 6 * H / 9)

    def test_unassigned_job_ignores_helper(self):
        snap = make(T0, heroes=[{"data": 28000002, "lvl": 55, "timer": 10 * H}],
                    helpers=[{"data": builders.BUILDER_APPRENTICE, "lvl": 8}])
        self.assertEqual(forecast(snap, apprentice_data()).jobs[0].ends_at, T0 + 10 * H)

    def test_fixture_helper_job_finishes_before_apprentice_is_ready(self):
        snap = parse_export(export_text(load_raw()))
        gw = next(j for j in forecast(snap, apprentice_data()).jobs if j.data == 28000002)
        self.assertEqual(gw.ends_at, gw.timer_end)


class VerifyTest(unittest.TestCase):
    def series(self, tower_timers, hero_timers, step=H):
        out = []
        for i, (t, h) in enumerate(zip(tower_timers, hero_timers)):
            b = [{"data": 1000009, "lvl": 18, "timer": t}] if t is not None else \
                [{"data": 1000009, "lvl": 19, "cnt": 1}]
            hs = [{"data": 28000000, "lvl": 61, "timer": h}] if h is not None else \
                [{"data": 28000000, "lvl": 62}]
            out.append(make(T0 + i * step, buildings=b, heroes=hs))
        return out

    def kinds(self, findings):
        return sorted((f.key[1], f.kind) for f in findings)

    def test_consistent_jobs_pass(self):
        snaps = self.series([3 * H, 2 * H, H, None], [10 * H, 9 * H, 8 * H, 7 * H])
        self.assertEqual(self.kinds(verify_series(snaps)),
                         [(1000009, "ok"), (28000000, "ok")])

    def test_drift_is_reported(self):
        # The timer jumped by 30 minutes between snapshots (e.g. a potion).
        snaps = self.series([3 * H, 2 * H - 1800], [10 * H, 9 * H])
        found = {f.key[1]: f for f in verify_series(snaps)}
        self.assertEqual(found[1000009].kind, "drift")
        self.assertAlmostEqual(found[1000009].seconds, 1800)
        self.assertEqual(found[28000000].kind, "ok")

    def test_finished_early_is_reported(self):
        # Predicted to end at T0+5h but gone by T0+1h.
        snaps = self.series([5 * H, None], [10 * H, 9 * H])
        found = {f.key[1]: f for f in verify_series(snaps)}
        self.assertEqual(found[1000009].kind, "early")

    def test_two_jobs_of_same_type(self):
        a = make(T0, buildings=[{"data": 1000009, "lvl": 18, "timer": H},
                                {"data": 1000009, "lvl": 18, "timer": 3 * H}])
        b = make(T0 + 2 * H, buildings=[{"data": 1000009, "lvl": 18, "timer": H},
                                         {"data": 1000009, "lvl": 19, "cnt": 1}])
        self.assertEqual([f.kind for f in verify_series([a, b])], ["ok", "ok"])


class JobKeyTest(unittest.TestCase):
    def test_key(self):
        j = BuilderJob("heroes", 28000000, 61, 0, 0.0, False, False)
        self.assertEqual(j.key, ("heroes", 28000000, 61))


if __name__ == "__main__":
    unittest.main()
