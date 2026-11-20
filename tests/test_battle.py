import os
import unittest

import cv2
import numpy as np

from tests.helpers import FIXTURES
from village import battle


def frame(i):
    return cv2.imread(os.path.join(FIXTURES, f"battle_speed_{i}.png"))


class SpeedTest(unittest.TestCase):
    def test_states(self):
        self.assertEqual(battle.speed_state(frame(0)), "1x")
        self.assertEqual(battle.speed_state(frame(1)), "4x")
        self.assertIsNone(battle.speed_state(np.zeros((900, 1600, 3), np.uint8)))

    def test_one_tap_toggles_to_4x(self):
        taps, shown = [], [frame(0)]
        orig = (battle.adb.screencap, battle.adb.tap, battle.time.sleep)
        battle.adb.screencap = lambda serial=None: shown[-1]
        battle.adb.tap = lambda x, y, serial=None: (taps.append((x, y)), shown.append(frame(1)))
        battle.time.sleep = lambda s: None
        try:
            self.assertTrue(battle.speed_up(log=lambda m: None))
        finally:
            battle.adb.screencap, battle.adb.tap, battle.time.sleep = orig
        self.assertEqual(taps, [battle.SPEED_TAP])          # exactly one tap


if __name__ == "__main__":
    unittest.main()
