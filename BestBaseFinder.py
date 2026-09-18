"""
BestBaseFinder — scrapes the top-N Legend League players, reads each player's
"Battle Details" popup, and ranks them into Top-5 Defensive Bases / Top-5 Attackers.

Conventions in this file mirror Ranked_Copy.py / wall.py / Builder.py rather than
importing them directly: none of those scripts are import-safe libraries (wall.py's
own activate_game_window() carries the comment "mirrors Ranked_Copy.py pattern"
instead of importing it, and Builder.py opens a tkinter window at module import
time). The proven patterns are copied here instead.

REQUIRED TEMPLATE IMAGES that don't exist yet in misc/ and must be captured by
the user (same screenshot+crop workflow as every other misc/*.png asset; use
select_region.py / locationfinder.py to find coordinates if needed):
    misc/list_button.png           - list/leaderboard button clicked after Attack
                                      to open the ranking list
    misc/battle_details_close.png  - close button on the "Battle Details" popup
                                      (falls back to Close_Btn.png if not found)

No profile is opened per player — each row's "Defenses: X/X" text is directly
clickable and opens "Battle Details" as an overlay on top of the ranking list
(~2s to render; everything outside the popup's own footprint still shows the
list behind it). See read_ranking_page()'s click_x/click_y and visit_player().

The ranking list is read as 5 separate column regions (rank / name / attacks /
defenses / trophies) rather than one OCR blob, since each column has a very
different text shape (pure digits, free text, "N/N" pairs). Rows are then
correlated across columns by matching each column entry's absolute on-screen
y-center against the rank column's y-centers (see read_ranking_page()).
"""

from datetime import datetime
import time
import random
import logging
import re
import os
import glob
import json
import csv
import statistics
import threading
import traceback
import tkinter as tk
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pyautogui
import pygetwindow as gw
import pytesseract
import cv2
import numpy as np
from pynput import keyboard as pynput_keyboard

pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

logging.basicConfig(
    filename='coc_bot_detailed.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(funcName)s - %(message)s'
)

# =========================================================
# CONFIG
# =========================================================
CONFIG = {
    'retry_attempts': 3,
    'retry_delay': 2,
    'popup_wait_timeout': 20,          # seconds to wait for profile/battle-details popups
    'scroll_amount': 1200,              # reversed from wall.py's -600 — this list scrolls the opposite way, bumped up from 600
    'scroll_nudge': -150,              # small corrective scroll (opposite of scroll_amount) when a rank gap is detected
    'max_scroll_tries': 60,            # safety cap across the whole run
    'max_ocr_retries': 3,              # retries per OCR read before treating it as failed
    'row_cluster_tolerance_ratio': 0.6,  # name-column line clustering tolerance, as a ratio of median text height
    'row_tolerance_fallback_px': 30,    # cross-column row-matching tolerance when only one rank is visible
    'target_player_count': 10,         # TEMP: only collect the top 10 while we shake out issues
    'watchdog_stuck_seconds': 180,      # background watchdog: recover if no progress this long
    'debug_visual': True,             # True: flash click markers + OCR region boxes live on screen
    'weights_defense': {               # confirmed with user — mixes offense "stars" stat in on purpose
        'stars': 0.50,
        'defense_destruction': 0.35,
        'trophies': 0.10,
        'defenses_done': 0.05,
    },
    'weights_attack': {
        'stars': 0.35,
        'attack_destruction': 0.35,
        'duration': 0.20,
        'trophies': 0.10,
    },
}

# =========================================================
# REGIONS  (relative to LDPlayer window top-left; from select_region.py)
# =========================================================
RANKING_LIST_REL = (24, 139, 1152, 516)      # rough bounding box — used only to position the scroll click
BATTLE_DETAILS_REL = (24, 139, 1152, 516)    # "Battle Details" popup — same rect as the ranking list

# The ranking list is read column-by-column rather than as one blob, since each
# column has a distinct text shape (pure digits, free text, "N/N" pairs).
COLUMN_RANK_REL = (76, 142, 67, 511)
COLUMN_NAME_REL = (143, 144, 414, 506)
COLUMN_ATTACKS_REL = (561, 148, 186, 513)
COLUMN_DEFENSES_REL = (770, 148, 222, 491)
COLUMN_TROPHIES_REL = (1024, 148, 89, 501)

# =========================================================
# TEMPLATE IMAGES
# =========================================================
IMG_ATTACK_BUTTON = "misc/attack_button.png"             # existing asset
IMG_LIST_BUTTON = "misc/list_button.png"                 # NEW — user must capture (opens the ranking list after Attack)
IMG_BATTLE_DETAILS_CLOSE = "misc/battle_details_close.png"  # NEW — user must capture
IMG_CLOSE_BTN = "Close_Btn.png"                          # existing asset (root-relative, matches Ranked_Copy.py)
IMG_AFK_PROMPT = "misc/areyouthere.png"                  # existing asset
IMG_RELOAD_GAME = "misc/reloadgame.png"                  # existing asset

