import os
import unittest

import cv2
import numpy as np

from tests.helpers import FIXTURES
from village.resources import read_resources
from village.store import Resources

# home_bars.png is the top-right corner (x 1280.., y 20..) of a live 1600x900
# home screenshot taken 2026-11-14 14:00.
ORIGIN = (1280, 20)


def home_screen():
    bars = cv2.imread(os.path.join(FIXTURES, "home_bars.png"))
    img = np.zeros((900, 1600, 3), np.uint8)
    h, w = bars.shape[:2]
    img[ORIGIN[1]:ORIGIN[1] + h, ORIGIN[0]:ORIGIN[0] + w] = bars
    return img


class ResourcesTest(unittest.TestCase):
    def test_reads_all_four(self):
        self.assertEqual(read_resources(home_screen()),
                         Resources(gold=21975210, elixir=11458224, dark_elixir=169833, gems=4875))

    def test_dimmed_screen_reads_nothing(self):
        # A popup dims the HUD to ~40%; digits must not be misread.
        dim = (home_screen() * 0.4).astype(np.uint8)
        self.assertEqual(read_resources(dim), Resources())

    def test_blank_screen_reads_nothing(self):
        self.assertEqual(read_resources(np.zeros((900, 1600, 3), np.uint8)), Resources())


if __name__ == "__main__":
    unittest.main()
