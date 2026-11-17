import unittest

from tests.helpers import export_text, load_raw
from village.export import ExportError, parse_export


class ParseFixtureTest(unittest.TestCase):
    """The fixture is a real TH18 export (tag anonymised). In game it showed
    0/7 builders free, i.e. 7 builder jobs."""

    def setUp(self):
        self.raw = load_raw()
        self.snap = parse_export(export_text(self.raw))

    def test_header(self):
        self.assertEqual(self.snap.tag, "#TESTTAG")
        self.assertEqual(self.snap.timestamp, 1794629763)

    def test_builder_jobs_match_in_game_counter(self):
        jobs = self.snap.builder_jobs()
        self.assertEqual(len(jobs), 7)
        self.assertEqual(sorted(j.section for j in jobs), ["buildings"] * 2 + ["heroes"] * 5)
        self.assertEqual([j.ends_at for j in jobs], sorted(j.ends_at for j in jobs))
        at = next(j for j in jobs if j.data == 1000009)   # Archer Tower 18 -> 19
        self.assertEqual((at.from_level, at.ends_at), (18, 1794629763 + 40913))

    def test_other_queues(self):
        self.assertEqual(len(self.snap.jobs("lab")), 2)          # one unit, one spell
        self.assertEqual(len(self.snap.jobs("pet_house")), 1)
        self.assertEqual(self.snap.jobs("unknown"), [])

    def test_flags(self):
        gw = next(e for e in self.snap.section("heroes") if e.data == 28000002)
        self.assertTrue(gw.helper_recurrent and gw.extra and gw.upgrading)
        mortar = next(e for e in self.snap.section("buildings") if e.data == 1000013)
        self.assertTrue(mortar.gear_up)
        self.assertEqual(self.snap.helper_cooldowns(),
                         {93000000: 1794629763 + 58570, 93000001: 1794629763 + 58570})

    def test_counts_and_levels(self):
        walls = [e for e in self.snap.section("buildings") if e.data == 1000010]
        self.assertEqual([(w.level, w.count) for w in walls], [(19, 325)])
        self.assertEqual(self.snap.level_of(1000001), 18)   # Town Hall
        self.assertIsNone(self.snap.level_of(42))

    def test_crafting_station_modules(self):
        modules = [e for e in self.snap.entries if e.data // 1_000_000 == 102]
        self.assertEqual(len(modules), 9)
        self.assertTrue(all(m.parent // 1_000_000 == 103 and m.level == 1 for m in modules))
        archetypes = [e for e in self.snap.entries if e.data // 1_000_000 == 103]
        self.assertTrue(all(a.parent == 1000097 for a in archetypes))

    def test_builder_base_is_separate(self):
        self.assertEqual(self.snap.jobs("bb_builder"), [])
        self.assertTrue(self.snap.section("buildings2"))

    def test_bom_tolerated(self):
        self.assertEqual(parse_export("﻿" + export_text(self.raw)).tag, "#TESTTAG")


class RejectTest(unittest.TestCase):
    def test_rejects_non_exports(self):
        for text in ("", "hello", "[]", '{"tag": "#A"}', '{"tag": "A", "timestamp": 1, "buildings": []}',
                     '{"tag": "#A", "timestamp": "x", "buildings": []}',
                     '{"tag": "#A", "timestamp": 1}',
                     '{"tag": "#A", "timestamp": 1, "buildings": [{"lvl": 1}]}',
                     '{"tag": "#A", "timestamp": 1, "buildings": [5]}'):
            with self.subTest(text=text), self.assertRaises(ExportError):
                parse_export(text)

    def test_unknown_id_list_sections_are_skipped(self):
        snap = parse_export('{"tag": "#A", "timestamp": 1, "buildings": [], "emotes": [126000000, 126000001]}')
        self.assertEqual(snap.entries, [])

    def test_minimal_export(self):
        snap = parse_export('{"tag": "#A", "timestamp": 1, "buildings": [], "skins": [1, 2]}')
        self.assertEqual(snap.entries, [])


if __name__ == "__main__":
    unittest.main()
