import os
import shutil
import unittest

import cv2
import numpy as np

from village.player import clean_clan, clean_name, read_player

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
HAVE_TESSERACT = os.path.exists(r"C:\Program Files\Tesseract-OCR\tesseract.exe") or shutil.which("tesseract")


class CleanTest(unittest.TestCase):
    def test_clean_name(self):
        self.assertEqual(clean_name("RAHMAN -iv"), "RAHMAN -iv")
        self.assertEqual(clean_name("  |Chief  Bob*\nnoise"), "Chief Bob")
        self.assertEqual(clean_name(""), "")
        self.assertEqual(len(clean_name("x" * 60)), 24)

    def test_clean_clan_cuts_ocr_noise(self):
        self.assertEqual(clean_clan('phenomenal = \ufffd-\ufffd*"*" |'), "phenomenal")
        self.assertEqual(clean_clan("The Clan  Name"), "The Clan Name")
        self.assertEqual(clean_clan("*** "), "")


@unittest.skipUnless(HAVE_TESSERACT, "tesseract not installed")
class ReadPlayerTest(unittest.TestCase):
    def test_reads_the_home_screen_corner(self):
        img = np.zeros((900, 1600, 3), np.uint8)
        crop = cv2.imread(os.path.join(FIXTURES, "home_profile.png"))
        img[:crop.shape[0], :crop.shape[1]] = crop
        p = read_player(img)
        self.assertIn("RAHMAN", p["name"])
        self.assertEqual((p["clan"], p["xp"]), ("phenomenal", 196))

    def test_unreadable_screen_gives_none(self):
        self.assertIsNone(read_player(np.zeros((900, 1600, 3), np.uint8)))
        img = np.zeros((900, 1600, 3), np.uint8)
        crop = cv2.imread(os.path.join(FIXTURES, "home_profile.png"))
        img[:crop.shape[0], :crop.shape[1]] = (crop * 0.4).astype(np.uint8)   # popup dims it
        self.assertIsNone(read_player(img))
