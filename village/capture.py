"""Capture the in-game data export through adb: no mouse, and the LDPlayer
window may be covered.

Flow (each step is checked before the next one runs):
 1. Clash is the focused app; a "System UI isn't responding" dialog is
    cleared first.
 2. The user's clipboard text is saved and a sentinel is put in its place.
 3. Deep link to More Settings; wait for its title at the expected position.
 4. Drag the label column up until the "Export Village data" row is at its
    end-of-list position on two frames in a row (the list stopped moving).
 5. Tap Copy.
 6. Wait for the clipboard to change; parse and validate the export.
 7. Tap Close and check the dialog is gone. Restore the user's clipboard.

Safety: Delete Account and Change Name are on this screen. The flow taps
only Copy and Close, each at an offset from a template match that must sit
at its expected position, and drags only on the label column (x=560),
which has no buttons.

Known limitation: if the user copies something while a capture waits for
the export, that capture fails and the clipboard is restored to what it
held before the capture started.
"""
import os
import time

import cv2

from . import adb, clipboard
from .export import ExportError, parse_export
from .resources import read_resources

MORE_SETTINGS_LINK = "https://link.clashofclans.com/en/?action=OpenMoreSettings"
_MISC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "misc")

# Positions on the 1600x900 emulator screen (top-left of the template match).
TITLE_POS = (680, 82)          # "More Settings" title
CLOSE_OFFSET = (610, 15)       # red X, relative to TITLE_POS -> (1290, 97)
ROW_POS = (358, 540)           # "Export Village data in JSON format", list at its end
COPY_OFFSET = (774, 20)        # Copy button, relative to ROW_POS -> (1132, 560)
DRAG_X, DRAG_FROM_Y, DRAG_TO_Y = 560, 720, 230
MATCH_MIN = 0.85
POS_TOL = 12
MAX_DRAGS = 16
BLIND_DRAGS = 8                # live: the row is reached after 8 drags from the top

SENTINEL = "coc-bot: waiting for village export"


class CaptureError(RuntimeError):
    pass


def _template(name):
    img = cv2.imread(os.path.join(_MISC, name), cv2.IMREAD_COLOR)
    if img is None:
        raise CaptureError(f"template misc/{name} missing")
    return img


def _find(img, tpl):
    """(score, (x, y)) of the best match."""
    res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    return score, loc


def _at(match, expected):
    score, (x, y) = match
    return (score >= MATCH_MIN and abs(x - expected[0]) <= POS_TOL
            and abs(y - expected[1]) <= POS_TOL)


def _wait_for(check, timeout, serial, interval=0.7):
    """Screenshot until check(img) returns a truthy value; return it or None.
    A System UI ANR dialog covering the screen is dismissed along the way
    (it can appear at any moment on this emulator)."""
    end = time.time() + timeout
    dismissed = 0
    while True:
        result = check(adb.screencap(serial))
        if result or time.time() >= end:
            return result
        if dismissed < 3 and adb.dismiss_systemui_anr(serial):
            dismissed += 1
            end += 2  # the dialog cost us time; don't count it
            continue
        time.sleep(interval)


def _title(img, tpl):
    m = _find(img, tpl)
    return m if _at(m, TITLE_POS) else None


def _close_dialog(title_tpl, serial):
    """Tap Close only if the More Settings title is where we expect it."""
    m = _wait_for(lambda img: _title(img, title_tpl), 3, serial)
    if not m:
        return False
    x, y = m[1]
    adb.tap(x + CLOSE_OFFSET[0], y + CLOSE_OFFSET[1], serial)
    return bool(_wait_for(lambda img: _title(img, title_tpl) is None, 5, serial))


