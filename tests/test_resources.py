import os
import unittest

import cv2
import numpy as np

from tests.helpers import FIXTURES
from village.home import is_home
from village.resources import read_builder_counter, read_resources
from village.store import Resources

# Crops of live 1600x900 home screenshots (2026-11-14), pasted back at their
# original position: the resource bars and the builder counter ("1/7").
PARTS = (("home_bars.png", (1280, 20)), ("home_counter.png", (1400, 660)))


def home_screen(parts=PARTS):
    img = np.zeros((900, 1600, 3), np.uint8)
    for name, (x, y) in parts:
        part = cv2.imread(os.path.join(FIXTURES, name))
        h, w = part.shape[:2]
        img[y:y + h, x:x + w] = part
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


class BuilderCounterTest(unittest.TestCase):
    def test_reads_counter(self):
        self.assertEqual(read_builder_counter(home_screen()), (1, 7))

    def test_dimmed_or_missing_counter(self):
        self.assertIsNone(read_builder_counter((home_screen() * 0.4).astype(np.uint8)))
        self.assertIsNone(read_builder_counter(np.zeros((900, 1600, 3), np.uint8)))

    def test_is_home_needs_counter_and_gold(self):
        self.assertTrue(is_home(home_screen()))
        self.assertFalse(is_home(home_screen(PARTS[:1])))   # bars only
        self.assertFalse(is_home(home_screen(PARTS[1:])))   # counter only


if __name__ == "__main__":
    unittest.main()
