import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import cv2
import numpy as np

from tests.helpers import export_text, load_raw
from village import accounts
from village.capture import TagMismatch
from village.export import parse_export
from village.store import Resources, Store

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
HAVE_TESSERACT = os.path.exists(r"C:\Program Files\Tesseract-OCR\tesseract.exe") or shutil.which("tesseract")


def snap(tag, ts=1_795_000_000):
    """The test export, re-tagged as another account."""
    raw = load_raw()
    raw["tag"], raw["timestamp"] = tag, ts
    return parse_export(json.dumps(raw))


def screen(fixture, x0, y0):
    img = np.zeros((900, 1600, 3), np.uint8)
    part = cv2.imread(os.path.join(FIXTURES, fixture))
    img[y0:y0 + part.shape[0], x0:x0 + part.shape[1]] = part
    return img


class SeparationTest(unittest.TestCase):
    """Every per-village thing belongs to one account and never leaks."""

    def setUp(self):
        self.s = Store(":memory:")
        self.s.save_snapshot(snap("#AAA"), Resources(1, 1, 1, 1))
        self.s.save_snapshot(snap("#BBB"), Resources(2, 2, 2, 2))
        self.s.set_active("#AAA")

    def test_snapshots_follow_the_active_account(self):
        self.assertEqual(self.s.latest().snapshot.tag, "#AAA")
        self.s.set_active("#BBB")
        self.assertEqual((self.s.latest().snapshot.tag, self.s.latest().resources.gold), ("#BBB", 2))
        self.assertEqual(self.s.count(), 1)
        self.assertEqual(self.s.count(tag=None), 2)
        self.assertEqual(len(self.s.resource_history(0)), 1)

    def test_queues_are_separate(self):
        a1 = self.s.add_plan_item(100)
        a2 = self.s.add_plan_item(101)
        self.s.set_active("#BBB")
        self.assertEqual(self.s.plan(), [])
        b1 = self.s.add_plan_item(200)
        self.assertEqual(self.s.plan()[0]["position"], 0)
        # B can't touch A's items
        self.assertFalse(self.s.update_plan_item(a1, enabled=False))
        self.assertFalse(self.s.delete_plan_item(a1))
        with self.assertRaises(ValueError):
            self.s.reorder_plan([b1, a1])
        self.s.set_active("#AAA")
        self.s.reorder_plan([a2, a1])
        self.assertEqual([i["data_id"] for i in self.s.plan()], [101, 100])

    def test_sessions_actions_farm_runs_and_kv(self):
        sid = self.s.start_session("manual")
        self.s.add_action(sid, "upgrade", "intent", 100, "X", 1)
        run = self.s.create_farm_run({"mode": "attacks"})
        self.s.kv_set(self.s.akey("last_session"), {"id": sid})
        self.s.set_active("#BBB")
        self.assertEqual((self.s.sessions(), self.s.actions(status="intent"), self.s.farm_runs()), ([], [], []))
        self.assertIsNone(self.s.kv_get(self.s.akey("last_session")))
        self.assertEqual(len(self.s.sessions(tag=None)), 1)
        self.assertEqual(self.s.farm_run(run)["tag"], "#AAA")

    def test_pinned_store_keeps_its_account(self):
        self.s.pin()
        self.s.set_active("#BBB")
        self.assertEqual(self.s.latest().snapshot.tag, "#AAA")

    def test_player_profile_is_per_account(self):
        self.s.save_snapshot(snap("#BBB", 1_795_000_100), player={"name": "B", "xp": 2})
        self.assertIsNone(self.s.kv_get(self.s.akey("player")))
        self.assertEqual(self.s.kv_get(self.s.akey("player", "#BBB"))["name"], "B")


