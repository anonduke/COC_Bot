import os
import shutil
import unittest

import cv2
import numpy as np

from tests.helpers import FIXTURES
from village import upgrader as u

# Crops of live adb screenshots (2026-11-14), pasted back at their position:
#   panel_list.png / panel_top.png: the Buildings & Heroes panel, x 1170.., y 0..
#   dialog_title.png: an upgrade dialog's title bar, x 240.., y 30..
HAVE_TESSERACT = os.path.exists(r"C:\Program Files\Tesseract-OCR\tesseract.exe") or shutil.which("tesseract")


def screen(name, x, y):
    part = cv2.imread(os.path.join(FIXTURES, name))
    img = np.zeros((900, 1600, 3), np.uint8)
    h, w = part.shape[:2]
    img[y:y + h, x:x + w] = part
    return img


class PureTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(u.normalize("Wizard Tower x 2 oe"), "wizard tower")
        self.assertEqual(u.normalize("Builder's Hut x 5"), "builder's hut")
        self.assertEqual(u.normalize("Hidden Tesla a"), "hidden tesla")
        self.assertEqual(u.normalize("X-Bow x 2"), "x-bow")

    def test_same_name(self):
        self.assertTrue(u.same_name("Wizard Tower ;", "Wizard Tower"))
        self.assertTrue(u.same_name("WizaRd Tower", "Wizard Tower"))
        self.assertTrue(u.same_name("Longsuot", "Longshot"))           # OCR slip
        self.assertFalse(u.same_name("Super Wizard Tower x 2", "Wizard Tower"))
        self.assertFalse(u.same_name("Inferno Tower", "Wizard Tower"))
        self.assertFalse(u.same_name("", "Wizard Tower"))

    def test_parse_title(self):
        self.assertEqual(u.parse_title("Upgrade Wizard Tower to Level 14?"), ("Wizard Tower", 14))
        self.assertEqual(u.parse_title("~) Upgrade Cake-A-Pult to Level 4?"), ("Cake-A-Pult", 4))
        self.assertIsNone(u.parse_title("Buildings & Heroes"))


class ScreenTest(unittest.TestCase):
    def test_rows_and_gaps_in_the_list(self):
        img = screen("panel_list.png", 1170, 0)
        rows = u.detect_rows(img)
        self.assertGreaterEqual(len(rows), 8)
        self.assertTrue(all(55 <= b - t <= 70 for t, b in rows))
        for g in u.row_gaps(img):      # every drag start lies between two rows
            self.assertTrue(any(b < g < t2 for (_, b), (t2, _) in zip(rows, rows[1:])))

    def test_in_progress_rows_are_not_offered(self):
        img = screen("panel_top.png", 1170, 0)
        # The green in-progress rows sit at the top (y < 350).
        self.assertTrue(all(t > 350 for t, _ in u.detect_rows(img)))
        self.assertTrue(u.row_in_progress(img, 70, 110))       # Grand Warden, upgrading
        t, b = u.detect_rows(img)[0]
        self.assertFalse(u.row_in_progress(img, t, b))          # a normal row

    def test_panel_and_dialog_recognised(self):
        self.assertTrue(u.panel_open(screen("panel_top.png", 1170, 0)))
        self.assertFalse(u.dialog_open(screen("panel_top.png", 1170, 0)))

    def test_row_key_ignores_highlight_border(self):
        img = screen("panel_list.png", 1170, 0)
        t, b = u.detect_rows(img)[3]
        hi = img.copy()
        cv2.rectangle(hi, (1182, t), (1594, b), (255, 255, 255), 3)   # "opened" border
        self.assertLessEqual(np.abs(u.row_key(img, t, b) - u.row_key(hi, t, b)).max(), 1)

    def test_price_colour(self):
        # Crops of the Confirm button area (x 1000.., y 740..) of two dialogs:
        # 184 000 dark elixir in red (not enough) and 520 000 gold (enough).
        self.assertTrue(u.price_in_red(screen("dialog_price_red.png", 1000, 740)))
        self.assertFalse(u.price_in_red(screen("dialog_price_ok.png", 1000, 740)))

    @unittest.skipUnless(HAVE_TESSERACT, "Tesseract not installed")
    def test_ocr_title_and_rows(self):
        title = u.read_title(screen("dialog_title.png", 240, 30))
        self.assertEqual(u.parse_title(title), ("Wizard Tower", 9))
        img = screen("panel_list.png", 1170, 0)
        names = [u.normalize(u.read_row_name(img, t)) for t, _ in u.detect_rows(img)]
        self.assertIn("wizard tower", names)
        self.assertIn("hidden tesla", names)


class NoConfirmTest(unittest.TestCase):
    def test_module_has_no_confirm_path(self):
        # Dry run only: nothing in the module may tap a Confirm button.
        with open(u.__file__, encoding="utf-8") as f:
            src = f.read().lower()
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        self.assertNotIn("def confirm", code)
        self.assertNotIn("confirm_", code)


if __name__ == "__main__":
    unittest.main()