# =========================================================
# FILES
# =========================================================
PROGRESS_FILE = "bestbase_progress.json"
CSV_EXPORT_FILE = "legend_players.csv"
JSON_EXPORT_FILE = "legend_players.json"
ERROR_DIR = "errors"

# =========================================================
# GLOBAL STATE
# =========================================================
WINDOW_X, WINDOW_Y = 0, 0
_last_heartbeat = 0.0
_stop_requested = threading.Event()   # set by the ESC listener — ends collection early


@dataclass
class RowRecord:
    rank: int
    username: str
    attacks_done: str
    defenses_done: str
    trophies: int
    click_x: int
    click_y: int


# =========================================================
# WINDOW / CLICK PRIMITIVES  (copied pattern — Ranked_Copy.py / wall.py)
# =========================================================
def activate_game_window() -> bool:
    """Bring LDPlayer window to the foreground."""
    global WINDOW_X, WINDOW_Y
    logging.debug("Activating LDPlayer window")
    try:
        windows = gw.getWindowsWithTitle("LDPlayer")
        if not windows:
            logging.error("No LDPlayer window found")
            return False

        game_window = windows[0]
        game_window.activate()
        game_window.restore()
        WINDOW_X, WINDOW_Y = game_window.left, game_window.top
        logging.info(f"LDPlayer activated at ({WINDOW_X}, {WINDOW_Y})")
        return True
    except Exception:
        logging.error(f"Failed to activate LDPlayer: {traceback.format_exc()}")
        return False


def get_absolute_region(rel_offset: Tuple[int, int, int, int], label: str = None) -> Optional[Tuple[int, int, int, int]]:
    """Convert a (x, y, w, h) offset relative to the LDPlayer window into absolute screen coords."""
    if not activate_game_window():
        return None
    x, y, w, h = rel_offset
    region_abs = (WINDOW_X + x, WINDOW_Y + y, w, h)
    if label:
        highlight_regions({label: region_abs})
    return region_abs


def locate_image(image_path: str, confidence: float = 0.8, retries: int = CONFIG['retry_attempts']):
    """Locate an image on screen with retries."""
    logging.debug(f"Looking for '{image_path}' with confidence={confidence}")
    for attempt in range(retries):
        try:
            pos = pyautogui.locateCenterOnScreen(image_path, confidence=confidence)
            if pos:
                logging.info(f"Found '{image_path}' at {pos} (attempt {attempt + 1})")
                return pos
        except Exception:
            logging.error(f"Error locating '{image_path}': {traceback.format_exc()}")
        time.sleep(CONFIG['retry_delay'])
    logging.warning(f"'{image_path}' not found after {retries} attempts")
    return None


# =========================================================
# DEBUG VISUALIZATION  (adapted from debug_new.py's highlight_regions_tk / XP.py's highlight_click)
# =========================================================
def highlight_click(x, y, duration=0.15):
    """Flash a small red marker at (x, y) on screen. No-op unless CONFIG['debug_visual'] is on."""
    if not CONFIG['debug_visual']:
        return

    def run():
        size = 20
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.6)
        root.geometry(f"{size}x{size}+{x - size // 2}+{y - size // 2}")
        root.configure(bg="red")
        root.after(int(duration * 1000), root.destroy)
        root.mainloop()

    threading.Thread(target=run, daemon=True).start()


