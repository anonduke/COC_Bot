"""Get the game to its home screen, using adb only.

A small first piece of the Phase 4 emulator manager:
  1. answer a System UI ANR dialog with Wait;
  2. home already? (the HUD's builder counter and gold read cleanly);
  3. close known popups: "Anyone there?" (RELOAD GAME), LDPlayer's top-up
     ad, "Welcome back Chief" Okay;
  4. otherwise restart Clash (force-stop + launch) and wait for home,
     closing the same popups as they appear. This also clears the
     "Anyone there?" inactivity dialog.

Two popup templates are the existing ones in misc/, captured on the LDPlayer
window (1196 px wide render area) and scaled to the 1600x900 adb frame;
reload_game_adb.png was cut from an adb screenshot.
"""
import os
import time

import cv2

from . import adb, rewards
from .resources import read_builder_counter, read_value

_MISC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "misc")
_WINDOW_TO_ADB = 1600 / 1196
# (template, scale to the adb frame). Tapping any of them is harmless.
POPUPS = (
    ("reload_game_adb.png", 1.0),           # "Anyone there?" -> RELOAD GAME (adb capture)
    ("ld_ad_close.png", _WINDOW_TO_ADB),     # LDPlayer top-up ad (window capture)
    ("welcome_okay.png", _WINDOW_TO_ADB),    # "Welcome back Chief" Okay (window capture)
)
POPUP_MATCH = 0.85
_cache = {}


def _popup_template(name, scale):
    if name not in _cache:
        img = cv2.imread(os.path.join(_MISC, name), cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(f"misc/{name}")
        _cache[name] = img if scale == 1.0 else cv2.resize(
            img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return _cache[name]


def is_home(img):
    """Home screen with nothing covering the HUD."""
    return read_builder_counter(img) is not None and read_value(img, "gold") is not None


def close_popup(img, serial=adb.SERIAL):
    """Tap the first known popup button found; returns its name or None."""
    for name, scale in POPUPS:
        tpl = _popup_template(name, scale)
        res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, (x, y) = cv2.minMaxLoc(res)
        if score >= POPUP_MATCH:
            adb.tap(x + tpl.shape[1] // 2, y + tpl.shape[0] // 2, serial)
            return name
    return None


def _settle(timeout, serial, log):
    """Poll until home, answering ANRs and closing popups on the way."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if adb.dismiss_systemui_anr(serial):
                log("dismissed a System UI ANR dialog (Wait)")
                continue
            img = adb.screencap(serial)
        except adb.AdbError:
            time.sleep(2)   # black frames while the game (re)loads, or adb hiccup
            continue
        if is_home(img) and rewards.classify(img) is None:
            return True
        if rewards.classify(img):
            for kind in rewards.clear_rewards(serial, log):
                log(f"handled reward screen: {kind}")
            continue
        closed = close_popup(img, serial)
        if closed:
            log(f"closed popup {closed}")
            time.sleep(1.5)
        else:
            time.sleep(2)
    return False


UNKNOWN_DIR = os.path.join(os.path.dirname(_MISC), "data", "screens", "unknown")


def _wait_focus(serial, timeout, log):
    """Wait for Clash to own the focus, answering System UI dialogs (which
    take the focus while they are up)."""
    end = time.time() + timeout
    while time.time() < end:
        if adb.dismiss_systemui_anr(serial):
            log("dismissed a System UI ANR dialog (Wait)")
        if adb.wait_for_focus(adb.COC_PACKAGE, 3, serial):
            return True
    return False


def ensure_home(serial=adb.SERIAL, log=print, restart_timeout=180, first_timeout=60):
    """True once the home screen is showing. Restarts Clash at most once.
    The screen that wasn't recognised is saved to data/screens/unknown/ so
    new popups can be added to POPUPS."""
    # Long enough for RELOAD GAME + Welcome back to play out without a restart.
    if _settle(first_timeout, serial, log):
        return True
    try:
        os.makedirs(UNKNOWN_DIR, exist_ok=True)
        path = os.path.join(UNKNOWN_DIR, time.strftime("%Y%m%d_%H%M%S.png"))
        cv2.imwrite(path, adb.screencap(serial))
        log(f"home screen not found (saved {path}); restarting Clash")
    except (adb.AdbError, OSError) as e:
        log(f"home screen not found ({e}); restarting Clash")
    adb.shell(f"am force-stop {adb.COC_PACKAGE}", serial=serial)
    time.sleep(2)
    adb.shell(f"monkey -p {adb.COC_PACKAGE} -c android.intent.category.LAUNCHER 1",
              serial=serial)
    if not _wait_focus(serial, 40, log):
        log("Clash did not come to the front after launch")
        return False
    return _settle(restart_timeout, serial, log)
