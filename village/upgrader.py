"""Upgrade executor (Phase 5): find an upgrade in the Builder menu and open
its dialog. DRY RUN ONLY in this version: it reads the dialog and closes it.
There is deliberately no function here that taps Confirm.

Flow, all through adb (see capture.py for the same rules):
  1. home screen (village.home.ensure_home);
  2. tap the builder counter -> "Buildings & Heroes" side panel;
  3. read the visible rows (Tesseract on the white row titles); skip rows in
     progress (green bars: tapping those can offer gem speed-ups);
  4. for a row whose name matches, tap it: the game opens that building's
     "Upgrade <name> to Level N?" dialog directly. Read the title, save a
     screenshot, close the dialog with its X. Right level -> done;
     wrong level (e.g. the other Wizard Tower) -> keep looking;
  5. no match visible -> scroll by dragging from the gap between two rows,
     until the list stops moving.

Safety rules, learnt live on 2026-11-14:
  * A drag that starts on a row is taken as a TAP on that row, and a row tap
    opens the upgrade dialog with Confirm showing (a Crafting Station row
    opened with its option already selected). So drags start in the dark gap
    between two rows' outlines, on a settled frame, re-checked just before.
    Even so, about one drag per run still lands as a tap (the row's touch
    area reaches into the gap): after every drag a dialog check closes it.
    Before a confirming version exists (Phase 5b) it must re-verify the
    dialog title on the very frame it taps Confirm on.
  * Every tap happens only after the screen it targets was recognised:
    builder counter on the home screen, a row while the panel is open and no
    dialog is, the dialog's X at its known position, the panel's X.
  * The dialog has two Confirm buttons: resources, and a Hammer of Building
    (a magic item). Neither is ever touched.
  * Any time a dialog is open when it shouldn't be, it is closed by its X
    before anything else.
"""
import difflib
import os
import re
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import adb
from .capture import capture_lock
from .home import ensure_home

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MISC = os.path.join(_REPO, "misc")
SHOT_DIR = os.path.join(_REPO, "data", "screens", "dryrun")

COUNTER_TAP = (1450, 690)            # home screen builder counter
PANEL_TITLE_POS = (1280, 12)         # misc/builder_panel_title.png
PANEL_CLOSE_OFFSET = (292, 16)       # panel X relative to the title -> (1572, 28)
DIALOG_X_POS = (1382, 40)            # misc/dialog_close_x.png
DIALOG_X_TAP = (33, 31)
POS_TOL = 15
MATCH = 0.88

ROW_STRIP_X = (1300, 1460)           # where row outlines are measured
ROW_NAME_X = (1238, 1470)            # row title text
ROW_TAP_X = 1330                     # inside the title text, away from icons
LIST_Y = (60, 800)
DRAG_X = 1385
MAX_PAGES = 40
TITLE_BOX = (250, 38, 1370, 92)      # dialog title, x0 y0 x1 y1

_tesseract_ready = False


class UpgradeError(RuntimeError):
    pass


@dataclass
class DryRun:
    target: str
    from_level: int
    found: bool = False
    title: str = ""
    level: int | None = None
    affordable: bool | None = None    # False: price shown in red
    reason: str = ""
    screenshots: list = field(default_factory=list)
    rows_opened: list = field(default_factory=list)   # (row text, dialog title)


# --- pure helpers (unit-tested on saved screenshots) -------------------------

def detect_rows(img):
    """[(top, bottom)] of the panel's list rows, from the dark outlines that
    frame each row. Two outlines a few pixels apart are the gap between rows."""
    x0, x1 = ROW_STRIP_X
    lum = img[LIST_Y[0]:LIST_Y[1], x0:x1].astype(int).mean(axis=(1, 2))
    bands = []
    for i, v in enumerate(lum):
        y = i + LIST_Y[0]
        if v < 60:
            if bands and y - bands[-1][1] <= 2:
                bands[-1][1] = y
            else:
                bands.append([y, y])
    return [(a1 + 1, b0 - 1) for (_, a1), (b0, _) in zip(bands, bands[1:]) if 50 <= b0 - a1 <= 80]


