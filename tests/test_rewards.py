import os
import unittest

import cv2

from tests.helpers import FIXTURES
from village import rewards

# Full live frames (2026-11-19, game 19.32), JPEG.


def frame(name):
    return cv2.imread(os.path.join(FIXTURES, name))


class ClassifyTest(unittest.TestCase):
    def test_screens(self):
        self.assertEqual(rewards.classify(frame("reward_star_bonus.jpg")), "star_bonus")
        self.assertEqual(rewards.classify(frame("reward_chest.jpg")), "chest")      # Skip faded out
        self.assertEqual(rewards.classify(frame("reward_continue.jpg")), "reward")
        self.assertIsNone(rewards.classify(frame("reward_home.jpg")))


class ClearTest(unittest.TestCase):
    def test_walks_chest_to_home(self):
        screens = ["reward_star_bonus.jpg", "reward_chest.jpg", "reward_chest.jpg",
                   "reward_continue.jpg", "reward_home.jpg"]
        taps, shown = [], list(screens)
        orig = (rewards.adb.screencap, rewards.adb.tap, rewards.time.sleep)
        rewards.adb.screencap = lambda serial=None: frame(shown[0] if len(shown) == 1 else shown.pop(0))
        rewards.adb.tap = lambda x, y, serial=None: taps.append((x, y))
        rewards.time.sleep = lambda s: None
        try:
            got = rewards.clear_rewards(log=lambda m: None)
        finally:
            rewards.adb.screencap, rewards.adb.tap, rewards.time.sleep = orig
        self.assertEqual(got, ["star_bonus", "chest", "chest", "reward"])
        self.assertEqual(taps[0], rewards.CLAIM_TAP)
        self.assertEqual(taps[1:3], [rewards.CHEST_TAP] * 2)


if __name__ == "__main__":
    unittest.main()
