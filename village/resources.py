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


# Storage bars fill from the right (the icon end) towards the left. Live
# calibration: 20 385 195 gold filled 0.78 and 9 975 078 elixir 0.38 of the
# bar, both giving ~26 M capacity, so the full bar runs x 1252..1536.
BAR_X = (1252, 1536)
BAR_ROWS = {"gold": 64, "elixir": 124, "dark_elixir": 188}   # rows below the digits
FULL_AT = 0.985


def _bar_colour(name, hsv_px):
    h, s, v = (int(c) for c in hsv_px)
    if name == "gold":
        return 15 <= h <= 35 and s >= 150 and v >= 150
    if name == "elixir":
        return 135 <= h <= 165 and s >= 120 and v >= 120
    return 125 <= h <= 160 and s >= 120 and 30 <= v <= 110      # dark elixir: deep purple


def bar_fill(img_bgr, name):
    """Fraction of a storage bar that is filled (0..1). Walks left from the
    icon end while the pixels have the bar's colour; small gaps (sparkles,
    the digits' outline) up to 4 px are tolerated."""
    x0, x1 = BAR_X
    row = cv2.cvtColor(img_bgr[BAR_ROWS[name]:BAR_ROWS[name] + 1, x0:x1 + 1],
                       cv2.COLOR_BGR2HSV)[0]
    edge, miss = len(row), 0
    for i in range(len(row) - 1, -1, -1):
        if _bar_colour(name, row[i]):
            edge, miss = i, 0
        else:
            miss += 1
            if miss > 4:
                break
    return round((len(row) - edge) / len(row), 3)


def storages_full(img_bgr, which=("gold", "elixir")):
    return all(bar_fill(img_bgr, n) >= FULL_AT for n in which)


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


# Since game 19.32 a gem-paid Goblin Builder is counted in "free/total" and
# its face replaces the builder's on the counter (live 2026-11-19: "1/7"
# with all 6 real builders busy; Confirm then opened a gem offer).
GOBLIN_ICON_REGION = (1380, 600, 220, 120)    # x, y, w, h around the counter's icon
_goblin_tpl = None


def counter_shows_goblin(img_bgr):
    global _goblin_tpl
    if _goblin_tpl is None:
        _goblin_tpl = cv2.imread(os.path.join(_REPO, "misc", "counter_goblin.png"), cv2.IMREAD_COLOR)
    x, y, w, h = GOBLIN_ICON_REGION
    res = cv2.matchTemplate(img_bgr[y:y + h, x:x + w], _goblin_tpl, cv2.TM_CCOEFF_NORMED)
    return cv2.minMaxLoc(res)[1] >= 0.85


def read_real_builder_counter(img_bgr):
    """(free, total) counting real builders only: with the Goblin icon shown,
    one of the "free" builders is the gem-paid Goblin Builder."""
    c = read_builder_counter(img_bgr)
    if c is None or not counter_shows_goblin(img_bgr):
        return c
    return max(0, c[0] - 1), max(0, c[1] - 1)
