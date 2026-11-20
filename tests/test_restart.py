import unittest
from unittest import mock

from tests.test_accounts import FakeCap, snap
from village import accounts, emulator
from village.store import Store


class StopTest(unittest.TestCase):
    def test_hung_emulator_is_force_closed(self):
        # `quit` ignored: still running after the wait, gone after taskkill
        with mock.patch.object(emulator, "ldconsole"), \
                mock.patch.object(emulator, "_wait_stopped", side_effect=[False, True]), \
                mock.patch.object(emulator, "kill_processes") as kill:
            emulator.stop(log=lambda m: None)
        kill.assert_called_once()

    def test_clean_quit_still_clears_leftover_helpers(self):
        with mock.patch.object(emulator, "ldconsole"), \
                mock.patch.object(emulator, "_wait_stopped", return_value=True), \
                mock.patch.object(emulator, "kill_processes") as kill:
            emulator.stop(log=lambda m: None)
        kill.assert_called_once()

    def test_kill_list_covers_the_virtualbox_helpers(self):
        for name in ("dnplayer.exe", "Ld9BoxHeadless.exe", "VBoxNetNAT.exe", "VBoxNetDHCP.exe",
                     "Ld9BoxSVC.exe"):
            self.assertIn(name, emulator.LD_PROCESSES)

    def test_still_running_after_force_close_is_an_error(self):
        with mock.patch.object(emulator, "ldconsole", side_effect=emulator.EmulatorError("timed out")), \
                mock.patch.object(emulator, "_wait_stopped", return_value=False), \
                mock.patch.object(emulator, "kill_processes"):
            with self.assertRaises(emulator.EmulatorError):
                emulator.stop(log=lambda m: None)


class RestartJobTest(unittest.TestCase):
    def setUp(self):
        self.s = Store(":memory:")
        self.s.save_snapshot(snap("#AAA"))
        self.s.set_active("#AAA")
        self.s.kv_set(self.s.akey("fail_streak"), {"n": 3, "at": 0})
        self.calls = []
        for name in ("stop", "restart_adb", "start", "ensure_ready"):
            p = mock.patch.object(emulator, name, side_effect=lambda *a, n=name, **k: self.calls.append(n))
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(accounts, "_gd_version", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def test_restart_runs_every_step_and_clears_the_back_off(self):
        with mock.patch.object(accounts, "capture_for", return_value=FakeCap("#AAA")) as cap:
            self.assertEqual(accounts.run_restart(self.s, log=lambda m: None), 0)
        self.assertEqual(self.calls, ["stop", "restart_adb", "start", "ensure_ready"])
        self.assertEqual(cap.call_args[0][1], "#AAA")              # the active account, verified
        self.assertEqual(self.s.kv_get(self.s.akey("fail_streak"))["n"], 0)
        job = self.s.kv_get("account_job")
        self.assertEqual((job["kind"], job["state"]), ("restart", "done"))

    def test_failed_restart_is_reported(self):
        emulator.start.side_effect = emulator.EmulatorError("Android did not finish booting")
        self.assertEqual(accounts.run_restart(self.s, log=lambda m: None), 1)
        job = self.s.kv_get("account_job")
        self.assertEqual(job["state"], "failed")
        self.assertIn("did not finish booting", job["message"])
        self.assertEqual(self.s.kv_get(self.s.akey("fail_streak"))["n"], 3)   # unchanged
        self.assertEqual(self.s.notifications(1)[0]["source"], "emulator")
