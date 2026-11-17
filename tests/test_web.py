import glob
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from tests.helpers import export_text, load_raw
from village import gamedata, web
from village.export import parse_export
from village.store import Resources, Store


def _real_version():
    found = sorted(glob.glob(os.path.join(gamedata.DEFAULT_ROOT, "*", "version.json")))
    return os.path.basename(os.path.dirname(found[-1])) if found else None


class WebTest(unittest.TestCase):
    version = None   # game version stored with the snapshot (None = no game data)

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "village.db")
        s = Store(cls.db)
        s.save_snapshot(parse_export(export_text(load_raw())), Resources(1, 2, 3, 4),
                        game_version=cls.version, counter=(0, 7))
        s.close()
        cls.httpd = web.serve("127.0.0.1", 0, db_path=cls.db, watch=False)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return r.status, r.read(), r.headers.get("Content-Type")

    def api(self, path, body=None, header=True):
        data = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if header:
            headers["X-Village"] = "1"
        req = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


class BasicWebTest(WebTest):
    def test_static_files(self):
        for path, ctype in (("/", "text/html"), ("/app.js", "text/javascript"), ("/app.css", "text/css")):
            status, body, ct = self.get(path)
            self.assertEqual(status, 200)
            self.assertTrue(ct.startswith(ctype))
            self.assertTrue(body)

    def test_path_traversal_is_refused(self):
        for path in ("/../village/web.py", "/..%2f..%2fwall.py", "/nope.txt"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get(path)
            self.assertEqual(ctx.exception.code, 404)

    def test_screenshot_route_is_confined(self):
        for path in ("/shots/dryrun/../../village.db", "/shots/dryrun/nope.png",
                     "/shots/dryrun/x.txt"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get(path)
            self.assertEqual(ctx.exception.code, 404)

    def test_dryrun_needs_a_queue(self):
        self.assertEqual(self.api("/api/actions/dryrun", {})[0], 409)

    def test_farm_validation_and_stop(self):
        self.assertEqual(self.api("/api/farm")[0], 200)
        self.assertEqual(self.api("/api/farm/start", {"mode": "nuke"})[0], 400)
        self.assertEqual(self.api("/api/farm/start", {"mode": "target"})[0], 400)
        self.assertEqual(self.api("/api/farm/stop", {})[0], 409)

    def test_farm_start_refused_while_game_busy(self):
        # Holding the capture lock = another job drives the game. The start is
        # refused and the queued run is closed, never left "queued".
        from village.capture import capture_lock
        with capture_lock():
            status, body = self.api("/api/farm/start", {"mode": "attacks", "attacks": 1})
        self.assertEqual(status, 409)
        _, farm = self.api("/api/farm")
        self.assertIsNone(farm["current"])
        self.assertEqual(farm["runs"][0]["status"], "failed")

    def test_stale_farm_run_is_closed(self):
        s = Store(self.db)
        rid = s.create_farm_run({"mode": "attacks", "attacks": 1})
        s.update_farm_run(rid, status="running", heartbeat_at=1)
        s.close()
        _, farm = self.api("/api/farm")
        self.assertIsNone(farm["current"])
        self.assertEqual(next(r for r in farm["runs"] if r["id"] == rid)["status"], "failed")

    def test_automation_controls(self):
        status, a = self.api("/api/automation")
        self.assertEqual(status, 200)
        self.assertFalse(a["alive"])                     # no orchestrator in tests
        self.assertEqual(self.api("/api/automation", {"paused": True, "dry_run": True})[0], 200)
        _, a = self.api("/api/automation")
        self.assertEqual((a["paused"], a["dry_run"]), (True, True))
        self.assertEqual(self.api("/api/automation", {"paused": "no"})[0], 400)
        self.assertEqual(self.api("/api/automation", {})[0], 400)
        self.assertEqual(self.api("/api/automation", {"paused": True}, header=False)[0], 403)

    def test_run_now_queues_one_command(self):
        status, r = self.api("/api/automation/run", {})
        self.assertEqual((status, r["queued"]), (200, True))
        self.assertEqual(self.api("/api/automation/run", {})[0], 409)   # already requested
        s = Store(self.db)
        for c in s.pending_commands():
            s.finish_command(c["id"], "test")
        s.close()

    def test_state(self):
        status, st = self.api("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(st["snapshot"]["tag"], "#TESTTAG")
        self.assertEqual(st["resources"], {"gold": 1, "elixir": 2, "dark_elixir": 3, "gems": 4})
        b = st["builders"]
        self.assertEqual((b["total"], b["source"], len(b["jobs"])), (7, "counter", 7))

    def test_history_and_status(self):
        self.assertEqual(self.api("/api/history?hours=10000000")[0], 200)
        status, st = self.api("/api/status")
        self.assertEqual(status, 200)
        self.assertIn("capture_running", st)

    def test_post_needs_header(self):
        status, body = self.api("/api/settings", {"jitter_min": 3}, header=False)
        self.assertEqual(status, 403)

    def test_unknown_endpoint(self):
        self.assertEqual(self.api("/api/nope")[0], 404)
        self.assertEqual(self.api("/api/nope", {})[0], 404)

    def test_bad_json(self):
        req = urllib.request.Request(self.base + "/api/settings", data=b"{not json",
                                     headers={"X-Village": "1"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 400)

    def test_settings_round_trip_and_validation(self):
        self.assertEqual(self.api("/api/settings", {"jitter_min": 7, "player_tag": "#abc123"})[0], 200)
        values = {s["key"]: s["value"] for s in self.api("/api/settings")[1]["settings"]}
        self.assertEqual((values["jitter_min"], values["player_tag"]), (7, "#ABC123"))
        for bad in ({"jitter_min": 999}, {"jitter_min": True}, {"active_start": "25:00"},
                    {"nope": 1}, {"automation_paused": "yes"}, {"player_tag": "abc"}):
            with self.subTest(bad=bad):
                self.assertEqual(self.api("/api/settings", bad)[0], 400)

    def test_notifications(self):
        s = Store(self.db)
        nid = s.notify("error", "test", "boom", "details")
        s.close()
        _, notes = self.api("/api/notifications")
        self.assertGreaterEqual(notes["unread"], 1)
        self.assertEqual(self.api("/api/notifications/ack", {"id": nid})[1]["acked"], 1)
        self.assertEqual(self.api("/api/notifications/ack", {"id": nid})[1]["acked"], 0)

    def test_plan_without_game_data(self):
        # No decoded game data for this snapshot: listing works, adding refuses.
        if self.version is None:
            status, plan = self.api("/api/plan")
            self.assertEqual(status, 200)
            # The page reads these keys unconditionally.
            self.assertEqual((plan["schedule"], plan["current"], plan["builders"]), ([], [], 0))
            self.assertEqual(self.api("/api/plan/add", {"data_id": 28000000})[0], 409)


@unittest.skipUnless(_real_version(), "no decoded game data (run: python -m village static)")
class QueueWebTest(WebTest):
    version = _real_version()

    def test_catalog(self):
        status, cat = self.api("/api/catalog")
        self.assertEqual(status, 200)
        king = next(c for c in cat["items"] if c["id"] == 28000000)
        self.assertEqual(king["kind"], "hero")

    def test_queue_round_trip(self):
        a = self.api("/api/plan/add", {"data_id": 28000000})[1]["id"]
        b = self.api("/api/plan/add", {"data_id": 1000009, "target_level": 20})[1]["id"]
        _, plan = self.api("/api/plan")
        self.assertEqual([i["id"] for i in plan["items"]], [a, b])
        self.assertTrue(plan["schedule"])
        self.assertEqual(self.api("/api/plan/order", {"ids": [b, a]})[0], 200)
        self.assertEqual(self.api("/api/plan/order", {"ids": [b]})[0], 409)       # incomplete
        self.assertEqual(self.api("/api/plan/update", {"id": a, "enabled": False})[0], 200)
        self.assertEqual(self.api("/api/plan/update", {"id": a, "enabled": "no"})[0], 400)
        self.assertEqual(self.api("/api/plan/update", {"id": 999, "note": "x"})[0], 404)
        _, plan = self.api("/api/plan")
        self.assertEqual([(i["id"], i["enabled"]) for i in plan["items"]], [(b, True), (a, False)])
        self.assertTrue(all(s["item_id"] == b for s in plan["schedule"]))
        self.assertEqual(self.api("/api/plan/add", {"data_id": 42})[0], 400)       # unknown object
        for i in (a, b):
            self.assertTrue(self.api("/api/plan/delete", {"id": i})[1]["ok"])


if __name__ == "__main__":
    unittest.main()
