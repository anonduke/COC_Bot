import copy
import os
import tempfile
import unittest

from tests.helpers import export_text, load_raw
from village import store as store_module
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
        self.assertIsNone(got.counter)

    def test_counter_round_trip(self):
        got = self.store.get(self.store.save_snapshot(self.snap, counter=(1, 7)))
        self.assertEqual(got.counter, (1, 7))

    def test_all_oldest_first(self):
        for dt in (600, 0):
            raw = copy.deepcopy(self.raw)
            raw["timestamp"] += dt
            self.store.save_snapshot(parse_export(export_text(raw)))
        ts = [s.snapshot.timestamp for s in self.store.all()]
        self.assertEqual(ts, sorted(ts))
        self.assertEqual(len(self.store.all(since=ts[1])), 1)

    def test_upgrade_from_v1(self):
        # A database created before the counter columns existed.
        import sqlite3
        from village import store as store_mod
        path = os.path.join(self.tmp.name, "v1.db")
        db = sqlite3.connect(path)
        db.executescript(store_mod._MIGRATIONS[0] + "PRAGMA user_version = 1;")
        db.execute("INSERT INTO snapshots (tag, export_ts, captured_at, raw_json) VALUES (?,?,?,?)",
                   (self.snap.tag, self.snap.timestamp, 1, export_text(self.raw)))
        db.commit()
        db.close()
        s = Store(path)
        try:
            self.assertEqual(s.db.execute("PRAGMA user_version").fetchone()[0],
                             len(store_mod._MIGRATIONS))
            self.assertIsNone(s.latest().counter)
        finally:
            s.close()

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
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0],
                         len(store_module._MIGRATIONS))
        self.assertEqual(self.store.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")


if __name__ == "__main__":
    unittest.main()
