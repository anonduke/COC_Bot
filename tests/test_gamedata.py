import glob
import os
import tempfile
import unittest
import zipfile

from tests.helpers import export_text, load_raw
from village import gamedata
from village.export import parse_export

BUILDINGS = '''"Name","GlobalID","TID","BuildCost"
"String","int","String","int"
"Town Hall",1000001,"TID_TH",100
,,,200
"Cannon",1000000,"TID_CANNON",5
,,,6
,,,7
'''
HEROES = '''"Name","TID","UpgradeCost"
"String","String","int"
"Barbarian King","TID_BK",10
,,11
"Archer Queen","TID_AQ",20
'''
TEXTS = '''"TID","EN"
"string","string"
"TID_TH","Town Hall"
"TID_BK","Barbarian King"
'''


class ParseTableTest(unittest.TestCase):
    def test_global_id_column_wins_over_row_order(self):
        objs = gamedata.parse_table(BUILDINGS, "buildings", 1)
        self.assertEqual(objs[1000001].name, "Town Hall")
        self.assertEqual(objs[1000000].name, "Cannon")

    def test_row_order_ids_without_global_id(self):
        objs = gamedata.parse_table(HEROES, "heroes", 28)
        self.assertEqual(objs[28000000].name, "Barbarian King")
        self.assertEqual(objs[28000001].name, "Archer Queen")

    def test_continuation_rows_are_levels(self):
        cannon = gamedata.parse_table(BUILDINGS, "buildings", 1)[1000000]
        self.assertEqual(cannon.max_level, 3)
        self.assertEqual(cannon.level(3)["BuildCost"], "7")
        self.assertIsNone(cannon.level(0))
        self.assertIsNone(cannon.level(4))

    def test_plain_text_and_signature_header(self):
        self.assertEqual(gamedata.decode_csv(HEROES.encode()), HEROES)
        self.assertEqual(gamedata.decode_csv(b"Sig:" + b"\0" * 64 + HEROES.encode()), HEROES)


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        for name, text in (("buildings", BUILDINGS), ("heroes", HEROES), ("texts", TEXTS)):
            with open(os.path.join(d, name + ".csv"), "w", encoding="utf-8") as f:
                f.write(text)
        with open(os.path.join(d, "version.json"), "w") as f:
            f.write('{"version": "1.2.3"}')

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_and_names(self):
        gd = gamedata.GameData.load(self.tmp.name)
        self.assertEqual(gd.version, "1.2.3")
        self.assertEqual(gd.name(1000001), "Town Hall")
        self.assertEqual(gd.name(1000000), "Cannon")        # no text -> internal name
        self.assertEqual(gd.name(28000000), "Barbarian King")
        self.assertEqual(gd.name(99), "#99")

    def test_extract_from_apk(self):
        apk = os.path.join(self.tmp.name, "pack.apk")
        with zipfile.ZipFile(apk, "w") as z:
            for table in (*gamedata.TABLES.values(), *gamedata.EXTRA_TABLES):
                z.writestr(f"assets/logic/{table}.csv",
                           {"buildings": BUILDINGS, "heroes": HEROES}.get(table, '"Name"\n"String"\n'))
            z.writestr("assets/localization/texts.csv", TEXTS)
            z.writestr("assets/localization/texts_patch.csv",
                       '"TID","EN"\n"string","string"\n"TID_BK","Barbarian King (patched)"\n')
        out = os.path.join(self.tmp.name, "out")
        gamedata.extract_from_apk(apk, out, "9.9.9")
        gd = gamedata.GameData.load(out)
        self.assertEqual(gd.version, "9.9.9")
        self.assertEqual(gd.name(28000000), "Barbarian King (patched)")
        self.assertEqual(gd.name(1000001), "Town Hall")


def _real_static_dir():
    dirs = sorted(glob.glob(os.path.join(gamedata.DEFAULT_ROOT, "*", "version.json")))
    return os.path.dirname(dirs[-1]) if dirs else None


@unittest.skipUnless(_real_static_dir(), "no decoded game data under data/static (run: python -m village static)")
class RealDataTest(unittest.TestCase):
    """Phase 2 acceptance: every ID in a real export resolves to a named object."""

    def test_every_export_id_resolves(self):
        gd = gamedata.GameData.load(_real_static_dir())
        snap = parse_export(export_text(load_raw()))
        missing = sorted({(e.section, e.data) for e in snap.entries
                          if e.section not in ("decos", "decos2") and gd.get(e.data) is None})
        self.assertEqual(missing, [])

    def test_known_ids(self):
        gd = gamedata.GameData.load(_real_static_dir())
        self.assertEqual(gd.name(1000001), "Town Hall")
        self.assertEqual(gd.name(1000010), "Wall")
        self.assertEqual(gd.name(28000000), "Barbarian King")
        self.assertEqual(gd.name(93000000), "Builder's Apprentice")


if __name__ == "__main__":
    unittest.main()
