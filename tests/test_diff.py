import copy
import unittest

from tests.helpers import export_text, load_raw
from village.diff import Change, Residual, diff
from village.export import parse_export


def snap(raw):
    return parse_export(export_text(raw))


def find(raw, section, data, **match):
    for i, e in enumerate(raw[section]):
        if e["data"] == data and all(e.get(k) == v for k, v in match.items()):
            return i
    raise KeyError((section, data, match))


class DiffTest(unittest.TestCase):
    def setUp(self):
        self.old = load_raw()
        self.new = copy.deepcopy(self.old)
        self.new["timestamp"] += 600

    def test_no_change(self):
        self.assertTrue(diff(snap(self.old), snap(self.new)).empty)

    def test_upgrade_started(self):
        # One of the Gold Storages at 16 starts upgrading.
        i = find(self.new, "buildings", 1000005, lvl=16)
        self.new["buildings"][i]["cnt"] = self.old["buildings"][i].get("cnt", 1) - 1
        self.new["buildings"].append({"data": 1000005, "lvl": 16, "timer": 3600})
        d = diff(snap(self.old), snap(self.new))
        self.assertEqual(d.started, [Change("buildings", 1000005, 16, 1)])
        self.assertEqual((d.finished, d.leveled, d.unexplained), ([], [], []))

    def test_upgrade_finished(self):
        # Archer Tower 18 (timer) completes -> one more Archer Tower at 19.
        i = find(self.new, "buildings", 1000009, lvl=18, timer=40913)
        del self.new["buildings"][i]
        self.new["buildings"].append({"data": 1000009, "lvl": 19, "cnt": 1})
        d = diff(snap(self.old), snap(self.new))
        self.assertEqual(d.finished, [Change("buildings", 1000009, 18, 1)])
        self.assertEqual((d.started, d.leveled, d.unexplained), ([], [], []))

    def test_hero_finished(self):
        i = find(self.new, "heroes", 28000000)
        self.new["heroes"][i] = {"data": 28000000, "lvl": 62}
        d = diff(snap(self.old), snap(self.new))
        self.assertEqual(d.finished, [Change("heroes", 28000000, 61, 1)])

    def test_instant_wall_upgrades(self):
        old = {"tag": "#T", "timestamp": 1, "buildings": [{"data": 1000010, "lvl": 18, "cnt": 10}]}
        new = {"tag": "#T", "timestamp": 2, "buildings": [{"data": 1000010, "lvl": 18, "cnt": 7},
                                                          {"data": 1000010, "lvl": 19, "cnt": 3}]}
        d = diff(snap(old), snap(new))
        self.assertEqual(d.leveled, [Change("buildings", 1000010, 18, 3)])
        self.assertEqual(d.unexplained, [])

    def test_unexplained_is_reported(self):
        self.new["buildings"].append({"data": 1000000, "lvl": 1, "cnt": 1})  # new building placed
        d = diff(snap(self.old), snap(self.new))
        self.assertEqual(d.unexplained, [Residual("buildings", 1000000, 1, False, 1)])
        self.assertEqual((d.started, d.finished, d.leveled), ([], [], []))

    def test_different_villages_rejected(self):
        self.new["tag"] = "#OTHER"
        with self.assertRaises(ValueError):
            diff(snap(self.old), snap(self.new))


if __name__ == "__main__":
    unittest.main()
