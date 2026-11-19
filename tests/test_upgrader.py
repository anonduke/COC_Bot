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
        self.assertTrue(u.same_row(u.row_key(img, t, b), u.row_key(hi, t + 2, b + 2)))

    def test_row_key_tells_different_rows_apart(self):
        img = screen("panel_list.png", 1170, 0)
        keys = [u.row_key(img, t, b) for t, b in u.detect_rows(img)]
        # Rows 1 and 2 (Builder's Hut x 5, X-Bow x 2) really show the same
        # time and cost; the row name tells those apart. All others differ.
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                self.assertEqual(u.same_row(keys[i], keys[j]), (i, j) == (1, 2), (i, j))

    def test_price_colour(self):
        # Crops of the Confirm button area (x 1000.., y 740..) of two dialogs:
        # 184 000 dark elixir in red (not enough) and 520 000 gold (enough).
        self.assertTrue(u.price_in_red(screen("dialog_price_red.png", 1000, 740)))
        self.assertFalse(u.price_in_red(screen("dialog_price_ok.png", 1000, 740)))

    def test_title_level_ignores_question_mark(self):
        # Tesseract read "Level 9?" fine but "Level 10?" as "107" (game 19.32);
        # the glyph reader drops the "?" before reading the digits.
        self.assertEqual(u.read_title_level(screen("dialog_title.png", 240, 30)), 9)
        self.assertEqual(u.read_title_level(dialog("dialog_top_dd24.png", "dialog_buttons_red.png")), 24)

    @unittest.skipUnless(HAVE_TESSERACT, "Tesseract not installed")
    def test_ocr_title_and_rows(self):
        self.assertEqual(u.read_dialog(screen("dialog_title.png", 240, 30)), ("Wizard Tower", 9))
        img = screen("panel_list.png", 1170, 0)
        names = [u.normalize(u.read_row_name(img, t)) for t, _ in u.detect_rows(img)]
        self.assertIn("wizard tower", names)
        self.assertIn("hidden tesla", names)


def dialog(top, buttons):
    """A full frame with an upgrade dialog's title bar + X and its buttons."""
    img = np.zeros((900, 1600, 3), np.uint8)
    for name, (x, y) in ((top, (240, 30)), (buttons, (1000, 725))):
        part = cv2.imread(os.path.join(FIXTURES, name))
        h, w = part.shape[:2]
        img[y:y + h, x:x + w] = part
    return img


@unittest.skipUnless(HAVE_TESSERACT, "Tesseract not installed")
class ConfirmChecksTest(unittest.TestCase):
    def test_right_dialog_passes(self):
        img = dialog("dialog_top_wt9.png", "dialog_buttons_ok.png")
        self.assertEqual(u.confirm_checks(img, "Wizard Tower", 9), [])

    def test_wrong_level_or_name_blocks(self):
        img = dialog("dialog_top_wt9.png", "dialog_buttons_ok.png")
        self.assertTrue(u.confirm_checks(img, "Wizard Tower", 14))
        self.assertTrue(u.confirm_checks(img, "Archer Tower", 9))

    def test_red_price_blocks(self):
        img = dialog("dialog_top_dd24.png", "dialog_buttons_red.png")
        problems = u.confirm_checks(img, "Dragon Duke", 24)
        self.assertEqual(len(problems), 1)
        self.assertIn("red", problems[0])

    def test_no_dialog_blocks(self):
        problems = u.confirm_checks(screen("panel_top.png", 1170, 0), "Wizard Tower", 9)
        self.assertIn("upgrade dialog not open", problems)

    def test_confirm_button_must_be_in_place(self):
        img = dialog("dialog_top_wt9.png", "dialog_buttons_ok.png")
        img[725:845, 1000:1250] = 0                     # buttons gone (e.g. other popup)
        self.assertTrue(any("Confirm" in p for p in u.confirm_checks(img, "Wizard Tower", 9)))


class AfterConfirmTest(unittest.TestCase):
    def test_goblin_offer_is_not_success(self):
        img = screen("goblin_offer.png", 730, 150)
        self.assertIn("Goblin", u.popup_after_confirm(img))

    def test_plain_panel_is_fine(self):
        self.assertIsNone(u.popup_after_confirm(screen("panel_top.png", 1170, 0)))


class DryRunNeverConfirmsTest(unittest.TestCase):
    def test_dry_run_passes_confirm_false(self):
        calls = []
        orig = u._run
        u._run = lambda *a, **k: calls.append(k.get("confirm")) or u.DryRun("x", 1)
        try:
            u.dry_run("Wizard Tower", 8)
        finally:
            u._run = orig
        self.assertEqual(calls, [False])

    def test_confirm_only_reachable_through_confirm_flag(self):
        with open(u.__file__, encoding="utf-8") as f:
            src = f.read()
        # _confirm is called in exactly one place, behind `if confirm and ...`.
        calls = [l for l in src.splitlines()
                 if "_confirm(result" in l and not l.lstrip().startswith("def ")]
        self.assertEqual(len(calls), 1)
        self.assertIn("if confirm and result.affordable:\n                        _confirm(result", src)


if __name__ == "__main__":
    unittest.main()
