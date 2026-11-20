"""Post-battle reward screens (game 19.32+, seen live 2026-11-19).

After Return Home the game can show, a few seconds later:
  1. "Star Bonus received!" with a Claim button (free loot, no gems);
  2. Claim opens a reward CHEST on a wooden table: "Tap!", a Skip button
     bottom-right, and four lightning slots that fill with each tap. Live:
     4 taps on the chest opened it;
  3. the reward ("Power Pancakes ... delivered to the Temporary Storage")
     with a green Continue button. Centre taps do nothing here: without
     Continue the game stays on this screen forever;
  4. Continue -> back to the home village.
The attack code knew none of these, so the next attack clicked into them
(a click landed on Claim) and then waited for Find a Match that never came.

clear_rewards() walks through any of these screens using adb only. It only
ever taps: Claim (inside a recognised Star Bonus popup), the chest (screen
centre, while the chest screen is recognised) and Continue. All three are
free; none of these screens has a gem button.
"""
import os
import time

import cv2

from . import adb

_MISC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "misc")

# template name -> (expected top-left, minimum score)
MARKERS = {
    "star_bonus_title.png": ((510, 160), 0.75),   # cut from a recording frame
    "reward_continue.png": ((655, 730), 0.85),
    "chest_skip.png": ((1450, 792), 0.85),
    "chest_bolts.png": ((594, 694), 0.55),          # lightning slots under a closed chest
}
POS_TOL = 25
CLAIM_TAP = (800, 705)                              # Claim, inside the Star Bonus popup
CHEST_TAP = (800, 450)                              # screen centre = the chest
_tpl = {}


def _match(img, name):
    if name not in _tpl:
        t = cv2.imread(os.path.join(_MISC, name), cv2.IMREAD_COLOR)
        if t is None:
            raise FileNotFoundError(f"misc/{name}")
        _tpl[name] = t
    (ex, ey), min_score = MARKERS[name]
    res = cv2.matchTemplate(img, _tpl[name], cv2.TM_CCOEFF_NORMED)
    _, score, _, (x, y) = cv2.minMaxLoc(res)
    if score >= min_score and abs(x - ex) <= POS_TOL and abs(y - ey) <= POS_TOL:
        return x, y
    return None


def classify(img):
    """'star_bonus', 'reward' (Continue), 'chest' or None."""
    if _match(img, "star_bonus_title.png"):
        return "star_bonus"
    if _match(img, "reward_continue.png"):
        return "reward"
    if _match(img, "chest_skip.png") or _match(img, "chest_bolts.png"):
        return "chest"
    return None


def clear_rewards(serial=adb.SERIAL, log=print, appear_within=0.0, max_steps=40):
    """Handle reward screens until none is left. `appear_within`: keep
    looking this many seconds for one to show up (the Star Bonus popup comes
    ~6 s after Return Home). Returns the list of screens handled."""
    handled = []
    deadline = time.time() + appear_within
    for _ in range(max_steps):
        try:
            img = adb.screencap(serial)
        except adb.AdbError:
            time.sleep(1)
            continue
        kind = classify(img)
        if kind is None:
            if time.time() >= deadline:
                break
            time.sleep(1)
            continue
        if kind == "star_bonus":
            log("Star Bonus popup: Claim")
            adb.tap(*CLAIM_TAP, serial=serial)
        elif kind == "reward":
            x, y = _match(img, "reward_continue.png")
            log("reward screen: Continue")
            adb.tap(x + 130, y + 40, serial=serial)
        else:
            log("reward chest: tap")
            adb.tap(*CHEST_TAP, serial=serial)
        handled.append(kind)
        deadline = time.time() + 4      # another screen usually follows within a few s
        time.sleep(1.3)
    return handled
