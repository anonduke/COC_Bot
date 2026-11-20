"""In-battle helpers (adb, independent of the LDPlayer window position).

Battle speed: after troops are deployed the battle screen shows a green
"1x" button at the right edge (game 19.32+). One tap switches it to "4x" and
the battle plays four times faster; another tap switches back to 1x (live
2026-11-19: 1x -> 4x -> 1x -> 4x). So: tap once, check it reads 4x, and tap
once more only if the first tap didn't register.
"""
import os
import time

import cv2

from . import adb

_MISC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "misc")
SPEED_TAP = (1534, 510)
SPEED_AREA = (1440, 440, 1600, 600)    # x0, y0, x1, y1 searched for the label
_tpl = {}


def _score(img, name):
    if name not in _tpl:
        _tpl[name] = cv2.imread(os.path.join(_MISC, name), cv2.IMREAD_COLOR)
    x0, y0, x1, y1 = SPEED_AREA
    return cv2.minMaxLoc(cv2.matchTemplate(img[y0:y1, x0:x1], _tpl[name], cv2.TM_CCOEFF_NORMED))[1]


def speed_state(img):
    """'1x', '4x', or None (no speed button: battle not started / over)."""
    s1, s4 = _score(img, "speed_1x.png"), _score(img, "speed_4x.png")
    if max(s1, s4) < 0.9:
        return None
    return "1x" if s1 > s4 else "4x"


def speed_up(serial=adb.SERIAL, log=print):
    """Switch the battle to 4x. Returns True if it reads 4x afterwards."""
    for attempt in range(2):
        state = speed_state(adb.screencap(serial))
        if state == "4x":
            log(f"battle speed 4x ({attempt} tap{'s' if attempt != 1 else ''})")
            return True
        if state is None:
            log("battle speed button not shown")
            return False
        adb.tap(*SPEED_TAP, serial=serial)
        time.sleep(0.8)
    ok = speed_state(adb.screencap(serial)) == "4x"
    log(f"battle speed {'4x' if ok else 'still 1x'}")
    return ok
