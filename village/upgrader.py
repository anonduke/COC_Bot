"""Upgrade executor (Phase 5): find an upgrade in the Builder menu and open
its dialog. dry_run() reads the dialog and closes it; upgrade() (Phase 5b)
confirms it, but only when confirm_checks() passes on the frame it taps on.

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
    area reaches into the gap): after every drag a dialog check closes it,
    and such a stray dialog can never be confirmed: Confirm is only tapped
    after confirm_checks() passes on the frame the tap is based on.
  * Every tap happens only after the screen it targets was recognised:
    builder counter on the home screen, a row while the panel is open and no
    dialog is, the dialog's X at its known position, the panel's X.
  * The dialog has two Confirm buttons: resources, and a Hammer of Building
    (a magic item). The magic-item one is never touched; the resource one
    only by _confirm(), only in upgrade(), only for a free builder.
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
from .home import ensure_home, is_home
from .resources import read_builder_counter

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
DRAG_END_X = 1000                    # scroll drags end over the village, off the panel
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
    confirm_tapped: bool = False
    confirmed: bool = False           # dialog closed after Confirm (verify by export!)
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
    # Between two rows there are TWO outlines a few px apart (the bottom edge
    # of one row, the top edge of the next). A single dark line inside a row
    # (text shadow, live 19.32: "Elixir Collector x 7") is not a boundary.
    bounds = [(a, b) for a, b in zip(bands, bands[1:]) if 3 <= b[0] - a[1] <= 9]
    rows = [(b1[1] + 1, a2[0] - 1) for (_, b1), (a2, _) in zip(bounds, bounds[1:])
            if 50 <= a2[0] - b1[1] <= 80]
    # Rows next to a section header have the double outline on one side only:
    # add single-band rows that don't overlap one found above.
    single = [(a1 + 1, b0 - 1) for (_, a1), (b0, _) in zip(bands, bands[1:]) if 50 <= b0 - a1 <= 80]
    for t, b in single:
        if all(b < t2 - 3 or t > b2 + 3 for t2, b2 in rows):
            rows.append((t, b))
    return sorted(rows)


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
    """The row's time/cost text, as a grayscale crop (fixed size), to tell
    apart rows with the same name (e.g. Wizard Tower 13->14 and 9->10). Live
    2026-11-16 a 12x4 thumbnail was too coarse and made the 9->10 row look
    "already opened"."""
    mid = (top + bottom) // 2
    return cv2.cvtColor(img[mid - 24:mid + 24, 1440:1590], cv2.COLOR_BGR2GRAY).astype(np.int16)


def same_row(a, b, tol=3):
    """True if two row_key crops show the same text (allowing the white
    'opened' border and +-3 px of vertical shift). Measured on live rows:
    same row 0-0.7, different time/cost text >= 5.5."""
    best = 255
    for dy in range(-3, 4):
        x = a[max(0, dy):len(a) + min(0, dy)]
        y = b[max(0, -dy):len(b) + min(0, -dy)]
        best = min(best, float(np.abs(x - y).mean()))
    return best < tol


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


def read_title_level(img):
    """The N of "... to Level N?", read from the title's yellow glyphs with
    the game-font digit matcher, the "?" glyph dropped first. Tesseract read
    "Level 10?" as "107" on game 19.32 (the ? taken for a 7)."""
    from .resources import _wall
    x0, y0, x1, y1 = TITLE_BOX
    h, s, v = cv2.split(cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV))
    yellow = ((h >= 15) & (h <= 40) & (s >= 110) & (v >= 170)).astype(np.uint8) * 255
    n, _, st, _ = cv2.connectedComponentsWithStats(yellow, 8)
    comps = sorted((st[i] for i in range(1, n) if st[i, cv2.CC_STAT_AREA] >= 15),
                   key=lambda c: c[0])
    glyphs = []                          # components overlapping in x form one glyph
    for c in comps:
        x, w = int(c[0]), int(c[2])
        if glyphs and x <= glyphs[-1][1] - 1:
            glyphs[-1][1] = max(glyphs[-1][1], x + w)
        else:
            glyphs.append([x, x + w])
    if not glyphs:
        return None
    words = [[glyphs[0]]]
    for g in glyphs[1:]:
        (words[-1].append(g) if g[0] - words[-1][-1][1] < 12 else words.append([g]))
    digits = words[-1][:-1]              # "N?" without the "?"
    if not digits or len(words) < 2:
        return None
    try:
        text = _wall()._classify_digit_mask(yellow[:, digits[0][0]:digits[-1][1]])
    except cv2.error:
        return None
    return int(text) if text.isdigit() and len(text) == len(digits) else None


def read_dialog(img):
    """(name, level) of an upgrade dialog, or None."""
    m = re.search(r"upgrade\s+(.+?)\s+to\s+level", read_title(img) or "", re.I)
    if not m:
        return None
    level = read_title_level(img)
    if level is None:
        exact = re.search(r"level\s*(\d+)\?", read_title(img), re.I)
        level = int(exact.group(1)) if exact else None
    return m.group(1).strip(), level


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


# --- confirming (Phase 5b) ------------------------------------------------------

CONFIRM_POS = (1062, 738)            # misc/dialog_confirm.png: the RESOURCE Confirm
CONFIRM_TAP = (1120, 790)            # middle of that button (label + price)
KEYCODE_BACK = 4


def confirm_checks(img, name, want):
    """Why Confirm must NOT be tapped on this frame ([] = all checks pass).
    Everything is checked on the one frame the tap is based on."""
    problems = []
    if not dialog_open(img):
        problems.append("upgrade dialog not open")
    parsed = read_dialog(img)
    if not (parsed and same_name(parsed[0], name) and parsed[1] == want):
        problems.append(f"title is not 'Upgrade {name} to Level {want}' ({parsed})")
    if price_in_red(img):
        problems.append("price shown in red (not enough resources)")
    if not _at(img, "dialog_confirm.png", CONFIRM_POS):
        problems.append("resource Confirm button not where expected")
    return problems


def _confirm(result, name, want, serial, log, shot_dir):
    """The dialog for exactly this upgrade is open: confirm it, guarded.

    Only the resource Confirm is ever tapped, never the magic-item one and
    never a gem button. If anything but a plain close follows (a gem offer
    for missing resources or busy builders), it is dismissed with Android's
    BACK key, which never buys anything. confirmed=True only means "the
    dialog closed"; the caller verifies through a fresh export."""
    frame = adb.screencap(serial)
    problems = confirm_checks(frame, name, want)
    stamp = time.strftime("%Y%m%d_%H%M%S_") + re.sub(r"\W+", "_", name) + f"_{want}"
    if problems:
        close_dialog(serial, log)
        result.reason = "not confirmed: " + "; ".join(problems)
        return
    cv2.imwrite(os.path.join(shot_dir, stamp + "_before_confirm.png"), frame)
    adb.tap(*CONFIRM_TAP, serial=serial)
    result.confirm_tapped = True
    log(f"  tapped Confirm for {name} -> {want}")
    time.sleep(2.5)
    after = adb.screencap(serial)
    after_path = os.path.join(shot_dir, stamp + "_after_confirm.png")
    cv2.imwrite(after_path, after)
    result.screenshots.append(after_path)
    if dialog_open(after) and not confirm_checks(after, name, want):
        # The very same dialog is still up: the tap did not register.
        close_dialog(serial, log)
        result.reason = "Confirm tap did not register; dialog closed, nothing spent"
        return
    if panel_open(after) or is_home(after):
        result.confirmed = True
        result.reason = "confirmed (dialog closed); to be verified by export"
        return
    # Something else popped up: back out without touching it.
    for _ in range(3):
        adb.shell(f"input keyevent {KEYCODE_BACK}", serial=serial)
        time.sleep(1.5)
        img = adb.screencap(serial)
        if panel_open(img) or is_home(img):
            break
    result.reason = ("an unexpected popup followed Confirm (e.g. a gem offer); dismissed "
                     "with BACK, nothing tapped in it; see the after_confirm screenshot")