def highlight_regions(regions: Dict[str, Tuple[int, int, int, int]], duration=1.2):
    """Flash green outlined boxes + labels around one or more absolute screen regions."""
    if not CONFIG['debug_visual'] or not regions:
        return

    def run():
        root = tk.Tk()
        root.attributes("-fullscreen", True)
        root.attributes("-alpha", 0.25)
        root.attributes("-topmost", True)
        root.configure(bg="black")

        canvas = tk.Canvas(root, bg="black", highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        for label, (x, y, w, h) in regions.items():
            canvas.create_rectangle(x, y, x + w, y + h, outline="green", width=4)
            canvas.create_text(x + 5, y - 10, text=label, fill="green",
                                anchor="nw", font=("Arial", 12, "bold"))

        root.after(int(duration * 1000), root.destroy)
        root.mainloop()

    threading.Thread(target=run, daemon=True).start()


def show_result_overlay(record: dict, near_x: int, near_y: int, duration: float = 2.5):
    """Pop up the scraped stats for one player next to their row, so results are
    visible on the LDPlayer screen itself instead of only in the console/log."""
    if not CONFIG['debug_visual']:
        return

    text = (
        f"#{record['rank']} {record['username']}\n"
        f"Attacks {record['attacks_done']}   Defenses {record['defenses_done']}   Trophies {record['trophies']}\n"
        f"Stars {record.get('avg_stars_attack')}   AtkDestr {record.get('avg_attack_destruction')}%   "
        f"DefDestr {record.get('avg_defense_destruction')}%   Duration {record.get('avg_attack_duration')}"
    )

    def run():
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        tk.Label(root, text=text, fg="lime", bg="black", font=("Consolas", 10, "bold"),
                 justify="left", padx=8, pady=6).pack()
        root.update_idletasks()
        root.geometry(f"+{near_x + 30}+{max(near_y - 20, 0)}")
        root.after(int(duration * 1000), root.destroy)
        root.mainloop()

    threading.Thread(target=run, daemon=True).start()


# =========================================================
# CLICK / TIMING HELPERS
# =========================================================
def click_at(x, y):
    """Click at an absolute screen position, flashing a debug marker first if enabled."""
    highlight_click(x, y)
    pyautogui.click(x, y)


def wait(min_s, max_s=None):
    """Pause execution; pass a single value for a fixed delay or two for a random range."""
    time.sleep(random.uniform(min_s, max_s) if max_s is not None else min_s)


# =========================================================
# ERROR RECOVERY / WATCHDOG  (adapted from Ranked_Copy._bot_monitor + wall._escape_stuck_screen)
# =========================================================
def _update_heartbeat():
    global _last_heartbeat
    _last_heartbeat = time.time()


def _log_error_screenshot(rank: int, reason: str):
    """Screenshot-log an error frame, per the task's error-recovery requirement."""
    os.makedirs(ERROR_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(ERROR_DIR, f"error_rank{rank}_{reason}_{ts}.png")
    try:
        pyautogui.screenshot().save(path)
        logging.error(f"Error screenshot saved: {path} (rank={rank}, reason={reason})")
    except Exception:
        logging.error(f"Failed to save error screenshot: {traceback.format_exc()}")


def _start_escape_listener() -> pynput_keyboard.Listener:
    """Global ESC key hook: sets _stop_requested so collect_players() ends early and
    whatever data has been gathered so far still gets exported/scored."""
    def on_press(key):
        if key == pynput_keyboard.Key.esc:
            logging.info("[ESC] Stop requested by user")
            print("\n[ESC] Stopping collection — evaluating data collected so far...")
            _stop_requested.set()
            return False  # stop listening, one shot is enough

    listener = pynput_keyboard.Listener(on_press=on_press)
    listener.daemon = True
    listener.start()
    return listener


def _escape_stuck_screen():
    """Click Close_Btn.png a few times to recover to a known screen."""
    logging.warning("[WATCHDOG] Attempting recovery via Close_Btn.png")
    for _ in range(3):
        try:
            pos = pyautogui.locateCenterOnScreen(IMG_CLOSE_BTN, confidence=0.8)
        except Exception:
            pos = None
        if not pos:
            break
        pyautogui.click(pos)
        time.sleep(0.5)


def _afk_monitor(stop_event: threading.Event):
    """Background thread: handles the 'Are you there?' AFK prompt and stuck-screen recovery."""
    _update_heartbeat()
    while not stop_event.wait(5):
        try:
            pos = pyautogui.locateCenterOnScreen(IMG_AFK_PROMPT, confidence=0.8)
            if pos:
                logging.warning("[MONITOR] 'Are you there?' detected — reloading")
                reload_pos = None
                try:
                    reload_pos = pyautogui.locateCenterOnScreen(IMG_RELOAD_GAME, confidence=0.8)
                except Exception:
                    pass
                pyautogui.click(reload_pos if reload_pos else pos)
                time.sleep(15)
                activate_game_window()
                _update_heartbeat()
        except Exception:
            pass  # image not found / screenshot error — normal

        if _last_heartbeat > 0 and (time.time() - _last_heartbeat) > CONFIG['watchdog_stuck_seconds']:
            logging.warning("[MONITOR] No progress for too long — attempting recovery")
            _escape_stuck_screen()
            _update_heartbeat()


# =========================================================
# OCR PRIMITIVES
# =========================================================
def _preprocess_list_image(img_arr, scale=3):
    """Grayscale + upscale + OTSU threshold. Same recipe as wall._preprocess_list_image."""
    gray = cv2.cvtColor(img_arr, cv2.COLOR_RGB2GRAY)
    up = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    _, thresh = cv2.threshold(up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return thresh, scale


def _preprocess_hsv_text(img_arr, scale=3, v_thresh=180, s_thresh=18):
    """
    Isolate white-filled game-UI text (rank/attacks/defenses/trophies render as a
    white glyph with a dark outline on a beige card). Plain grayscale+OTSU fails on
    this: the beige background is bright enough to threshold as "white" alongside
    the actual text fill, leaving only the dark outline — an unreadable hollow shape.
    HSV lets us split on saturation instead: beige has S~20-30, true white fill has
    S~0-10, so a tight S cutoff keeps the glyph fill and drops the background.
    """
    up = cv2.resize(img_arr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(up, cv2.COLOR_RGB2HSV)
    _, s, v = cv2.split(hsv)
    mask = cv2.bitwise_and(
        cv2.threshold(v, v_thresh, 255, cv2.THRESH_BINARY)[1],
        cv2.threshold(s, s_thresh, 255, cv2.THRESH_BINARY_INV)[1],
    )
    return cv2.bitwise_not(mask), scale


# The RANK column's narrow "1" glyph sits close enough to a following 5/7 that
# Tesseract sometimes classifies the pair as letters instead of digits (e.g. "15"
# read as "iS", "17" read as "7" with the leading 1 dropped). Rather than force a
# digit whitelist (which just mis-maps the wrong glyph onto some digit instead of
# rejecting it), OCR normally and correct the specific confusions afterward.
_DIGIT_CONFUSION = str.maketrans({
    'i': '1', 'I': '1', 'l': '1', '|': '1',
    'S': '5', 's': '5',
    'O': '0', 'o': '0',
    'B': '8', 'G': '6',
})


def _fix_digit_confusion(text: str) -> str:
    return text.translate(_DIGIT_CONFUSION)


def _word_boxes(processed_img, psm=6, whitelist=None):
    """Run pytesseract.image_to_data and return a flat list of non-empty, confident word boxes."""
    config = f"--oem 3 --psm {psm}"
    if whitelist:
        config += f" -c tessedit_char_whitelist={whitelist}"
    data = pytesseract.image_to_data(processed_img, config=config, output_type=pytesseract.Output.DICT)
    words = []
    for i in range(len(data['text'])):
        text = data['text'][i].strip()
        try:
            conf = int(float(data['conf'][i]))
        except (ValueError, TypeError):
            conf = -1
        if text and conf >= 0:
            words.append({
                'text': text, 'left': data['left'][i], 'top': data['top'][i],
                'width': data['width'][i], 'height': data['height'][i], 'conf': conf,
            })
    return words


def _read_column_entries(rel_offset: Tuple[int, int, int, int], label: str,
                          whitelist: str = None, psm: int = 6, preprocess_fn=_preprocess_list_image) -> List[dict]:
    """
    Screenshot one column region, OCR it, and return word boxes annotated with their
    ABSOLUTE on-screen center (x_center_abs, y_center_abs) — this is what lets rows
    from different columns be matched up later purely by vertical position.
    """
    region_abs = get_absolute_region(rel_offset, label=label)
    if not region_abs:
        return []

    img_pil = pyautogui.screenshot(region=region_abs)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    img_pil.save(f"debug_col_{label.lower()}_{ts}.png")

    processed, scale = preprocess_fn(np.array(img_pil))
    words = _word_boxes(processed, psm=psm, whitelist=whitelist)
    for w in words:
        w['x_center_abs'] = region_abs[0] + int((w['left'] + w['width'] / 2) / scale)
        w['y_center_abs'] = region_abs[1] + (w['top'] + w['height'] / 2) / scale
    return words


def _cluster_rows(words: List[dict], tolerance_ratio: float) -> List[List[dict]]:
    """Group word boxes into text rows based on vertical (top) proximity."""
    if not words:
        return []
    heights = [w['height'] for w in words]
    median_h = statistics.median(heights)
    tolerance = max(median_h * tolerance_ratio, 5)

    rows: List[List[dict]] = []
    current: List[dict] = []
    current_top = None
    for w in sorted(words, key=lambda w: w['top']):
        if current_top is None or abs(w['top'] - current_top) <= tolerance:
            current.append(w)
            current_top = current[0]['top']
        else:
            rows.append(current)
            current = [w]
            current_top = w['top']
    if current:
        rows.append(current)
    return rows


def clean_username(name: str) -> str:
    name = name.strip(" '\"|_-")
    return re.sub(r'\s+', ' ', name).strip()


def _lines_from_entries(entries: List[dict], tolerance_ratio: float) -> List[dict]:
    """Merge word boxes from one column into text lines (handles multi-word names)."""
    lines = []
    for cluster in _cluster_rows(entries, tolerance_ratio):
        words_sorted = sorted(cluster, key=lambda w: w['left'])
        lines.append({
            'text': " ".join(w['text'] for w in words_sorted),
            'x_center_abs': sum(w['x_center_abs'] for w in cluster) / len(cluster),
            'y_center_abs': sum(w['y_center_abs'] for w in cluster) / len(cluster),
        })
    return lines


def _nearest_entry(entries: List[dict], target_y: float, tolerance: float, prefer_topmost: bool = False) -> Optional[dict]:
    """
    Find the column entry whose y_center_abs best matches target_y (within tolerance).
    prefer_topmost=True picks the highest (smallest y) match instead of the closest —
    used for the name column, where a username line sits above its clan-name line and
    both can fall inside the same row band.
    """
    candidates = [e for e in entries if abs(e['y_center_abs'] - target_y) <= tolerance]
    if not candidates:
        return None
    if prefer_topmost:
        return min(candidates, key=lambda e: e['y_center_abs'])
    return min(candidates, key=lambda e: abs(e['y_center_abs'] - target_y))


def _assemble_rows(rank_entries: List[dict], name_entries: List[dict], attacks_entries: List[dict],
                    defenses_entries: List[dict], trophies_entries: List[dict]) -> List[RowRecord]:
    """
    Pure row-correlation logic: given already-OCR'd column entries, match each rank's
    absolute y-center against the other columns' y-centers to reassemble full rows.
    Separated from read_ranking_page() so it can be unit-tested without a live screen.
    """
    name_lines = _lines_from_entries(name_entries, CONFIG['row_cluster_tolerance_ratio'])

    rank_ys = sorted(e['y_center_abs'] for e in rank_entries if e['text'].isdigit())
    gaps = [b - a for a, b in zip(rank_ys, rank_ys[1:]) if b - a > 5]
    row_tolerance = (statistics.median(gaps) / 2) if gaps else CONFIG['row_tolerance_fallback_px']

    records = []
    for e in rank_entries:
        if not e['text'].isdigit():
            continue
        rank = int(e['text'])
        target_y = e['y_center_abs']

        name_line = _nearest_entry(name_lines, target_y, row_tolerance, prefer_topmost=True)
        att_entry = _nearest_entry(attacks_entries, target_y, row_tolerance)
        def_entry = _nearest_entry(defenses_entries, target_y, row_tolerance)
        trophy_entry = _nearest_entry(trophies_entries, target_y, row_tolerance)

        if not (name_line and att_entry and def_entry and trophy_entry):
            logging.debug(
                f"Rank {rank}: incomplete row (name={bool(name_line)}, attacks={bool(att_entry)}, "
                f"defenses={bool(def_entry)}, trophies={bool(trophy_entry)})"
            )
            continue

        m_att = re.search(r'(\d+)\s*/\s*(\d+)', att_entry['text'])
        m_def = re.search(r'(\d+)\s*/\s*(\d+)', def_entry['text'])
        trophy_digits = re.findall(r'\d+', trophy_entry['text'])
        username = clean_username(name_line['text'])
        if not (m_att and m_def and trophy_digits and username):
            continue

        records.append(RowRecord(
            rank=rank,
            username=username,
            attacks_done=f"{m_att.group(1)}/{m_att.group(2)}",
            defenses_done=f"{m_def.group(1)}/{m_def.group(2)}",
            trophies=int(trophy_digits[-1]),
            click_x=def_entry['x_center_abs'],  # "Defenses: X/X" is directly clickable and opens Battle Details
            click_y=int(target_y),
        ))
    return records


def read_ranking_page(max_retries: int = None) -> List[RowRecord]:
    """
    Read the ranking list as 5 independent column OCR passes (rank / name / attacks /
    defenses / trophies), then correlate rows across columns by absolute y-position —
    each rank's y-center is the anchor other columns are matched against.
    """
    max_retries = max_retries or CONFIG['max_ocr_retries']

    for attempt in range(max_retries):
        # RANK: no whitelist (whitelisting forces mis-mapped letters onto the wrong
        # digit instead of rejecting them) — OCR freely at psm 11 (sparse text, one
        # number per badge) then fix known letter/digit confusions afterward.
        rank_entries = _read_column_entries(
            COLUMN_RANK_REL, "RANK", psm=11,
            preprocess_fn=lambda arr: _preprocess_hsv_text(arr, scale=6),
        )
        for e in rank_entries:
            e['text'] = _fix_digit_confusion(e['text'])
        if not rank_entries:
            logging.warning(f"No rank digits read (attempt {attempt + 1}/{max_retries}) — retrying OCR")
            time.sleep(1)
            continue

        # NAME: usernames render in the same hollow white-fill font as rank/attacks,
        # so plain grayscale+OTSU misses them entirely (only the plain-font clan-name
        # line survives) — same HSV fix applies. The clan-name line isn't white-filled
        # so the HSV mask naturally drops it, which is what we want anyway.
        name_entries = _read_column_entries(COLUMN_NAME_REL, "NAME", preprocess_fn=_preprocess_hsv_text)
        attacks_entries = _read_column_entries(COLUMN_ATTACKS_REL, "ATTACKS", whitelist="0123456789/",
                                                preprocess_fn=_preprocess_hsv_text)
        defenses_entries = _read_column_entries(COLUMN_DEFENSES_REL, "DEFENSES", whitelist="0123456789/",
                                                 preprocess_fn=_preprocess_hsv_text)
        trophies_entries = _read_column_entries(COLUMN_TROPHIES_REL, "TROPHIES", whitelist="0123456789",
                                                 preprocess_fn=_preprocess_hsv_text)

        records = _assemble_rows(rank_entries, name_entries, attacks_entries, defenses_entries, trophies_entries)
        if records:
            return records
        logging.warning(f"No complete rows assembled (attempt {attempt + 1}/{max_retries}) — retrying OCR")
        time.sleep(1)
    return []


def _last_float(line: str) -> Optional[float]:
    nums = re.findall(r'\d+\.\d+|\d+', line)
    return float(nums[-1]) if nums else None


def _last_percent(line: str) -> Optional[int]:
    nums = re.findall(r'\d+\.?\d*', line.replace('%', ''))
    return round(float(nums[-1])) if nums else None


def _parse_duration(line: str) -> Optional[str]:
    m = re.search(r'(\d+)\s*m\D{0,3}(\d+)\s*s', line, re.IGNORECASE)
    if m:
        return f"{int(m.group(1))}m {int(m.group(2))}s"
    return None


def read_battle_details(region_abs: Tuple[int, int, int, int], expected_username: str,
                         max_retries: int = None) -> Optional[dict]:
    """
    OCR the 'Battle Details' popup for the 4 required stats, tolerant of OCR noise:
    matches lines by keyword containment rather than exact text.
    """
    max_retries = max_retries or CONFIG['max_ocr_retries']
    for attempt in range(max_retries):
        img_pil = pyautogui.screenshot(region=region_abs)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        img_pil.save(f"debug_battledetails_{ts}.png")

        processed, _ = _preprocess_list_image(np.array(img_pil))
        raw = pytesseract.image_to_string(processed, config="--oem 3 --psm 6")

        result = {}
        for line in (l.strip() for l in raw.splitlines() if l.strip()):
            low = line.lower()
            if 'avg_stars_attack' not in result and 'star' in low:
                val = _last_float(line)
                if val is not None:
                    result['avg_stars_attack'] = val
            elif 'avg_attack_destruction' not in result and 'destruction' in low and 'attack' in low:
                val = _last_percent(line)
                if val is not None:
                    result['avg_attack_destruction'] = val
            elif 'avg_defense_destruction' not in result and 'destruction' in low and 'defen' in low:
                val = _last_percent(line)
                if val is not None:
                    result['avg_defense_destruction'] = val
            elif 'avg_attack_duration' not in result and 'duration' in low:
                dur = _parse_duration(line)
                if dur:
                    result['avg_attack_duration'] = dur

        if len(result) == 4:
            return result

        logging.warning(
            f"Battle details incomplete for '{expected_username}' "
            f"(attempt {attempt + 1}/{max_retries}): got {list(result.keys())}"
        )
        time.sleep(1)
    return None


# =========================================================
# NAVIGATION
# =========================================================
def open_legend_ranking() -> bool:
    """Click Attack, then the list button, then the Legend League tab to open the ranking list."""
    if not activate_game_window():
        return False

    attack_btn = locate_image(IMG_ATTACK_BUTTON)
    if not attack_btn:
        logging.error("Attack button not found — cannot open Legend League ranking")
        return False
    click_at(*attack_btn)
    wait(1, 2)

    list_btn = locate_image(IMG_LIST_BUTTON)
    if not list_btn:
        logging.error("List button not found — cannot open Legend League ranking")
        return False
    click_at(*list_btn)
    wait(1, 2)
    return True


def scroll_ranking_list(amount: int):
    region_abs = get_absolute_region(RANKING_LIST_REL)
    if not region_abs:
        return
    cx = region_abs[0] + region_abs[2] // 2
    cy = region_abs[1] + region_abs[3] // 2
    pyautogui.moveTo(cx, cy)
    pyautogui.scroll(amount)
    wait(0.6, 1.2)


def visit_player(row: RowRecord) -> Optional[dict]:
    """Click a row's 'Defenses: X/X' text — it's directly clickable and opens the
    'Battle Details' popup as an overlay on top of the list (~2s to render, and
    everything outside the popup's own area still looks like the list behind it).
    Read it, then close it."""
    logging.info(f"Visiting rank {row.rank} ({row.username})")
    try:
        click_at(row.click_x, row.click_y)
        wait(2.0, 2.5)  # popup takes ~2s to render before it's readable

        region_abs = get_absolute_region(BATTLE_DETAILS_REL, label="BATTLE_DETAILS")
        details = read_battle_details(region_abs, row.username)
        if details is None:
            logging.error(f"Rank {row.rank}: Battle Details OCR failed")
            _log_error_screenshot(row.rank, "battle_details_ocr_failed")
            _escape_stuck_screen()
            return None

        close_btn = locate_image(IMG_BATTLE_DETAILS_CLOSE, retries=2) or locate_image(IMG_CLOSE_BTN, retries=2)
        if close_btn:
            click_at(*close_btn)
        else:
            logging.warning(f"Rank {row.rank}: no close button found for Battle Details popup")
            _escape_stuck_screen()
        wait(0.5, 1.0)

        _update_heartbeat()
        record = {
            "rank": row.rank,
            "username": row.username,
            "trophies": row.trophies,
            "attacks_done": row.attacks_done,
            "defenses_done": row.defenses_done,
            **details,
        }
        show_result_overlay(record, row.click_x, row.click_y)
        return record
    except Exception:
        logging.error(f"Rank {row.rank}: exception in visit_player: {traceback.format_exc()}")
        _log_error_screenshot(row.rank, "visit_player_exception")
        _escape_stuck_screen()
        return None


# =========================================================
# PROGRESS / RESUME  (dedup source of truth — never re-visit a known rank)
# =========================================================
def load_progress() -> Dict[int, dict]:
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return {int(k): v for k, v in data.items()}
        except Exception:
            logging.error(f"Failed to load progress file: {traceback.format_exc()}")
    return {}


def save_progress(players: Dict[int, dict]):
    try:
        with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump({str(k): v for k, v in players.items()}, f, indent=2)
    except Exception:
        logging.error(f"Failed to save progress: {traceback.format_exc()}")


# =========================================================
# COLLECTION ORCHESTRATOR
# =========================================================
def collect_players(target_count: int = 100) -> Dict[int, dict]:
    players = load_progress()
    if players:
        logging.info(f"Resumed progress: {len(players)} player(s) already collected")
        print(f"Resumed progress: {len(players)}/{target_count} player(s) already collected")

    last_max_rank = max(players.keys()) if players else 0
    consecutive_no_new = 0
    scroll_tries = 0

    # len(players) is the authoritative stop condition — robust even if a rank
    # inside the target range permanently fails to visit and last_max_rank stalls.
    while (len(players) < target_count and scroll_tries < CONFIG['max_scroll_tries']
           and not _stop_requested.is_set()):
        if not activate_game_window():
            logging.error("LDPlayer window lost — stopping collection")
            break

        rows = read_ranking_page()
        if not rows:
            logging.warning("Ranking page unreadable after retries — nudging scroll and retrying")
            scroll_tries += 1
            scroll_ranking_list(CONFIG['scroll_nudge'])
            continue

        visible_ranks = sorted(r.rank for r in rows)
        min_visible = visible_ranks[0]
        if last_max_rank > 0 and min_visible > last_max_rank + 1:
            logging.warning(
                f"Rank gap detected (last processed max={last_max_rank}, now seeing {min_visible}) "
                f"— nudging scroll up to recover"
            )
            scroll_ranking_list(CONFIG['scroll_nudge'])
            scroll_tries += 1
            continue

        new_count = 0
        for row in rows:
            if len(players) >= target_count or _stop_requested.is_set():
                break
            if row.rank in players or row.rank > target_count:
                continue
            record = visit_player(row)
            if record:
                players[row.rank] = record
                save_progress(players)
                last_max_rank = max(last_max_rank, row.rank)
                new_count += 1
                print(
                    f"[{len(players)}/{target_count}] Rank {row.rank}: {record['username']}  "
                    f"stars={record.get('avg_stars_attack')}  atk%={record.get('avg_attack_destruction')}  "
                    f"def%={record.get('avg_defense_destruction')}  duration={record.get('avg_attack_duration')}"
                )
            else:
                logging.warning(f"Skipping rank {row.rank} ({row.username}) after failed visit")

        if _stop_requested.is_set():
            logging.info(f"Stopped early by user (ESC) — {len(players)} player(s) collected")
            break

        if len(players) >= target_count:
            logging.info(f"Reached target of {target_count} player(s) — stopping collection")
            break

        consecutive_no_new = 0 if new_count else consecutive_no_new + 1
        if consecutive_no_new >= 2:
            logging.info("No new ranks after two consecutive scrolls — assuming end of list")
            break

        scroll_ranking_list(CONFIG['scroll_amount'])
        scroll_tries += 1

    save_progress(players)
    logging.info(f"Collection finished: {len(players)} player(s) recorded")
    return players


# =========================================================
# EXPORT
# =========================================================
CSV_FIELDS = [
    "rank", "username", "trophies", "attacks_done", "defenses_done",
    "avg_stars_attack", "avg_attack_destruction", "avg_defense_destruction", "avg_attack_duration",
]


def export_csv(players: Dict[int, dict], path: str = CSV_EXPORT_FILE):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for rank in sorted(players):
            writer.writerow({k: players[rank].get(k, "") for k in CSV_FIELDS})
    logging.info(f"Exported CSV: {path}")


def export_json(players: Dict[int, dict], top_defense: List[dict], top_attackers: List[dict],
                 path: str = JSON_EXPORT_FILE):
    payload = {
        "players": [players[r] for r in sorted(players)],
        "top_defense": top_defense,
        "top_attackers": top_attackers,
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)
    logging.info(f"Exported JSON: {path}")


# =========================================================
# ANALYSIS / SCORING
# =========================================================
def _normalize(values: List[float]) -> List[float]:
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def _defenses_completed(p: dict) -> int:
    try:
        return int(str(p['defenses_done']).split('/')[0])
    except (KeyError, ValueError, IndexError):
        return 0


def _duration_seconds(p: dict) -> Optional[int]:
    m = re.search(r'(\d+)m\s*(\d+)s', str(p.get('avg_attack_duration', '')))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def score_defense(players: Dict[int, dict]) -> Dict[int, float]:
    """
    Weighted defense score, per user-confirmed weighting:
      50% lower Average Stars Per Attack, 35% lower Average Destruction Per Defense,
      10% higher Trophy Count, 5% higher Defenses Completed.
    """
    w = CONFIG['weights_defense']
    ranks = list(players.keys())
    stars_n = _normalize([players[r]['avg_stars_attack'] for r in ranks])
    destr_n = _normalize([players[r]['avg_defense_destruction'] for r in ranks])
    trophies_n = _normalize([players[r]['trophies'] for r in ranks])
    defenses_n = _normalize([_defenses_completed(players[r]) for r in ranks])

    return {
        r: (
            w['stars'] * (1 - stars_n[i]) +
            w['defense_destruction'] * (1 - destr_n[i]) +
            w['trophies'] * trophies_n[i] +
            w['defenses_done'] * defenses_n[i]
        )
        for i, r in enumerate(ranks)
    }


def score_attack(players: Dict[int, dict]) -> Dict[int, float]:
    """Weighted attacker score: higher stars/destruction, faster duration, higher trophies."""
    w = CONFIG['weights_attack']
    ranks = list(players.keys())
    stars_n = _normalize([players[r]['avg_stars_attack'] for r in ranks])
    destr_n = _normalize([players[r]['avg_attack_destruction'] for r in ranks])
    durations_n = _normalize([_duration_seconds(players[r]) or 0 for r in ranks])
    trophies_n = _normalize([players[r]['trophies'] for r in ranks])

    return {
        r: (
            w['stars'] * stars_n[i] +
            w['attack_destruction'] * destr_n[i] +
            w['duration'] * (1 - durations_n[i]) +
            w['trophies'] * trophies_n[i]
        )
        for i, r in enumerate(ranks)
    }


def top_n(players: Dict[int, dict], scores: Dict[int, float], n: int = 5) -> List[dict]:
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return [{"score": round(s, 4), **players[r]} for r, s in ranked]


def print_leaderboard(title: str, entries: List[dict]):
    print(f"\n=== {title} ===")
    for i, e in enumerate(entries, 1):
        print(
            f"{i}. #{e['rank']} {e['username']}  score={e['score']}  "
            f"stars={e.get('avg_stars_attack')}  atk_destr={e.get('avg_attack_destruction')}%  "
            f"def_destr={e.get('avg_defense_destruction')}%  trophies={e.get('trophies')}  "
            f"duration={e.get('avg_attack_duration')}"
        )


# =========================================================
# SETUP CHECK
# =========================================================
def _cleanup_debug_images():
    """Delete debug/OCR screenshots left over from a previous run (mirrors Ranked_Copy.cleanup_session())."""
    patterns = ['debug_col_*.png', 'debug_battledetails_*.png', 'debug_ranking_*.png']
    deleted = 0
    for pattern in patterns:
        for f in glob.glob(pattern):
            try:
                os.remove(f)
                deleted += 1
            except Exception:
                pass
    logging.info(f"Startup cleanup: removed {deleted} old debug/OCR image(s)")
    print(f"Cleaned up {deleted} old debug/OCR image(s) from previous runs")


def _check_required_assets():
    required = {
        IMG_ATTACK_BUTTON: "Attack button (should already exist from other bots)",
        IMG_LIST_BUTTON: "List/leaderboard button clicked after Attack to open the ranking list — capture via screenshot+crop",
        IMG_BATTLE_DETAILS_CLOSE: "Close button on the 'Battle Details' popup — falls back to Close_Btn.png",
    }
    missing = [path for path in required if not os.path.exists(path)]
    for path in missing:
        logging.warning(f"[SETUP] Missing template image: {path} — {required[path]}")
    if missing:
        print("WARNING: missing template image(s) — see coc_bot_detailed.log for details:")
        for path in missing:
            print(f"  - {path}: {required[path]}")


# =========================================================
# MAIN
# =========================================================
def main():
    logging.info("=" * 60)
    logging.info("BestBaseFinder — Legend League scrape starting")
    logging.info("=" * 60)

    _stop_requested.clear()
    _cleanup_debug_images()
    _check_required_assets()

    if not activate_game_window():
        print("LDPlayer window not found. Start LDPlayer with Clash of Clans open on the "
              "Attack screen, then re-run this script.")
        return

    print("Press ESC at any time to stop early and evaluate the data collected so far.")
    _start_escape_listener()

    stop_event = threading.Event()
    monitor = threading.Thread(target=_afk_monitor, args=(stop_event,), daemon=True, name="BestBaseMonitor")
    monitor.start()

    try:
        players = load_progress()
        if len(players) < CONFIG['target_player_count']:
            if not open_legend_ranking():
                print("Failed to open the Legend League ranking list. Check misc/attack_button.png "
                      "and misc/list_button.png.")
                return
            players = collect_players(CONFIG['target_player_count'])
    finally:
        stop_event.set()

    if not players:
        print("No player data collected.")
        return

    export_csv(players)
    top_defense = top_n(players, score_defense(players), 5)
    top_attackers = top_n(players, score_attack(players), 5)
    export_json(players, top_defense, top_attackers)

    print(f"\nCollected {len(players)} player(s). Exported {CSV_EXPORT_FILE} / {JSON_EXPORT_FILE}.")
    print_leaderboard("TOP 5 DEFENSIVE BASES", top_defense)
    print_leaderboard("TOP 5 ATTACKERS", top_attackers)

    logging.info("BestBaseFinder run complete")


if __name__ == "__main__":
    main()
