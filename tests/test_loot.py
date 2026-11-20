import glob
import os
import re
import unittest

import cv2
import numpy as np

from village import loot

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CROPS = sorted(glob.glob(os.path.join(REPO, "data", "farm_runs", "run_12", "loot", "*.png")))


def read(img):
    import sys
    sys.path.insert(0, REPO)
    import Ranked_Copy as R
    return R._ocr_loot_mask(*loot.digit_mask(img))


@unittest.skipUnless(CROPS, "no saved loot crops (data/farm_runs/run_12/loot)")
class LootTest(unittest.TestCase):
    def test_normal_cream_digits(self):
        for f in CROPS[::4]:
            want = re.search(r"_(\d+)\.png$", f).group(1)
            self.assertEqual(read(cv2.imread(f)), want, f)

    def test_cake_event_yellow_digits(self):
        # During the cake event the digits are yellow (S ~153 instead of ~51);
        # the old cream-only mask then read 46-48. Recolour cream to yellow.
        f = CROPS[0]
        want = re.search(r"_(\d+)\.png$", f).group(1)
        hsv = cv2.cvtColor(cv2.imread(f), cv2.COLOR_BGR2HSV)
        cream = (hsv[:, :, 2] > 170) & (hsv[:, :, 1] < 90)
        hsv[cream, 0], hsv[cream, 1] = 30, 153
        self.assertEqual(read(cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)), want)


if __name__ == "__main__":
    unittest.main()