# --- find, then dry run or confirm ------------------------------------------------

def dry_run(name, from_level, serial=adb.SERIAL, log=print, shot_dir=SHOT_DIR):
    """Find the upgrade `name` from_level -> from_level + 1 in the Builder menu,
    open its dialog, record it, close it. Never confirms. Returns DryRun."""
    with capture_lock(wait=30):
        return _run(name, from_level, serial, log, shot_dir, confirm=False)


def upgrade(name, from_level, serial=adb.SERIAL, log=print, shot_dir=SHOT_DIR):
    """Like dry_run, but confirms the upgrade when every check passes and a
    builder is free. result.confirmed = the dialog closed after Confirm."""
    with capture_lock(wait=30):
        return _run(name, from_level, serial, log, shot_dir, confirm=True)


def _run(name, from_level, serial, log, shot_dir, confirm):
    result = DryRun(name, from_level)
    want = from_level + 1
    os.makedirs(shot_dir, exist_ok=True)
    if not ensure_home(serial, log):
        result.reason = "could not reach the home screen"
        return result
    if confirm:
        counter = read_builder_counter(adb.screencap(serial))
        if not counter or counter[0] < 1:
            # Confirm with no free builder opens a gem-priced Goblin Builder offer.
            result.reason = f"no free builder on the counter ({counter})"
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
                if any(normalize(text) == t and same_row(key, k) for t, k in tried):
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
                parsed = read_dialog(dimg)
                path = os.path.join(shot_dir, time.strftime("%Y%m%d_%H%M%S_") +
                                    re.sub(r"\W+", "_", name) + f"_{want}.png")
                cv2.imwrite(path, dimg)
                result.screenshots.append(path)
                result.rows_opened.append((text, title))
                log(f"  row {text!r} -> dialog {title!r}")
                if parsed and same_name(parsed[0], name) and parsed[1] == want:
                    result.found, result.title, result.level = True, title, parsed[1]
                    result.affordable = not price_in_red(dimg)
                    if confirm and result.affordable:
                        _confirm(result, name, want, serial, log, shot_dir)
                    else:
                        close_dialog(serial, log)
                        result.reason = (
                            "dialog matches the planned upgrade"
                            + ("" if confirm else " (dry run: not confirmed)")
                            + ("" if result.affordable else
                               "; the price is shown in red: not enough resources"))
                    return result
                close_dialog(serial, log)
                opened_one = True
                break                                # re-read rows after the dialog
            if opened_one:
                continue
            # Nothing (new) here: scroll from a gap between rows.
            # Diagonal drag that ends off the panel, so the finger is not on
            # the pressed row at release. Live 2026-11-16: drags from the gap
            # between rows opened a row's dialog in up to 1 of 2 drags, margin
            # drags in 1-4 of 10, diagonal drags in 1 of 10. A dialog opened
            # this way is closed below and can never be confirmed.
            x0, y0 = DRAG_X, 720
            # Re-check right before the drag: same list, same place, no dialog.
            now_img = adb.screencap(serial)
            if dialog_open(now_img) or np.abs(_list_strip(now_img) - _list_strip(img)).mean() >= 1.0:
                continue
            adb.swipe(x0, y0, DRAG_END_X, y0 - 380, 800, serial=serial)
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
