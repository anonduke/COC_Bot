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
    try:
        digits = wall._classify_digit_mask(wall._storage_mask(crop))
    except cv2.error:
        # wall's splitter can produce an empty slice on thin, wide specks
        # (e.g. a loading screen); that is "not readable", not a crash.
        return None
    if not digits or len(digits) > MAX_DIGITS[name]:
        return None
    return int(digits)


def read_resources(img_bgr):
    return Resources(**{name: read_value(img_bgr, name) for name in REGIONS})


# The home-village builder counter "free/total" above the Build button.
BUILDER_COUNTER = (1415, 672, 80, 44)


def read_builder_counter(img_bgr):
    """(free, total) from the home-screen counter, or None if it can't be read.
    Both are single digits, so the counter is exactly three blobs:
    digit, slash, digit; anything else (popup, merged blobs) gives None."""
    import numpy as np
    x, y, w, h = BUILDER_COUNTER
    crop = cv2.cvtColor(img_bgr[y:y + h, x:x + w], cv2.COLOR_BGR2RGB)
    wall = _wall()
    mask = wall._storage_mask(crop)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    blobs = sorted(range(1, n), key=lambda i: stats[i, cv2.CC_STAT_LEFT])
    if len(blobs) != 3:
        return None
    digits = []
    for i in (blobs[0], blobs[2]):
        try:
            d = wall._classify_digit_mask(np.where(labels == i, 255, 0).astype(np.uint8))
        except cv2.error:   # see read_value
            return None
        if len(d) != 1:
            return None
        digits.append(int(d))
    free, total = digits
    return (free, total) if 0 <= free <= total and total > 0 else None