class AdoptTest(unittest.TestCase):
    def test_rows_from_before_accounts_go_to_the_account_in_use(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "v.db")
        s = Store(path)
        s.save_snapshot(snap("#OLD"))
        # as left by the single-account version
        s.db.execute("INSERT INTO plan_items (position, data_id, created_at) VALUES (0, 7, 0)")
        s.db.execute("INSERT INTO sessions (trigger, started_at, status) VALUES ('x', 0, 'done')")
        for k, v in (("player", {"name": "Me"}), ("last_session", {"id": 1}), ("test:42", {"found": True})):
            s.db.execute("INSERT INTO kv VALUES (?, ?, 0)", (k, json.dumps(v)))
        s.db.execute("INSERT INTO settings VALUES ('player_name', '\"Chief Me\"', 0)")
        s.db.execute("DELETE FROM accounts")
        s.db.commit()
        s.close()
        s = Store(path)
        self.assertEqual(s.active_tag(), "#OLD")
        self.assertEqual([i["data_id"] for i in s.plan()], [7])
        self.assertEqual(len(s.sessions()), 1)
        self.assertEqual(s.kv_get("player:#OLD"), {"name": "Me"})
        self.assertEqual(s.kv_get("last_session:#OLD"), {"id": 1})
        self.assertEqual(s.kv_get("test:#OLD:42"), {"found": True})
        self.assertEqual(s.account("#OLD")["display_name"], "Chief Me")
        self.assertNotIn("player_name", s.settings())
        s.close()


class ScreenReadingTest(unittest.TestCase):
    def test_same_tag(self):
        self.assertTrue(accounts.same_tag("#2JV002VG2", "#2JV002VG2"))
        self.assertTrue(accounts.same_tag("#2UV002VG2", "#2JV002VG2"))     # one OCR slip
        self.assertFalse(accounts.same_tag("#2UV002VG9", "#2JV002VG2"))
        self.assertFalse(accounts.same_tag(None, "#2JV002VG2"))
        self.assertFalse(accounts.same_tag("#2JV002VG", "#2JV002VG2"))

    @unittest.skipUnless(HAVE_TESSERACT, "tesseract not installed")
    def test_reads_the_cards(self):
        cards = accounts.read_cards(screen("scid_cards.png", 1025, 430))
        self.assertEqual([(c["scid"], c["village"], c["town_hall"]) for c in cards],
                         [("Anonduke", "RAHMAN", 19), ("RAHMANIV", "RAHMAN - IV", 18),
                          ("RAHMANV", "RAHMAN V", 18)])
        self.assertTrue(all(abs(c["y"] - y) <= 10 for c, y in zip(cards, (526, 670, 814))))

    @unittest.skipUnless(HAVE_TESSERACT, "tesseract not installed")
    def test_scrolled_list_stops_before_other_ids(self):
        # top card cut off (no name) is skipped; "Log in to Supercell ID" is never a card
        cards = accounts.read_cards(screen("scid_cards_scrolled.png", 1025, 430))
        self.assertEqual([c["scid"] for c in cards], ["RAHMANV"])

    @unittest.skipUnless(HAVE_TESSERACT, "tesseract not installed")
    def test_reads_the_current_tag(self):
        self.assertEqual(accounts.read_tag(screen("scid_tag.png", 725, 174)), "#2RQPVQJ2V")
        self.assertIsNone(accounts.read_tag(np.zeros((900, 1600, 3), np.uint8)))


class FakeCap:
    def __init__(self, tag):
        self.snapshot = snap(tag)
        self.resources = Resources(5, 5, 5, 5)
        self.counter, self.home_at, self.player = None, 1_795_000_000, {"name": "Village " + tag}