def row_gaps(img, lo=430, hi=760):
    """y of the gaps between two adjacent detected rows: the least risky place
    to start a scroll drag. Not risk-free: live, a drag from a gap was now and
    then still taken as a tap on the neighbouring row (the rows' touch areas
    reach into the gap). The caller checks for a dialog after every drag and
    closes it; no dialog is ever confirmed."""
    rows = detect_rows(img)
    gaps = [(b1 + t2) // 2 for (_, b1), (t2, _) in zip(rows, rows[1:]) if 0 < t2 - b1 <= 16]
    return [g for g in gaps if lo <= g <= hi]


def row_key(img, top, bottom):
    """Fingerprint of a row's time/cost text. Ignores the white border the
    game draws around a row after it was opened, and 1 px position shifts."""
    crop = cv2.cvtColor(img[top + 8:bottom - 8, 1450:1585], cv2.COLOR_BGR2GRAY)
    return cv2.resize(crop, (12, 4), interpolation=cv2.INTER_AREA).astype(int) // 24


def row_in_progress(img, top, bottom):
    """In-progress rows have a green progress fill."""
    g = img[top:bottom, 1185:1595].astype(int)
    green = (g[:, :, 1] > g[:, :, 2] + 30) & (g[:, :, 1] > g[:, :, 0] + 60)
    return green.mean() > 0.4     # live: ~0.78 on an in-progress row, ~0.10 otherwise


def normalize(name):
    """'Wizard Tower x 2 oe' -> 'wizard tower'. Drops the copy count and the
    short noise tokens OCR picks up from the time column."""
    s = re.sub(r"[^a-z0-9' .\-]", " ", name.lower())
    s = re.sub(r"\s+x\s*\d+.*$", "", s)        # " x 2" and anything after
    words = [w for w in s.split() if len(w) > 2 or w in ("x-",)] or s.split()
    return " ".join(words).strip(" .-")


def same_name(ocr_text, wanted, threshold=0.85):
    a, b = normalize(ocr_text), normalize(wanted)
    return bool(a) and difflib.SequenceMatcher(None, a, b).ratio() >= threshold


_TITLE = re.compile(r"upgrade\s+(.+?)\s+to\s+level\s*(\d+)", re.I)


def parse_title(text):
    """'Upgrade Wizard Tower to Level 14?' -> ('Wizard Tower', 14) or None."""
    m = _TITLE.search(text or "")
    return (m.group(1).strip(), int(m.group(2))) if m else None


def _tesseract():
    global _tesseract_ready
    import pytesseract
    if not _tesseract_ready:
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        _tesseract_ready = True
    return pytesseract


def _white_text(crop, fx=2):
    up = cv2.resize(crop, None, fx=fx, fy=fx, interpolation=cv2.INTER_CUBIC)
    h, s, v = cv2.split(cv2.cvtColor(up, cv2.COLOR_BGR2HSV))
    mask = ((v >= 180) & (s <= 70)) | ((h >= 15) & (h <= 40) & (s >= 110) & (v >= 180))
    return cv2.bitwise_not(mask.astype(np.uint8) * 255)


PRICE_BOX = (1035, 770, 1160, 798)  # resource price on the dialog's Confirm button


def price_in_red(img):
    """The game shows the price in red when there isn't enough of the resource
    (Confirm would then offer to buy the rest with gems). Live: ~0.37 of the
    price area is red on such a dialog, 0.0 otherwise."""
    x0, y0, x1, y1 = PRICE_BOX
    hsv = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    red = ((h <= 8) | (h >= 170)) & (s > 120) & (v > 150)
    return red.mean() > 0.1


def read_row_name(img, top):
    x0, x1 = ROW_NAME_X
    return _tesseract().image_to_string(_white_text(img[top + 4:top + 34, x0:x1]),
                                        config="--psm 7").strip()


def read_title(img):
    x0, y0, x1, y1 = TITLE_BOX
    return _tesseract().image_to_string(_white_text(img[y0:y1, x0:x1]), config="--psm 7").strip()


# --- screen checks --------------------------------------------------------------

_tpl = {}


def _template(name):
    if name not in _tpl:
        img = cv2.imread(os.path.join(_MISC, name), cv2.IMREAD_COLOR)
        if img is None:
            raise UpgradeError(f"template misc/{name} missing")
        _tpl[name] = img
    return _tpl[name]


def _at(img, name, pos):
    res = cv2.matchTemplate(img, _template(name), cv2.TM_CCOEFF_NORMED)
    _, score, _, (x, y) = cv2.minMaxLoc(res)
    ok = score >= MATCH and abs(x - pos[0]) <= POS_TOL and abs(y - pos[1]) <= POS_TOL
    return (x, y) if ok else None


def panel_open(img):
    return _at(img, "builder_panel_title.png", PANEL_TITLE_POS)


def dialog_open(img):
    return _at(img, "dialog_close_x.png", DIALOG_X_POS)


def _wait(check, timeout, serial, interval=0.6):
    end = time.time() + timeout
    while True:
        img = adb.screencap(serial)
        r = check(img)
        if r or time.time() >= end:
            return r, img
        time.sleep(interval)


def _list_strip(img):
    return img[LIST_Y[0]:LIST_Y[1], 1185:1595].astype(np.int16)


def settled_frame(serial, timeout=4.0):
    """A screenshot taken once the list has stopped moving (a drag leaves it
    gliding for a moment; rows measured on a moving frame are stale)."""
    prev = adb.screencap(serial)
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.4)
        cur = adb.screencap(serial)
        if np.abs(_list_strip(cur) - _list_strip(prev)).mean() < 1.0:
            return cur
        prev = cur
    return prev


def close_dialog(serial, log):
    """Tap the dialog's X if (and only if) it is where a dialog X belongs."""
    pos, _ = _wait(dialog_open, 2, serial)
    if not pos:
        return False
    adb.tap(pos[0] + DIALOG_X_TAP[0], pos[1] + DIALOG_X_TAP[1], serial)
    gone, _ = _wait(lambda im: not dialog_open(im), 4, serial)
    if not gone:
        log("warning: dialog did not close")
    return bool(gone)


