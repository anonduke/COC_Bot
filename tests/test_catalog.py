import json
import unittest

from village import catalog
from village.builders import forecast
from village.export import parse_export
from village.gamedata import GameData, GameObject

H = 3600
T0 = 1_000_000
TH, HUT, CANNON, KING = 1000001, 1000015, 1000000, 28000000


def gd():
    th = GameObject(TH, "buildings", "Town Hall",
                    [{"Name": "Town Hall", "TownHallLevel": "0", "BuildTimeH": "1", "BuildCost": "1",
                      "BuildResource": "Gold"}] * 3)
    # Cannon: level L needs Town Hall L; reaching level L takes L hours.
    cannon = GameObject(CANNON, "buildings", "Cannon",
                        [{"Name": "Cannon" if i == 0 else "", "TownHallLevel": str(i + 1),
                          "BuildTimeH": str(i + 1), "BuildCost": str((i + 1) * 100),
                          "BuildResource": "Gold" if i == 0 else ""} for i in range(5)])
    # King: row L = L -> L+1, 10 h each; last row empty.
    king = GameObject(KING, "heroes", "Barbarian King",
                      [{"Name": "Barbarian King" if i == 0 else "", "RequiredTownHallLevel": "1",
                        "RequiredHeroTavernLevel": "0",
                        "UpgradeTimeH": "10" if i < 9 else "", "UpgradeCost": "50" if i < 9 else "",
                        "UpgradeResource": "DarkElixir" if i == 0 else ""} for i in range(10)])
    return GameData({o.id: o for o in (th, cannon, king)}, [], "t")


def snap(buildings=(), heroes=(), th_level=3, huts=2):
    raw = {"tag": "#T", "timestamp": T0,
           "buildings": [{"data": TH, "lvl": th_level, "cnt": 1},
                         {"data": HUT, "lvl": 1, "cnt": huts}, *buildings],
           "heroes": list(heroes)}
    return parse_export(json.dumps(raw))


class StepTest(unittest.TestCase):
    def test_building_row_is_the_level_reached(self):
        s = catalog.step(gd(), CANNON, 2)
        self.assertEqual((s.to_level, s.seconds, s.cost, s.resource), (3, 3 * H, 300, "Gold"))

    def test_hero_row_is_the_level_left(self):
        s = catalog.step(gd(), KING, 3)
        self.assertEqual((s.to_level, s.seconds, s.cost, s.resource), (4, 10 * H, 50, "DarkElixir"))
        self.assertIsNone(catalog.step(gd(), KING, 10))   # max level

    def test_max_level_follows_town_hall(self):
        self.assertEqual(catalog.max_level(gd(), CANNON, 3), 3)
        self.assertEqual(catalog.max_level(gd(), CANNON, 9), 5)


class CatalogTest(unittest.TestCase):
    def test_upgrading_copy_counts_as_next_level(self):
        s = snap(buildings=[{"data": CANNON, "lvl": 1, "cnt": 1},
                            {"data": CANNON, "lvl": 2, "timer": H}])
        self.assertEqual(catalog.instances(s, CANNON), ([1, 3], 1))
        item = next(c for c in catalog.catalog(s, gd()) if c["id"] == CANNON)
        self.assertEqual((item["min_level"], item["max_level"], item["steps_left"]), (1, 3, 2))

    def test_expand_keeps_copies_even(self):
        s = snap(buildings=[{"data": CANNON, "lvl": 1, "cnt": 2}])
        steps = catalog.expand(s, gd(), CANNON)
        self.assertEqual([x.from_level for x in steps], [1, 1, 2, 2])


class PreviewTest(unittest.TestCase):
    def plan(self, s, items):
        items = [{"id": i, "data_id": d, "target_level": t, "enabled": True}
                 for i, (d, t) in enumerate(items, start=1)]
        planned, _ = catalog.preview(s, gd(), items, forecast(s, counter=(2, 2)), T0)
        return planned

    def test_one_hero_never_gets_two_builders(self):
        s = snap(heroes=[{"data": KING, "lvl": 1}])
        p = self.plan(s, [(KING, 4)])
        self.assertEqual([x.step.from_level for x in p], [1, 2, 3])
        for a, b in zip(p, p[1:]):
            self.assertGreaterEqual(b.start, a.end)

    def test_busy_hero_waits_for_its_current_job(self):
        s = snap(heroes=[{"data": KING, "lvl": 1, "timer": 5 * H}])
        p = self.plan(s, [(KING, 3)])
        self.assertEqual(p[0].step.from_level, 2)
        self.assertEqual(p[0].start, T0 + 5 * H)

    def test_blocked_item_does_not_stall_the_queue(self):
        # The King is busy for 5 h; the second builder should do Cannon now.
        s = snap(heroes=[{"data": KING, "lvl": 1, "timer": 5 * H}],
                 buildings=[{"data": CANNON, "lvl": 1, "cnt": 1}])
        p = self.plan(s, [(KING, 3), (CANNON, 2)])
        first = min(p, key=lambda x: x.start)
        self.assertEqual((first.step.data, first.start), (CANNON, T0))

    def test_two_copies_in_parallel(self):
        s = snap(buildings=[{"data": CANNON, "lvl": 1, "cnt": 2}])
        p = self.plan(s, [(CANNON, 2)])
        self.assertEqual(sorted(x.lane for x in p), [0, 1])
        self.assertEqual({x.start for x in p}, {T0})

    def test_same_type_twice_continues(self):
        s = snap(buildings=[{"data": CANNON, "lvl": 1, "cnt": 1}])
        p = self.plan(s, [(CANNON, 2), (CANNON, 3)])
        self.assertEqual([(x.item_id, x.step.to_level) for x in p], [(1, 2), (2, 3)])

    def test_disabled_items_are_skipped(self):
        s = snap(buildings=[{"data": CANNON, "lvl": 1, "cnt": 1}])
        items = [{"id": 1, "data_id": CANNON, "target_level": None, "enabled": False}]
        self.assertEqual(catalog.preview(s, gd(), items, forecast(s, counter=(2, 2)), T0)[0], [])


if __name__ == "__main__":
    unittest.main()