class JobsTest(unittest.TestCase):
    def setUp(self):
        self.s = Store(":memory:")
        self.s.save_snapshot(snap("#AAA"))
        self.s.set_active("#AAA")
        self.s.upsert_account("#AAA", scid="IdA")
        self.s.upsert_account("#BBB", scid="IdB")
        for target, value in (("village.emulator.ensure_ready", None),
                              ("village.accounts._gd_version", None)):
            p = mock.patch(target, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def test_capture_for_switches_back_when_on_another_account(self):
        calls = []
        def fake_capture(expected_tag=None, log=None):
            if not calls:
                calls.append("first")
                raise TagMismatch("#BBB", expected_tag)
            return FakeCap(expected_tag)
        with mock.patch("village.capture.capture_export", fake_capture), \
                mock.patch.object(accounts, "switch_to", return_value="switched") as sw:
            cap = accounts.capture_for(self.s, "#AAA", log=lambda m: None)
        sw.assert_called_once()
        self.assertEqual(sw.call_args[0][:2], ("#AAA", "IdA"))
        self.assertEqual(cap.snapshot.tag, "#AAA")

    def test_capture_for_without_a_known_name_fails_safely(self):
        self.s.upsert_account("#AAA", scid=None)
        with mock.patch("village.capture.capture_export", side_effect=TagMismatch("#BBB", "#AAA")):
            with self.assertRaises(accounts.SwitchError):
                accounts.capture_for(self.s, "#AAA", log=lambda m: None)

    def test_switch_makes_the_account_active_only_after_reading_it(self):
        with mock.patch.object(accounts, "switch_to", return_value="switched"), \
                mock.patch.object(accounts, "capture_for", return_value=FakeCap("#BBB")):
            self.assertEqual(accounts.run_switch(self.s, "#BBB", log=lambda m: None), 0)
        self.assertEqual(self.s.active_tag(), "#BBB")
        self.assertEqual(self.s.latest().snapshot.tag, "#BBB")
        job = self.s.kv_get("account_job")
        self.assertEqual((job["state"], job["target"]), ("done", "#BBB"))
        self.assertEqual(self.s.account("#BBB")["village"], "Village #BBB")

    def test_failed_switch_keeps_the_old_account(self):
        with mock.patch.object(accounts, "switch_to", side_effect=accounts.SwitchError("panel did not open")):
            self.assertEqual(accounts.run_switch(self.s, "#BBB", log=lambda m: None), 1)
        self.assertEqual(self.s.active_tag(), "#AAA")
        job = self.s.kv_get("account_job")
        self.assertEqual(job["state"], "failed")
        self.assertIn("panel did not open", job["message"])

    def test_wrong_village_after_switch_is_rejected(self):
        with mock.patch.object(accounts, "switch_to", return_value="switched"), \
                mock.patch.object(accounts, "capture_for", side_effect=TagMismatch("#CCC", "#BBB")):
            self.assertEqual(accounts.run_switch(self.s, "#BBB", log=lambda m: None), 1)
        self.assertEqual(self.s.active_tag(), "#AAA")
        self.assertEqual(self.s.count(tag=None), 1)          # nothing saved

    def test_discover_learns_tags_and_returns(self):
        self.s.upsert_account("#BBB", scid=None)            # B's card name not known yet
        cards = [{"scid": "IdA", "village": "A", "town_hall": 17},
                 {"scid": "IdB", "village": "B", "town_hall": 18}]
        with mock.patch.object(accounts, "list_cards", return_value=("#AAA", cards)), \
                mock.patch.object(accounts, "open_profile_settings"), \
                mock.patch.object(accounts, "_tap_card") as tap, \
                mock.patch("village.capture.capture_export", return_value=FakeCap("#BBB")), \
                mock.patch.object(accounts, "switch_to", return_value="switched") as back, \
                mock.patch.object(accounts, "capture_for", return_value=FakeCap("#AAA")):
            self.assertEqual(accounts.run_discover(self.s, log=lambda m: None), 0)
        self.assertEqual(tap.call_args[0][0], "IdB")
        self.assertEqual(self.s.account("#BBB")["scid"], "IdB")
        self.assertEqual((self.s.account("#AAA")["position"], self.s.account("#BBB")["position"]), (0, 1))
        self.assertEqual(back.call_args[0][:2], ("#AAA", "IdA"))
        self.assertEqual(self.s.active_tag(), "#AAA")
