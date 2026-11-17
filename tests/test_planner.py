import json
import unittest

from tests.test_catalog import CANNON, KING, TH, gd
from village import planner
from village.export import parse_export

T0 = 1_000_000


def snap(buildings=(), heroes=()):
    raw = {"tag": "#T", "timestamp": T0,
           "buildings": [{"data": TH, "lvl": 3, "cnt": 1}, *buildings], "heroes": list(heroes)}
    return parse_export(json.dumps(raw))


def items(*specs):
    return [{"id": i, "data_id": d, "target_level": t, "enabled": e}
            for i, (d, t, e) in enumerate(specs, start=1)]


class CandidatesTest(unittest.TestCase):
    def test_queue_order_and_lowest_copy(self):
        s = snap(buildings=[{"data": CANNON, "lvl": 2, "cnt": 1}, {"data": CANNON, "lvl": 1, "cnt": 1}],
                 heroes=[{"data": KING, "lvl": 1}])
        c = planner.candidates(s, gd(), items((KING, 5, True), (CANNON, None, True)))
        self.assertEqual([(x.data_id, x.from_level) for x in c], [(KING, 1), (CANNON, 1)])

    def test_busy_hero_is_skipped_not_blocking(self):
        s = snap(buildings=[{"data": CANNON, "lvl": 1, "cnt": 1}],
                 heroes=[{"data": KING, "lvl": 1, "timer": 3600}])
        c = planner.candidates(s, gd(), items((KING, 5, True), (CANNON, None, True)))
        self.assertEqual([x.data_id for x in c], [CANNON])

    def test_target_disabled_and_excluded(self):
        s = snap(buildings=[{"data": CANNON, "lvl": 2, "cnt": 1}], heroes=[{"data": KING, "lvl": 1}])
        self.assertEqual(planner.candidates(s, gd(), items((CANNON, 2, True))), [])   # at target
        self.assertEqual(planner.candidates(s, gd(), items((KING, 5, False))), [])     # disabled
        self.assertEqual(planner.candidates(s, gd(), items((KING, 5, True)), exclude={(KING, 1)}), [])

    def test_affordability_and_capacity(self):
        s = snap(heroes=[{"data": KING, "lvl": 1}])       # costs 50 dark elixir
        c = planner.candidates(s, gd(), items((KING, 5, True)), {"dark_elixir": 60})[0]
        self.assertTrue(c.affordable)
        c = planner.candidates(s, gd(), items((KING, 5, True)), {"dark_elixir": 40},
                               {"dark_fill": 0.8})[0]
        self.assertFalse(c.affordable)
        self.assertEqual((c.capacity, c.reason), (50, ""))
        c = planner.candidates(s, gd(), items((KING, 5, True)), {"dark_elixir": 20},
                               {"dark_fill": 0.5})[0]
        self.assertIn("more than the storage holds", c.reason)
        self.assertIsNone(planner.candidates(s, gd(), items((KING, 5, True)))[0].affordable)

    def test_farm_target(self):
        c = planner.Candidate(1, KING, "K", 1, 1, "gold", 1000, False, None)
        self.assertEqual(planner.farm_target(c, 10), 1100)
        c = planner.Candidate(1, KING, "K", 1, 1, "gold", 1000, False, 1050)
        self.assertEqual(planner.farm_target(c, 10), 1039)


if __name__ == "__main__":
    unittest.main()