def capture_export(expected_tag=None, max_age=300, serial=adb.SERIAL, log=print,
                   screen_dir=None):
    """Run the flow once. Returns (export_text, Snapshot, Resources); the
    resources are read from the home screen before More Settings opens
    (fields are None if not readable, e.g. a popup was dimming the screen).
    If screen_dir is set, that screenshot is saved there for the OCR test set.
    Raises CaptureError (or adb.AdbError) with the reason on any failed check."""
    title_tpl, row_tpl = _template("more_settings_title.png"), _template("export_row.png")

    if adb.dismiss_systemui_anr(serial):
        log("dismissed a System UI ANR dialog (Wait)")
    if not adb.wait_for_focus(adb.COC_PACKAGE, 8, serial):
        raise CaptureError(f"Clash is not in front: {adb.focused_window(serial)}")

    home = adb.screencap(serial)
    resources = read_resources(home)
    if screen_dir:
        os.makedirs(screen_dir, exist_ok=True)
        cv2.imwrite(os.path.join(screen_dir, time.strftime("home_%Y%m%d_%H%M%S.png")), home)

    saved_clip = clipboard.get_text()
    clipboard.set_text(SENTINEL)
    try:
        adb.shell(f"am start -a android.intent.action.VIEW -d '{MORE_SETTINGS_LINK}' "
                  f"-p {adb.COC_PACKAGE}", serial=serial)
        if not _wait_for(lambda img: _title(img, title_tpl), 8, serial):
            raise CaptureError("More Settings did not open")

        # Do the expected drags in one adb call, then check. Overshooting is
        # harmless: the list stops at its end.
        adb.swipe(DRAG_X, DRAG_FROM_Y, DRAG_X, DRAG_TO_Y, 800, times=BLIND_DRAGS, serial=serial)
        row, prev, extra = None, None, 0
        for _ in range(MAX_DRAGS - BLIND_DRAGS + 4):
            img = adb.screencap(serial)
            if not _title(img, title_tpl):
                if adb.dismiss_systemui_anr(serial):
                    log("dismissed a System UI ANR dialog (Wait)")
                    prev = None
                    continue
                raise CaptureError("left More Settings while scrolling")
            m = _find(img, row_tpl)
            if not _at(m, ROW_POS) and adb.dismiss_systemui_anr(serial):
                # The dialog covers the row but not the title.
                log("dismissed a System UI ANR dialog (Wait)")
                prev = None
                continue
            if _at(m, ROW_POS) and prev is not None and _at(prev, ROW_POS) \
                    and m[1] == prev[1]:
                row = m
                break
            prev = m
            if not _at(m, ROW_POS):
                if BLIND_DRAGS + extra >= MAX_DRAGS:
                    break
                adb.swipe(DRAG_X, DRAG_FROM_Y, DRAG_X, DRAG_TO_Y, 800, serial=serial)
                extra += 1
            time.sleep(0.8)
        if row is None:
            raise CaptureError("could not reach the Data Export row")
        if extra:
            log(f"  needed {extra} drag(s) beyond the usual {BLIND_DRAGS}")

        adb.tap(row[1][0] + COPY_OFFSET[0], row[1][1] + COPY_OFFSET[1], serial)
        text, end = None, time.time() + 10
        while time.time() < end:
            time.sleep(0.5)
            text = clipboard.get_text()
            if text and text != SENTINEL:
                break
        if not text or text == SENTINEL:
            raise CaptureError("clipboard did not receive the export")

        try:
            snap = parse_export(text)
        except ExportError as e:
            raise CaptureError(f"clipboard is not an export: {e}") from e
        if expected_tag and snap.tag != expected_tag:
            raise CaptureError(f"export is for {snap.tag}, expected {expected_tag}")
        age = time.time() - snap.timestamp
        if abs(age) > max_age:
            raise CaptureError(f"export timestamp is {age:.0f}s off from now")
    finally:
        # Cleanup must not hide the original error. Closing is attempted even
        # if the title never showed: _close_dialog taps only when it is there.
        try:
            if not _close_dialog(title_tpl, serial):
                log("warning: More Settings was not found open, or did not close")
        except Exception as e:  # noqa: BLE001
            log(f"warning: closing More Settings failed: {e}")
        try:
            clipboard.set_text(saved_clip)
        except OSError as e:
            log(f"warning: could not restore the clipboard: {e}")
    return text, snap, resources
