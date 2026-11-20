import os
import unittest

import cv2
import numpy as np

from tests.helpers import FIXTURES
from village.home import is_home
from village.resources import (BUILDER_COUNTER, REGIONS, read_builder_counter,
                                read_real_builder_counter, read_resources)
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

    def test_thin_specks_do_not_crash(self):
        # wall's digit splitter crashes on 1 px tall, wide blobs; the readers
        # must report "not readable" instead.
        img = np.zeros((900, 1600, 3), np.uint8)
        for x, y, w, h in (*REGIONS.values(), BUILDER_COUNTER):
            img[y + h // 2, x + 5:x + 14] = 255
        self.assertEqual(read_resources(img), Resources())
        self.assertIsNone(read_builder_counter(img))


class BuilderCounterTest(unittest.TestCase):
    def test_reads_counter(self):
        self.assertEqual(read_builder_counter(home_screen()), (1, 7))

    def test_dimmed_or_missing_counter(self):
        self.assertIsNone(read_builder_counter((home_screen() * 0.4).astype(np.uint8)))
        self.assertIsNone(read_builder_counter(np.zeros((900, 1600, 3), np.uint8)))

    def test_goblin_builder_is_not_a_real_free_builder(self):
        # 2026-11-19: "1/7" with a Goblin face = the gem-paid Goblin Builder.
        goblin = home_screen(((("home_counter_goblin.png"), (1380, 600)),))
        self.assertEqual(read_builder_counter(goblin), (1, 7))
        self.assertEqual(read_real_builder_counter(goblin), (0, 6))
        self.assertEqual(read_real_builder_counter(home_screen()), (1, 7))   # normal icon

    def test_is_home_needs_counter_and_gold(self):
        self.assertTrue(is_home(home_screen()))
        self.assertFalse(is_home(home_screen(PARTS[:1])))   # bars only
        self.assertFalse(is_home(home_screen(PARTS[1:])))   # counter only


if __name__ == "__main__":
    unittest.main()


class BuilderFreeTest(unittest.TestCase):
    """Before Confirm on a building/hero: is a REAL builder free?"""

    def setUp(self):
        from village.upgrader import builder_free
        self.check = builder_free
        self.goblin = home_screen(((("home_counter_goblin.png"), (1380, 600)),))   # "1/7" + Goblin

    def test_goblin_busy_and_a_real_builder_free(self):
        # 2026-11-20: the export says 1 real builder free (the Goblin's job is
        # running): the counter's free slot is that real builder.
        ok, counter = self.check(self.goblin, real_free=1)
        self.assertTrue(ok)
        self.assertEqual(counter, (1, 7))

    def test_only_the_goblin_is_free(self):
        self.assertFalse(self.check(self.goblin, real_free=0)[0])

    def test_counter_unreadable_or_nothing_free(self):
        self.assertFalse(self.check(np.zeros((900, 1600, 3), np.uint8), real_free=1)[0])

    def test_without_the_export_count_assume_the_goblin_is_the_free_one(self):
        self.assertFalse(self.check(self.goblin)[0])
        self.assertTrue(self.check(home_screen())[0])
