"""Read gold, elixir, dark elixir and gems from a home-screen screenshot.

Works on adb screenshots (1600x900, see adb.screencap), so it does not need
the LDPlayer window. The digit reading itself is wall.py's: the same white
text mask and per-digit matcher that already reads gold/elixir reliably.
"""
import os
import sys

import cv2

from .store import Resources

# (x, y, w, h) of the number on each home-screen bar, 1600x900 screen.
REGIONS = {
    "gold": (1285, 28, 245, 40),
    "elixir": (1285, 90, 245, 40),
    "dark_elixir": (1340, 151, 190, 40),
    "gems": (1375, 214, 155, 40),
}
MAX_DIGITS = {"gold": 8, "elixir": 8, "dark_elixir": 7, "gems": 6}


_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _wall():
    # wall.py lives at the repo root; make it importable whatever the cwd is
    # (e.g. when started by Task Scheduler). Lazy: it pulls in pyautogui.
    if _REPO not in sys.path:
        sys.path.insert(0, _REPO)
    import wall
    return wall


def read_value(img_bgr, name):
    """One resource as int, or None if no digits were found."""
    x, y, w, h = REGIONS[name]
    crop = cv2.cvtColor(img_bgr[y:y + h, x:x + w], cv2.COLOR_BGR2RGB)
    wall = _wall()
    digits = wall._classify_digit_mask(wall._storage_mask(crop))
    if not digits or len(digits) > MAX_DIGITS[name]:
        return None
    return int(digits)


def read_resources(img_bgr):
    return Resources(**{name: read_value(img_bgr, name) for name in REGIONS})
