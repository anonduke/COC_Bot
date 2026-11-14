import copy
import os
import tempfile
import unittest

from tests.helpers import export_text, load_raw
from village.export import parse_export
from village.store import Resources, Store


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "sub", "village.db")
        self.store = Store(self.path)
        self.raw = load_raw()
        self.snap = parse_export(export_text(self.raw))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_round_trip(self):
        sid = self.store.save_snapshot(self.snap, Resources(1, 2, 3, 4), "18.647.5", captured_at=99)
        got = self.store.get(sid)
        self.assertEqual(got.snapshot.raw, self.raw)
        self.assertEqual(got.snapshot.entries, self.snap.entries)
        self.assertEqual(got.resources, Resources(1, 2, 3, 4))
        self.assertEqual((got.captured_at, got.game_version), (99, "18.647.5"))

    def test_unread_resources_are_none(self):
        got = self.store.get(self.store.save_snapshot(self.snap))
        self.assertEqual(got.resources, Resources())

    def test_same_export_saved_once(self):
        a = self.store.save_snapshot(self.snap)
        b = self.store.save_snapshot(self.snap)
        self.assertEqual(a, b)
        self.assertEqual(self.store.count(), 1)

    def test_latest_and_previous(self):
        ids = []
        for dt in (0, 600, 300):
            raw = copy.deepcopy(self.raw)
            raw["timestamp"] += dt
            ids.append(self.store.save_snapshot(parse_export(export_text(raw))))
        self.assertEqual(self.store.latest().id, ids[1])
        self.assertEqual(self.store.latest(before_id=ids[1]).id, ids[2])
        self.assertEqual(self.store.latest(before_id=ids[0]), None)
        self.assertEqual(self.store.latest(tag="#NOPE"), None)

    def test_reopen_keeps_data_and_schema(self):
        self.store.save_snapshot(self.snap)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(self.store.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")


if __name__ == "__main__":
    unittest.main()