def close_panel(serial, log):
    pos, img = _wait(panel_open, 2, serial)
    if dialog_open(img):
        close_dialog(serial, log)
        pos, img = _wait(panel_open, 2, serial)
    if pos:
        adb.tap(pos[0] + PANEL_CLOSE_OFFSET[0], pos[1] + PANEL_CLOSE_OFFSET[1], serial)
        time.sleep(1)


# --- the dry run ----------------------------------------------------------------

def dry_run(name, from_level, serial=adb.SERIAL, log=print, shot_dir=SHOT_DIR):
    """Find the upgrade `name` from_level -> from_level + 1 in the Builder menu,
    open its dialog, record it, close it. Never confirms. Returns DryRun."""
    with capture_lock(wait=30):
        return _dry_run(name, from_level, serial, log, shot_dir)


def _dry_run(name, from_level, serial, log, shot_dir):
    result = DryRun(name, from_level)
    want = from_level + 1
    os.makedirs(shot_dir, exist_ok=True)
    if not ensure_home(serial, log):
        result.reason = "could not reach the home screen"
        return result

    # The counter was just read on the home screen by ensure_home.
    adb.tap(*COUNTER_TAP, serial=serial)
    pos, img = _wait(panel_open, 6, serial)
    if not pos:
        result.reason = "the Buildings & Heroes panel did not open"
        return result

    tried = []   # (normalized name, row_key) of rows already opened
    try:
        prev = None
        for _ in range(MAX_PAGES):
            img = settled_frame(serial)
            if dialog_open(img):          # never act with a stray dialog open
                close_dialog(serial, log)
                continue
            if not panel_open(img):
                result.reason = "the panel closed unexpectedly"
                return result
            opened_one = False
            for top, bottom in detect_rows(img):
                if row_in_progress(img, top, bottom):
                    continue
                text = read_row_name(img, top)
                if not same_name(text, name):
                    continue
                key = row_key(img, top, bottom)
                if any(normalize(text) == t and np.abs(key - k).max() <= 1 for t, k in tried):
                    continue
                tried.append((normalize(text), key))
                # Open this row's dialog: tap inside the title text.
                adb.tap(ROW_TAP_X, (top + bottom) // 2, serial)
                opened, dimg = _wait(dialog_open, 4, serial)
                if not opened:
                    result.reason = f"tapping row {text!r} did not open a dialog"
                    return result
                time.sleep(0.6)                      # let the open animation finish
                dimg = adb.screencap(serial)
                title = read_title(dimg)
                parsed = parse_title(title)
                path = os.path.join(shot_dir, time.strftime("%Y%m%d_%H%M%S_") +
                                    re.sub(r"\W+", "_", name) + f"_{want}.png")
                cv2.imwrite(path, dimg)
                result.screenshots.append(path)
                result.rows_opened.append((text, title))
                log(f"  row {text!r} -> dialog {title!r}")
                close_dialog(serial, log)
                if parsed and same_name(parsed[0], name) and parsed[1] == want:
                    result.found, result.title, result.level = True, title, parsed[1]
                    result.affordable = not price_in_red(dimg)
                    result.reason = ("dialog matches the planned upgrade (dry run: not confirmed)"
                                     + ("" if result.affordable else
                                        "; the price is shown in red: not enough resources"))
                    return result
                opened_one = True
                break                                # re-read rows after the dialog
            if opened_one:
                continue
            # Nothing (new) here: scroll from a gap between rows.
            gaps = row_gaps(img)
            if not gaps:
                result.reason = "no safe place to start a scroll drag"
                return result
            y0 = gaps[-1]
            # Re-check right before the drag: same list, same place, no dialog.
            now_img = adb.screencap(serial)
            if dialog_open(now_img) or np.abs(_list_strip(now_img) - _list_strip(img)).mean() >= 1.0:
                continue
            adb.swipe(DRAG_X, y0, DRAG_X, max(LIST_Y[0] + 30, y0 - 380), 800, serial=serial)
            time.sleep(1.2)
            after = settled_frame(serial)
            if dialog_open(after):
                stamp = time.strftime("%Y%m%d_%H%M%S")
                cv2.imwrite(os.path.join(shot_dir, f"{stamp}_drag_from_{y0}_before.png"), img)
                cv2.imwrite(os.path.join(shot_dir, f"{stamp}_drag_from_{y0}_after.png"), after)
                log(f"warning: a scroll drag from y={y0} opened a dialog; closing it")
                close_dialog(serial, log)
                continue
            strip = after[LIST_Y[0]:LIST_Y[1], 1185:1595]
            if prev is not None and np.abs(strip.astype(int) - prev.astype(int)).mean() < 2:
                result.reason = (f"reached the end of the list: no {name} row opens "
                                 f"'to Level {want}'")
                return result
            prev = strip
        result.reason = "gave up after scrolling the whole list"
        return result
    finally:
        close_panel(serial, log)
