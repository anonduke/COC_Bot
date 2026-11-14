from datetime import datetime
from random import randint
import time
import math
import logging
import pyautogui
import pygetwindow as gw
from typing import Dict
import traceback
import cv2
import pytesseract
import numpy as np
import os
import glob
import threading

# Specify the path to the Tesseract executable (Windows-specific)
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Wall upgrade module (optional — bot still works if wall.py is missing)
try:
    import wall as _wall
    _wall.DEBUG = 0          # silence wall.py print output when called from here
    WALL_UPGRADE_AVAILABLE = True
except Exception:
    WALL_UPGRADE_AVAILABLE = False
    logging.warning("wall.py not found — wall upgrade feature disabled")

# Enhanced Logging Configuration
logging.basicConfig(
    filename='coc_bot_detailed.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(funcName)s - %(message)s'
)
# Global variables
timestamps: Dict[str, datetime] = {
    'start': None,
    'collectResources': None,
    '_lastInteraction': None
}
# Configuration dictionary
CONFIG = {
    'goblin_attack': 0,
    'min_gold_to_attack': 0,
    'max_attack_attempts': 20,
    'battle_wait_timeout': 30,
    'gold_region': (60, 116, 125, 34),  # Relative to WINDOW_X, WINDOW_Y — was (60,125,125,25), which cut the digit tops ('883 485' -> '634')
    'retry_attempts': 3,
    'retry_delay': 2,
    'ranked_mode': False,       # Toggle for ranked battle mode
    'wall_upgrade': False,      # Toggle wall upgrade between attack runs
    'wall_check_every': 5,      # Run wall upgrade check every N successful attacks
    'event_troops': False,      # Toggle deployment of the current event troop
    'event_troop_count': 10,    # Number of event troops to deploy (varies per event)
    'builder_base': False,      # Toggle Builder Base attack mode (different flow/troops)
    'builder_cart_check_every': 5,  # Check the Builder Base elixir cart every N successful attacks
    'ai_deploy': False,         # Toggle AI-aimed spell drops (heat-map + lead-ahead targeting)
}

# Global variables
WINDOW_X, WINDOW_Y = 0, 0
gold_region_files = []
attack_count = 0          # counts successful attacks for wall upgrade trigger
walls_this_session = 0   # total walls upgraded in current bot session
_last_heartbeat = 0.0    # epoch time of last bot activity (used by monitor)
_monitor_stop_evt = None # threading.Event set when bot stops
# **Troop & Spell Deployment Coordinates**

goblins_positions = [
    (509, 305),(242, 327),(263, 320),(283, 308),(308, 293),(328, 277),(346, 260),(362, 249),(385, 235),(407, 217),(416, 206),
    (435, 192),(454, 177),(479, 160),(497, 143),(516, 129),(537, 110),(551, 98),(567, 86),(594, 67),(605, 58),(642, 48),(667, 46),(692, 51),(717, 57),(740, 62),(763, 74),(784, 90),(803, 105),(825, 126),(846, 144),(866, 152),
    (880, 159),(901, 168),(912, 176),(934, 189),(945, 201),(965, 218),(988, 235),(998, 242),(1023, 263),(1050, 283),(1062, 290),(1092, 318),
    (1108, 330),(1130, 358),(1131, 368),(1129, 401),(1120, 416),(1101, 433),(1084, 448),(1063, 473),(1050, 488),(1033, 501),(986, 521),
    (979, 526),(971, 540),(956, 552),(938, 565),(927, 571),(908, 578),(508, 577),(483, 573),(452, 558),(430, 541),(410, 518),(390, 506),
    (375, 497),(362, 489),(353, 484),(339, 470),(320, 462),(298, 445),(279, 422),(277, 410),(256, 395),(238, 374),(230, 337),(234, 328),(244, 320),(263, 311)
]
wallbreaker_position = [
    (509, 305),(328, 277),(385, 235),(435, 192),(516, 129),(605, 58),(880, 159),(1101, 433),
    (277, 410), (353, 484),  (277, 410), (1129, 401)]
electro_dragon_positions = [
    (342, 260), (362, 245), (377, 234), (395, 223), (413, 210),
    (431, 197), (454, 181), (395, 223),(377, 234)
]
new_troop_positions = [
    (330, 268), (338, 262), (345, 256), (353, 250), (360, 244),
    (368, 238), (375, 232), (383, 226), (390, 220), (398, 214),
    (405, 208), (413, 202), (420, 196), (428, 190), (435, 184),
    (443, 178), (450, 172), (340, 272), (348, 265), (356, 258),
    (363, 252), (371, 246), (378, 240), (386, 234), (393, 228),
    (401, 222), (408, 216), (416, 210), (423, 204), (431, 198),
    (438, 192), (446, 186), (453, 180), (335, 264), (343, 257),
    (350, 251), (358, 245), (365, 239), (373, 233), (380, 227),
    (388, 221), (395, 215), (403, 209), (410, 203), (418, 197),
    (425, 191), (433, 185), (440, 179), (448, 173), (455, 167),
]
balloon_positions = [
    (342, 260), (362, 245), (377, 234), (395, 223), (413, 210), 
    (431, 197), (454, 181), (377, 234), (395, 223), (413, 210),
    (395, 223), (342, 260), (362, 245), (377, 234), (395, 223)
]
warden_position = (395, 223)
archer_queen_position = (207, 353)
king_position = (347, 489)
duke_position = (347, 489)
prince_position = (395, 223)
rc_position = (640, 62)
rc_fallback_position = (413, 210)   # electro/balloon lane — used when rc_position isn't a legal drop on this base
cc_position = (207, 353)
baby_dragon_position = (395, 223)
b_hero_position = (395, 223)  # Builder Base hero deploy point


rage_spell_positions = [
    (502, 297), (632, 207),
    (581, 407), (702, 314)
]
freeze_spell_positions = [
    (595, 409), (687, 341), (760, 273)
]

def _force_foreground(win, tries=3):
    """Bring `win` to the front and confirm it really is the foreground
    window. Windows refuses SetForegroundWindow while another app holds focus
    (pygetwindow raises 'Error code 0 - The operation completed
    successfully'); live, the bot then searched/clicked over VS Code for
    minutes. Minimize + restore gets past that lock."""
    for _ in range(tries):
        try:
            if win.isMinimized:
                win.restore()
            win.activate()
        except Exception:
            pass
        time.sleep(0.15)
        active = gw.getActiveWindow()
        if active is not None and active._hWnd == win._hWnd:
            return True
        try:
            win.minimize()
            time.sleep(0.3)
            win.restore()
            time.sleep(0.5)
        except Exception:
            pass
    active = gw.getActiveWindow()
    return active is not None and active._hWnd == win._hWnd

def activate_game_window():
    """Bring LDPlayer window to the foreground."""
    global WINDOW_X, WINDOW_Y
    logging.debug("Activating LDPlayer window")
    try:
        windows = gw.getWindowsWithTitle("LDPlayer")

        # --- REMOVED reloadgame.png check ---
        # pos = locate_image("misc/reloadgame.png")
        # if pos:
        #     pyautogui.click(pos)

        if not windows:
            logging.error("No LDPlayer window found")
            return False

        game_window = windows[0]
        if not _force_foreground(game_window):
            logging.error("LDPlayer could not be brought to the front")
            return False
        WINDOW_X, WINDOW_Y = game_window.left, game_window.top
        logging.info(f"LDPlayer activated at ({WINDOW_X}, {WINDOW_Y})")

        #time.sleep(1)
        return True

    except Exception as e:
        logging.error(f"Failed to activate LDPlayer: {traceback.format_exc()}")
        return False


def locate_image(image_path, confidence=0.8, retries=CONFIG['retry_attempts']):
    """Locate an image on screen with retries."""
    logging.debug(f"Looking for '{image_path}' with confidence={confidence}")
    for attempt in range(retries):
        try:
            pos = pyautogui.locateCenterOnScreen(image_path, confidence=confidence)
            if pos:
                logging.info(f"Found '{image_path}' at {pos} (Attempt {attempt + 1})")
                return pos
            time.sleep(CONFIG['retry_delay'])
        except pyautogui.ImageNotFoundException:
            pass   # pyscreeze raises instead of returning None — just "not on screen"
        except Exception as e:
            logging.error(f"Error locating '{image_path}': {traceback.format_exc()}")
    logging.debug(f"'{image_path}' not found")
    return None

def zoomOutAndCenter():
    """Zoom out and center base."""
    logging.debug("Zooming out and centering base")

    if not activate_game_window():
        return False

    pyautogui.keyDown('ctrl')
    time.sleep(0.1)
    for _ in range(5):
        pyautogui.scroll(-500)
        time.sleep(0.2)
    pyautogui.keyUp('ctrl')
    time.sleep(0.1)

    pyautogui.moveTo(WINDOW_X + 640, WINDOW_Y + 360)
    pyautogui.click()
    time.sleep(0.5)

    logging.info("Base centered")
    return True
def _loot_digit_mask(crop_bgr):
    """Black-on-white image of just the cream-coloured loot digits.
    A plain grey threshold reads nothing over a busy battlefield, and a
    bright/unsaturated mask alone also picks up snow on winter bases
    ('698 714' -> '69871499'). So: drop bluish pixels, then keep only
    digit-sized blobs that sit on the same row and don't touch the crop edge
    (live-verified on 3 bases: 1184638, 883485, 698714).
    Also returns each digit, left to right, as a 30x20 0/1 image for
    _classify_loot_digits. Two touching digits ('99') form one wide blob,
    which is split evenly."""
    up = cv2.resize(crop_bgr, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    h, s, v = cv2.split(cv2.cvtColor(up, cv2.COLOR_BGR2HSV))
    mask = ((v > 170) & (s < 90) & ~((h >= 80) & (h <= 140) & (s > 20))).astype(np.uint8) * 255

    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    img_h = mask.shape[0]
    digits = [i for i in range(1, n)
              if 35 <= stats[i][3] <= 80                    # digit height at 4x
              and stats[i][2] <= stats[i][3] * 3.5          # up to ~3 touching digits
              and stats[i][4] >= 150
              and stats[i][1] > 2 and stats[i][1] + stats[i][3] < img_h - 2]
    keep = np.zeros_like(mask)
    digit_imgs = []
    if digits:
        row_mid = np.median([stats[i][1] + stats[i][3] / 2 for i in digits])
        for i in sorted(digits, key=lambda i: stats[i][0]):
            x, y, w, bh = stats[i][:4]
            if abs(y + bh / 2 - row_mid) > 12:
                continue
            keep[labels == i] = 255
            blob = (labels[y:y + bh, x:x + w] == i).astype(np.float32)
            parts = max(1, round(w / (0.82 * bh))) if w > bh * 1.3 else 1
            for p in range(parts):
                piece = blob[:, p * w // parts:(p + 1) * w // parts]
                digit_imgs.append(cv2.resize(piece, (20, 30), interpolation=cv2.INTER_AREA))
    return cv2.copyMakeBorder(cv2.bitwise_not(keep), 20, 20, 20, 20,
                              cv2.BORDER_CONSTANT, value=255), digit_imgs

# Sample digits cut from live loot reads (built from ~100 bases). Tesseract
# misreads this font too often — drops 1s ('711 761' -> 71761) and turns 9
# into 5/3 — while nearest-sample matching had 0 errors on 500 digits.
try:
    _LOOT_DIGITS = np.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "misc", "loot_digits.npz"))
    _LOOT_DIGIT_SAMPLES = [(d, img) for d in _LOOT_DIGITS.files for img in _LOOT_DIGITS[d].astype(np.float32) / 255]
except Exception:
    _LOOT_DIGIT_SAMPLES = []

def _classify_loot_digits(digit_imgs):
    return ''.join(min(_LOOT_DIGIT_SAMPLES, key=lambda s: np.abs(s[1] - im).sum())[0]
                   for im in digit_imgs)

def _ocr_loot_mask(mask, digit_imgs):
    """Read the loot number: per-digit sample matching when the samples file
    is present, else Tesseract, preferring a read with one digit per blob
    (at full 4x size it sometimes drops a '0': '2 013 889' -> 213889)."""
    if _LOOT_DIGIT_SAMPLES and digit_imgs:
        return _classify_loot_digits(digit_imgs)
    first = ''
    for scale, psm in ((0.5, 7), (1.0, 7), (0.35, 7), (0.5, 8)):
        img = cv2.resize(mask, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale != 1.0 else mask
        text = ''.join(filter(str.isdigit, pytesseract.image_to_string(
            img, config=f'--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789')))
        if text and len(text) == len(digit_imgs):
            return text
        first = first or text
    return first

def recognize_numbers_from_region(region):
    """Capture a screenshot of the specified region and recognize numbers using pytesseract."""
    try:
        # Capture screenshot of the region
        screenshot = pyautogui.screenshot(region=region)
        
        # Save with timestamp for debugging
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"gold_region_{timestamp}.png"
        screenshot.save(filename)
        gold_region_files.append(filename)
        logging.info(f"Gold region saved as {filename}")
        
        # Read the image with OpenCV
        image = cv2.imread(filename)
        numbers = _ocr_loot_mask(*_loot_digit_mask(image))

        if not numbers:
            # Fallback: the original grey threshold
            gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            _, threshold_img = cv2.threshold(gray_image, 150, 255, cv2.THRESH_BINARY)
            recognized_text = pytesseract.image_to_string(
                threshold_img, config=r'--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789')
            numbers = ''.join(filter(str.isdigit, recognized_text))

        gold = int(numbers) if numbers else 0
        logging.debug(f"Recognized gold amount: {gold}")

        # A 0 read is left to the caller: it fails the min-gold check (so
        # attack() skips to the next base) unless min gold is 0. Skipping here
        # as well used to throw away every base once and attack the next one
        # unchecked.
        if gold == 0:
            logging.warning("Gold OCR read 0")

        return gold
    except Exception as e:
        logging.error(f"Error in recognize_numbers_from_region: {traceback.format_exc()}")
        return 0
    
def isGoodOpponent():
    """Check if opponent has enough gold using pytesseract OCR."""
    logging.debug("Entering isGoodOpponent")
    gold_region = (WINDOW_X + CONFIG['gold_region'][0], WINDOW_Y + CONFIG['gold_region'][1], 
                   CONFIG['gold_region'][2], CONFIG['gold_region'][3])
    
    # Visualize gold region (commented out as per your code)
    # draw_green_square(gold_region[0], gold_region[1], gold_region[2], gold_region[3], duration=1)
    
    # Fetch gold amount using OCR. Right after the battle opens the loot
    # numbers can still be settling (read 54786 for 447059 live), so keep
    # reading until two consecutive reads agree.
    gold = recognize_numbers_from_region(gold_region)
    for _ in range(4):
        time.sleep(0.7)
        again = recognize_numbers_from_region(gold_region)
        if again == gold:
            break
        logging.info(f"Gold read changed {gold} -> {again}, reading again")
        gold = again
    logging.info(f"Detected gold: {gold}")
    
    # Check if gold meets the minimum requirement
    result = gold >= CONFIG['min_gold_to_attack']
    logging.debug(f"Is good opponent? {result} (Gold: {gold} vs Min: {CONFIG['min_gold_to_attack']})")
    return result

def updateTimestamp(timer: str):
    """Update timestamp."""
    if timer in timestamps:
        timestamps[timer] = datetime.now()

def endBattle():
    """End the battle if it is still running. Once troops are deployed the
    red button reads 'Surrender' instead of 'End Battle' — looking only for
    End Battle left the bot waiting out the full 3-minute timer. Neither
    button is shown when the battle already ended on its own, so never click None."""
    for btn_img, ok_img in (("misc/battle_screen.png", "misc/end_ok.png"),
                            ("misc/surrender.PNG", "misc/surrender_ok.PNG")):
        btn = locate_image(btn_img, retries=1)
        if btn:
            pyautogui.click(btn)
            time.sleep(1.5)
            ok_btn = locate_image(ok_img)
            if ok_btn:
                pyautogui.click(ok_btn)
            logging.info(f"Battle ended early via {btn_img}")
            return
    logging.info("No End Battle/Surrender button — battle already over")


def finishBattleAndGoHome(timeout=120):
    """End battle and return to village. Gives up after `timeout` seconds
    (it used to loop forever if Return Home never appeared)."""
    logging.debug("Entering finishBattleAndGoHome")
    deadline = time.time() + timeout
    checks = 0
    while time.time() < deadline:
        if checks % 8 == 0:
            activate_game_window()   # another window may have taken focus and covered the game
        checks += 1
        pos = locate_image("misc/end_battle.png", retries=1)
        if pos:
            pyautogui.click(pos)
            updateTimestamp('_lastInteraction')
            logging.info("Battle ended, returning home")
            time.sleep(4)
            #trainTroops()
            return True
        time.sleep(1)
    logging.error(f"Return Home button not found after {timeout}s")
    return False


LDPLAYER_DIR = r"C:\LDPlayer\LDPlayer9"
COC_PACKAGE = "com.supercell.clashofclans"

def _screen_frozen(seconds=3.0):
    """The game always animates (water, flags, troops). An unchanged window
    for a few seconds means the emulator hung — live, Android's System UI
    stopped responding and the last frame (Battle button included) stayed on
    screen, so every attack failed while recovery thought it was home."""
    try:
        win = gw.getWindowsWithTitle("LDPlayer")[0]
        region = (win.left + 10, win.top + 40, win.width - 70, win.height - 50)
        a = np.array(pyautogui.screenshot(region=region)).astype(np.int16)
        time.sleep(seconds)
        b = np.array(pyautogui.screenshot(region=region)).astype(np.int16)
        return int((np.abs(a - b).sum(axis=2) > 30).sum()) < 50
    except Exception:
        return False

def _ldconsole(*args, timeout=60):
    import subprocess
    try:
        subprocess.run([os.path.join(LDPLAYER_DIR, "ldconsole.exe"), *args],
                       timeout=timeout, capture_output=True)
    except Exception:
        logging.error(f"ldconsole {args} failed: {traceback.format_exc()}")

def _close_android_anr():
    """Tap 'Close app' on Android's '<app> isn't responding' dialog, if shown."""
    try:
        win = gw.getWindowsWithTitle("LDPlayer")[0]
        img = pyautogui.screenshot(region=(win.left, win.top, win.width, win.height))
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        words = [w.lower() for w in data["text"]]
        if not any("responding" in w for w in words):
            return False
        for i, w in enumerate(words[:-1]):
            if w == "close" and words[i + 1].startswith("app"):
                pyautogui.click(win.left + data["left"][i] + data["width"][i] // 2,
                                win.top + data["top"][i] + data["height"][i] // 2)
                logging.warning("[RECOVER] Closed an Android 'isn't responding' dialog")
                time.sleep(5)
                return True
    except Exception:
        logging.error(f"ANR check failed: {traceback.format_exc()}")
    return False

def _clear_launch_popups():
    """LDPlayer's top-up ad and the game's 'Welcome back' / 'Okay' popups."""
    for _ in range(3):
        for img in ("misc/ld_ad_close.png", "misc/welcome_okay.png", "misc/end_ok.png"):
            pos = locate_image(img, retries=1)
            if pos:
                pyautogui.click(pos)
                time.sleep(2)

def restart_game():
    """Relaunch Clash; reboot the emulator if it is still frozen after that."""
    if not os.path.exists(os.path.join(LDPLAYER_DIR, "ldconsole.exe")):
        logging.error("[RECOVER] ldconsole.exe not found — can't restart the game")
        return False
    logging.warning("[RECOVER] Game frozen — restarting Clash of Clans")
    _ldconsole("killapp", "--index", "0", "--packagename", COC_PACKAGE)
    time.sleep(3)
    _ldconsole("runapp", "--index", "0", "--packagename", COC_PACKAGE)
    time.sleep(45)
    activate_game_window()
    _close_android_anr()
    if _screen_frozen():
        logging.warning("[RECOVER] Still frozen — rebooting the emulator")
        _ldconsole("reboot", "--index", "0")
        time.sleep(90)
        for _ in range(4):
            activate_game_window()
            if not _close_android_anr():
                break
        _ldconsole("runapp", "--index", "0", "--packagename", COC_PACKAGE)
        time.sleep(60)
        activate_game_window()
        _close_android_anr()
    _clear_launch_popups()
    ok = not _screen_frozen()
    logging.warning(f"[RECOVER] Game restart {'succeeded' if ok else 'did NOT unfreeze the game'}")
    return ok

def recover_to_home(max_rounds=6):
    """Get back to the home village from whatever screen the bot is stuck on
    (results screen, a popup, the match-finder, a battle that never ended).
    Returns True once the home Battle button is visible."""
    activate_game_window()
    if _screen_frozen():
        restart_game()
        activate_game_window()
    for rnd in range(1, max_rounds + 1):
        if locate_image("misc/attack_button.png", retries=1):
            logging.info(f"[RECOVER] Home screen reached (round {rnd})")
            return True
        for img in ("misc/end_battle.png",        # Return Home (results screen)
                    "misc/battle_screen.png",     # End Battle (battle still running)
                    "misc/end_ok.png",            # Okay (end-battle confirm)
                    "misc/surrender.PNG",         # Surrender (troops already deployed)
                    "misc/welcome_okay.png",      # 'Welcome back Chief' after a reload
                    "misc/ld_ad_close.png",       # LDPlayer top-up ad over the game
                    "misc/surrender_ok.PNG",
                    "Close_Btn.PNG",
                    "misc/close.PNG"):
            pos = locate_image(img, retries=1)
            if pos:
                logging.warning(f"[RECOVER] round {rnd}: clicking {img} at {pos}")
                pyautogui.click(pos)
                time.sleep(2)
                break
        else:
            logging.warning(f"[RECOVER] round {rnd}: nothing recognised on screen")
            time.sleep(3)
    logging.error("[RECOVER] Could not get back to the home screen")
    try:
        pyautogui.screenshot(f"stuck_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.png")
    except Exception:
        pass
    return False

# ---------------------------------------------------------
#  AI DEPLOY — heat-map + lead-ahead spell targeting (rage only)
# ---------------------------------------------------------
# Opt-in (CONFIG['ai_deploy']) replacement for the fixed rage spell
# coordinate list. Fixed positions can't account for where the troop pack
# actually ends up on a given base, so rage sometimes buffed empty ground
# instead of the actual troops. This instead: screenshots the battlefield,
# diffs two quick samples to find where the troop mass currently is AND
# which way it's moving, then aims 3-7 tiles ahead of it along that
# direction (accounting for the time this analysis itself takes) — and
# never within AI_MIN_SPELL_SEPARATION_PX of a spell already dropped this
# attack. Every entry point falls back to the existing fixed-position
# behavior on any failure, so this can never break the normal attack flow —
# worst case it behaves exactly as before.
#
# Freeze deliberately does NOT use this — see the note by
# AI_MIN_SPELL_SEPARATION_PX below for why.
AI_BATTLE_REGION = (257, 36, 1047 - 257, 583 - 36)  # same area as capture_battlefield()
AI_TILE_PX = 22          # approx on-screen px per base tile at the standard battle zoom — tune if drops land short/long
AI_LEAD_TILES_RAGE = (3, 7)     # rage: buff troops close to where they are now — live-verified working well
# Freeze always uses the fixed freeze_spell_positions list, never AI Deploy —
# without real defense detection, an AI-aimed freeze can only guess how far
# ahead the nearest defense is, and a miss (landing outside the wall on
# empty ground) wastes the whole spell. Rage doesn't have this problem since
# it just needs to catch the troops themselves.
AI_MIN_SPELL_SEPARATION_PX = 70  # never drop two spells closer than this to each other
AI_CLICK_DELAY_ESTIMATE = 0.3    # rough extra seconds between "done analyzing" and the click landing

def _ai_capture_gray():
    """Screenshot the battlefield region as grayscale, for frame differencing.
    Returns None on failure so callers can fall back cleanly."""
    try:
        if not activate_game_window():
            return None
        region = (WINDOW_X + AI_BATTLE_REGION[0], WINDOW_Y + AI_BATTLE_REGION[1],
                  AI_BATTLE_REGION[2], AI_BATTLE_REGION[3])
        img = pyautogui.screenshot(region=region)
        gray = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
        return gray
    except Exception:
        logging.error(f"AI Deploy: capture failed: {traceback.format_exc()}")
        return None

def _ai_troop_mass_centroid(before_gray, after_gray):
    """Frame-difference two battlefield captures to isolate what changed
    (i.e. troops that weren't there in `before` — buildings/terrain are
    static so they cancel out). Returns (cx, cy) in region-local pixels, or
    None if too little changed to trust there's a real troop cluster there."""
    try:
        diff = cv2.absdiff(before_gray, after_gray)
        _, mask = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        ys, xs = np.nonzero(mask)
        if len(xs) < 40:
            return None
        return float(np.mean(xs)), float(np.mean(ys))
    except Exception:
        logging.error(f"AI Deploy: centroid calc failed: {traceback.format_exc()}")
        return None

def ai_compute_spell_drop(prior_points, lead_tiles=AI_LEAD_TILES_RAGE):
    """Core AI Deploy targeting. Takes two quick battlefield samples to find
    the troop mass's current position and velocity, extrapolates forward to
    where it'll be by the time a spell actually lands (tile-lead distance +
    velocity * remaining processing/click time), and nudges the result away
    from any previously-used drop point this attack. `lead_tiles` controls
    how far ahead to aim — small for rage (buff the troops where they are),
    large for freeze (freeze is wasted on troops; it needs to reach a
    defense further ahead). Returns an absolute (screen_x, screen_y) click
    target, or None if it couldn't confidently find troops (caller should
    fall back to a fixed position)."""
    t_start = time.time()
    before = _ai_capture_gray()
    if before is None:
        return None

    time.sleep(0.5)
    after1 = _ai_capture_gray()
    if after1 is None:
        return None
    pos1 = _ai_troop_mass_centroid(before, after1)
    if not pos1:
        logging.info("AI Deploy: no troop mass detected yet")
        return None

    time.sleep(0.5)
    after2 = _ai_capture_gray()
    if after2 is None:
        return None
    pos2 = _ai_troop_mass_centroid(after1, after2)

    cx1, cy1 = pos1
    if pos2:
        cx2, cy2 = pos2
        dx, dy = cx2 - cx1, cy2 - cy1
        dist = math.hypot(dx, dy)
        elapsed = max(0.1, time.time() - t_start - 0.5)  # time over which that velocity was measured
        if dist > 1:
            dir_x, dir_y = dx / dist, dy / dist
            speed = dist / elapsed  # px/sec
        else:
            dir_x, dir_y, speed = 0.0, 0.0, 0.0
        base_x, base_y = cx2, cy2
    else:
        dir_x, dir_y, speed = 0.0, 0.0, 0.0
        base_x, base_y = cx1, cy1

    if dir_x == 0 and dir_y == 0:
        # No measurable movement between samples (troops just landed, or
        # camera settled) — head toward the middle of the battlefield
        # instead of leaving the spell sitting right on top of the troops.
        # The base's higher-value stuff is typically more central than
        # wherever troops are dropped in from.
        region_cx, region_cy = AI_BATTLE_REGION[2] / 2, AI_BATTLE_REGION[3] / 2
        ddx, ddy = region_cx - base_x, region_cy - base_y
        ddist = math.hypot(ddx, ddy)
        if ddist > 1:
            dir_x, dir_y = ddx / ddist, ddy / ddist

    tiles_ahead = randint(*lead_tiles)
    lead_px = tiles_ahead * AI_TILE_PX
    # Compensate for the time still left between "now" and the click actually
    # registering — otherwise a fast-moving pack outruns a lead computed only
    # from the analysis window.
    extra_px = speed * AI_CLICK_DELAY_ESTIMATE

    target_x = base_x + dir_x * (lead_px + extra_px)
    target_y = base_y + dir_y * (lead_px + extra_px)

    # Stay inside the captured region — bounds re-applied after every push
    # below too, since a click outside the game window entirely (e.g. a
    # negative screen coordinate) is not just "a bad drop", it's a click on
    # whatever's behind/above the game, which must never happen.
    region_left = WINDOW_X + AI_BATTLE_REGION[0] + 15
    region_right = WINDOW_X + AI_BATTLE_REGION[0] + AI_BATTLE_REGION[2] - 15
    region_top = WINDOW_Y + AI_BATTLE_REGION[1] + 15
    region_bottom = WINDOW_Y + AI_BATTLE_REGION[1] + AI_BATTLE_REGION[3] - 15

    def _clamp(sx, sy):
        return (max(region_left, min(region_right, sx)),
                max(region_top, min(region_bottom, sy)))

    screen_x = WINDOW_X + AI_BATTLE_REGION[0] + int(target_x)
    screen_y = WINDOW_Y + AI_BATTLE_REGION[1] + int(target_y)
    screen_x, screen_y = _clamp(screen_x, screen_y)

    # Never stack a new spell on top of one already dropped this attack —
    # push it further along the direction of travel until it clears, capped
    # so this can't loop forever. Re-clamped every iteration: live-verified
    # that without this, the push could walk the point past the window edge
    # (e.g. a negative screen Y) and the resulting click lands outside the
    # game entirely instead of just being a suboptimal spot.
    for _ in range(4):
        too_close = next(
            ((px, py) for px, py in prior_points
             if math.hypot(screen_x - px, screen_y - py) < AI_MIN_SPELL_SEPARATION_PX),
            None
        )
        if not too_close:
            break
        if dir_x == 0 and dir_y == 0:
            dir_x, dir_y = 1.0, 0.0  # no known direction — just push sideways
        screen_x += int(dir_x * AI_MIN_SPELL_SEPARATION_PX)
        screen_y += int(dir_y * AI_MIN_SPELL_SEPARATION_PX)
        screen_x, screen_y = _clamp(screen_x, screen_y)

    logging.info(f"AI Deploy: troop mass ~({int(base_x)},{int(base_y)}) region-local, "
                f"dir=({dir_x:.2f},{dir_y:.2f}) speed={speed:.0f}px/s, "
                f"{tiles_ahead} tiles ahead -> click ({screen_x},{screen_y})")
    return screen_x, screen_y

def _deploy_spell_batch(fixed_positions, ai_prior_points, jitter=5, delay=0.2, lead_tiles=AI_LEAD_TILES_RAGE):
    """Deploy len(fixed_positions) spells (icon must already be selected by
    the caller). Uses AI Deploy targeting per-drop when CONFIG['ai_deploy']
    is on, falling back to the given fixed position (with the usual jitter)
    for any individual drop the AI can't confidently place — so a bad read
    never skips a spell outright. Currently only used for rage — freeze
    always uses its own plain fixed-position loop, never this."""
    for fx, fy in fixed_positions:
        pt = None
        if CONFIG['ai_deploy']:
            try:
                pt = ai_compute_spell_drop(ai_prior_points, lead_tiles=lead_tiles)
            except Exception:
                logging.error(f"AI Deploy: drop computation failed, falling back: {traceback.format_exc()}")
                pt = None
            if not pt:
                logging.info("AI Deploy: falling back to fixed position for this spell")
        if not pt:
            pt = (fx + randint(-jitter, jitter), fy + randint(-jitter, jitter))
        pyautogui.click(pt[0], pt[1])
        ai_prior_points.append(pt)
        time.sleep(delay)

def _select_slot(image_path):
    """Tap a troop/spell slot. Returns its position, or None (tapping nothing) if the icon
    isn't on screen — pyautogui.click(None) would otherwise click wherever
    the mouse is and the following deploy taps would drop whatever was
    selected before."""
    pos = locate_image(image_path)
    if not pos:
        logging.warning(f"Slot '{image_path}' not found — skipping its deployment")
        return None
    pyautogui.click(pos)
    return pos

# Far-left map edge — legal on every base seen live (Queen/siege land here)
LEFTOVER_DROP_POSITIONS = [(207, 353), (230, 337), (242, 327), (238, 374)]

def _slot_has_units(slot_pos):
    """A troop slot is in colour while it has units left and turns grey at x0."""
    x, y = int(slot_pos[0]), int(slot_pos[1])
    img = np.array(pyautogui.screenshot(region=(x - 30, y - 30, 60, 60)))
    return cv2.cvtColor(img, cv2.COLOR_RGB2HSV)[:, :, 1].mean() >= 60

def _deploy_leftovers(slot_pos, label, max_taps=12):
    """On bigger bases some fixed drop points fall inside the red no-deploy
    area and those units stay in the slot. Drop whatever is left on the map
    edge (the slot is still selected)."""
    time.sleep(0.3)
    taps = 0
    while taps < max_taps and _slot_has_units(slot_pos):
        x, y = LEFTOVER_DROP_POSITIONS[taps % len(LEFTOVER_DROP_POSITIONS)]
        pyautogui.click(x + randint(-5, 5), y + randint(-5, 5))
        taps += 1
        time.sleep(0.15)
    if taps:
        logging.warning(f"{label}: {taps} leftover unit(s) dropped on the map edge")

def _hero_slot_used(slot_pos, wait=2.0):
    """True once the hero is on the field. Live-checked slot states:
    undeployed = portrait in colour, no bar; deployed = green health bar on
    top of the slot (portrait stays in colour while still selected); dead =
    grey/dark portrait, no bar. Polls for up to `wait` seconds."""
    x, y = int(slot_pos[0]), int(slot_pos[1])
    deadline = time.time() + wait
    while True:
        img = cv2.cvtColor(np.array(pyautogui.screenshot(region=(x - 45, y - 75, 75, 105))),
                           cv2.COLOR_RGB2HSV)
        bar = img[0:12, 0:70]                        # health bar strip
        h, s, v = bar[:, :, 0], bar[:, :, 1], bar[:, :, 2]
        green = int(((h >= 35) & (h <= 85) & (s > 100) & (v > 120)).sum())
        sat = img[45:105, 15:75, 1].mean()           # portrait
        used = green > 100 or sat < 60
        if used or time.time() >= deadline:
            logging.debug(f"Hero slot {slot_pos}: bar={green} saturation={sat:.0f} used={used}")
            return used
        time.sleep(0.25)

def deployTroops():
    """Deploy troops with strategic positioning and slight randomness."""
    logging.debug("Deploying troops")
    if CONFIG['goblin_attack']:
        pyautogui.click(locate_image("troops/wall.png"))
        time.sleep(0.5)
        for x, y in wallbreaker_position:
            pyautogui.click(x + randint(-10, 10), y + randint(-10, 10))
            time.sleep(0.1)
        time.sleep(0.5)
        archer_queen_pos = locate_image("heros/archer_queen.PNG")
        if archer_queen_pos:
            pyautogui.click(archer_queen_pos)
            time.sleep(0.5)
            pyautogui.click(archer_queen_position[0] + randint(-5, 5), archer_queen_position[1] + randint(-5, 5))
            pyautogui.click(archer_queen_pos)
            # Deploy king
        king_pos = locate_image("heros/king.PNG")
        if king_pos:
            pyautogui.click(king_pos)
            time.sleep(0.5)
            pyautogui.click(king_position[0] + randint(-5, 5), king_position[1] + randint(-5, 5))
            # Deploy CC
            time.sleep(4)
            pyautogui.click(king_pos)
            # Deploy duke
        duke_pos = locate_image("heros/duke.PNG")
        if duke_pos:
            pyautogui.click(duke_pos)
            time.sleep(0.5)
            pyautogui.click(duke_position[0] + randint(-5, 5), duke_position[1] + randint(-5, 5))
            time.sleep(4)
            pyautogui.click(duke_pos)
        cc_pos = locate_image("troops/troop_siegebarracks.PNG")
        if cc_pos:
            pyautogui.click(cc_pos)
            time.sleep(0.5)
            pyautogui.click(cc_position[0] + randint(-5, 5), cc_position[1] + randint(-5, 5))
            pyautogui.click(king_pos)
        pyautogui.click(locate_image("troops/goblin.png"))
        time.sleep(0.5)
        for x, y in goblins_positions:
            pyautogui.click(x + randint(-10, 10), y + randint(-10, 10))
            time.sleep(0.2)
        time.sleep(10)
        pyautogui.click(locate_image("misc/surrender.png"))
        pyautogui.click(locate_image("misc/surrender_ok.png"))
    else:
            # Deploy **Archer Queen**
        archer_queen_pos = locate_image("heros/archer_queen.PNG")
        if archer_queen_pos:
            pyautogui.click(archer_queen_pos)
            time.sleep(0.5)
            pyautogui.click(archer_queen_position[0] + randint(-5, 5), archer_queen_position[1] + randint(-5, 5))
            pyautogui.click(archer_queen_pos)
            # Deploy king
        king_pos = locate_image("heros/king.PNG")
        if king_pos:
            pyautogui.click(king_pos)
            time.sleep(0.5)
            pyautogui.click(king_position[0] + randint(-5, 5), king_position[1] + randint(-5, 5))
            # Deploy CC
            time.sleep(4)
            # Deploy duke
        duke_pos = locate_image("heros/duke.PNG")
        if duke_pos:
            pyautogui.click(duke_pos)
            time.sleep(0.5)
            pyautogui.click(duke_position[0] + randint(-5, 5), duke_position[1] + randint(-5, 5))
            time.sleep(4)
        cc_pos = locate_image("troops/troop_siegebarracks.PNG")
        if cc_pos:
            pyautogui.click(cc_pos)
            time.sleep(0.5)
            pyautogui.click(cc_position[0] + randint(-5, 5), cc_position[1] + randint(-5, 5))

        # Deploy **Event Troop** (deployed at the Electro Dragon spot, before Electro Dragons)
        if CONFIG['event_troops']:
            event_troop_pos = locate_image("troops/eventtroops.png")
            if event_troop_pos:
                pyautogui.click(event_troop_pos)
                time.sleep(0.5)
                count = max(1, CONFIG['event_troop_count'])
                for i in range(count):
                    x, y = electro_dragon_positions[i % len(electro_dragon_positions)]
                    pyautogui.click(x + randint(-10, 10), y + randint(-10, 10))
                    time.sleep(0.1)

        # Deploy **Electro Dragons**
        electro_slot = _select_slot("troops/troops_electro.png")
        if electro_slot:
            time.sleep(0.5)
            for x, y in electro_dragon_positions:
                pyautogui.click(x + randint(-10, 10), y + randint(-10, 10))
                time.sleep(0.1)
            _deploy_leftovers(electro_slot, "Electro Dragons")

        # Deploy **Balloons**
        loon_slot = _select_slot("troops/troops_loon.png")
        if loon_slot:
            time.sleep(0.1)
            for x, y in balloon_positions:
                pyautogui.click(x + randint(-10, 10), y + randint(-10, 10))
                time.sleep(0.1)
            _deploy_leftovers(loon_slot, "Balloons")

        # Deploy **Warden**
        warden_pos = locate_image("heros/warden.PNG")
        if warden_pos:
            pyautogui.click(warden_pos)
            time.sleep(0.1)
            pyautogui.click(warden_position[0] + randint(-5, 5), warden_position[1] + randint(-5, 5))
        # Deploy Prince
        prince_pos = locate_image("heros/prince.PNG")
        if prince_pos:
            pyautogui.click(prince_pos)
            time.sleep(0.1)
            pyautogui.click(prince_position[0] + randint(-5, 5), prince_position[1] + randint(-5, 5))   
        # Wait **3 seconds**, then enable **Warden Ability**
        time.sleep(3)
        if warden_pos:
            pyautogui.click(warden_pos)
        if prince_pos:
            time.sleep(1)
            pyautogui.click(prince_pos)
        rc_pos = locate_image("heros/rc.PNG")
        if rc_pos:
            pyautogui.click(rc_pos)
            time.sleep(0.1)
            # rc_position sits at the top edge of the map; on some bases' scenery
            # it isn't a legal drop and the Champion sat out the attack (2 of 4
            # live attacks), so fall back to the troop lane.
            # Last resort: the far-left map edge where the Queen and siege
            # barracks land — legal on every base seen live.
            for x, y in (rc_position, rc_fallback_position, archer_queen_position):
                pyautogui.click(x + randint(-5, 5), y + randint(-5, 5))
                if _hero_slot_used(rc_pos):
                    pyautogui.click(rc_pos)   # ability
                    break
                logging.warning(f"Royal Champion not deployed at ({x}, {y}) — trying next spot")
        baby_dragon_pos = locate_image("troops/troop_babydragon.PNG")
        if baby_dragon_pos:
            pyautogui.click(baby_dragon_pos)
            time.sleep(0.1)
            pyautogui.click(baby_dragon_position[0] + randint(-5, 5), baby_dragon_position[1] + randint(-5, 5))
        # Wait **3 seconds**, then deploy **first 2 Rage Spells**
        time.sleep(3)
        # Tracks every AI-Deploy spell drop point this attack (screen coords)
        # so a later rage cast never stacks on an earlier one. Freeze doesn't
        # use AI Deploy (see note above), so it doesn't touch this list.
        ai_spell_drop_points = []
        if _select_slot("troops/rage_spell.PNG"):
            time.sleep(0.5)
            _deploy_spell_batch(rage_spell_positions[:2], ai_spell_drop_points)

            # Wait **4 seconds**, then deploy **next 2 Rage Spells**
            time.sleep(9)
            _deploy_spell_batch(rage_spell_positions[2:], ai_spell_drop_points)

        # Deploy **Freeze Spells** together — always the fixed positions,
        # never AI Deploy. Without real defense detection, an AI-aimed
        # freeze can only guess how far ahead the nearest defense is, and a
        # miss (freeze landing outside the wall on empty ground) wastes the
        # whole spell — worse than the original coordinates, which are at
        # least known-reasonable spots. Rage doesn't have this problem since
        # it just needs to catch the troops themselves, which AI Deploy
        # reliably found in testing.
        time.sleep(1)
        if _select_slot("troops/freez_spell.PNG"):
            time.sleep(0.5)
            for x, y in freeze_spell_positions:
                pyautogui.click(x + randint(-5, 5), y + randint(-5, 5))
                time.sleep(0.2)

        # Deploy **New Troop** (after all troops, spells, and heroes)
        new_troop_pos = locate_image("troops/newtroop.png")
        if new_troop_pos:
            pyautogui.click(new_troop_pos)
            time.sleep(0.5)
            for x, y in new_troop_positions:
                pyautogui.click(x + randint(-10, 10), y + randint(-10, 10))
                time.sleep(0.1)


    logging.info("Troop and spell deployment complete")
    return True

def zoomOutBattlefield():
    """Zoom out on the battlefield (main village or builder base) so the whole
    base is visible. The fixed deploy-position coordinates
    (electro_dragon_positions, balloon_positions, rage/freeze positions, ...)
    were captured against a zoomed-out view — without this, taps land on the
    wrong world location and troops silently fail to deploy even though the
    deploy button itself was found. The battle inherits the home village
    zoom, and tapping a Wall row during wall upgrades zooms the village in,
    so this is needed on every attack. Scroll only — no tap, since a tap on
    the battlefield could deploy a troop."""
    logging.debug("Zooming out battlefield")
    try:
        if not activate_game_window():
            return False
        # Hover (no click) over the battlefield so the scroll zooms the map
        # rather than landing on the troop bar / Next button.
        pyautogui.moveTo(WINDOW_X + 640, WINDOW_Y + 300)
        pyautogui.keyDown('ctrl')
        time.sleep(0.1)
        for _ in range(5):
            pyautogui.scroll(-500)
            time.sleep(0.2)
        pyautogui.keyUp('ctrl')
        time.sleep(0.3)
        logging.info("Battlefield zoomed out")
        return True
    except Exception:
        logging.error(f"Failed to zoom out battlefield: {traceback.format_exc()}")
        try:
            pyautogui.keyUp('ctrl')
        except Exception:
            pass
        return False

def startAttackingBuilder():
    """Start a Builder Base attack: b_attack -> b_find -> wait for b_battle -> zoom out."""
    logging.debug("Starting builder base attack process")
    try:
        attack_btn = locate_image("misc/b_attack.PNG")
        if not attack_btn:
            logging.error("Builder attack button not found")
            return False
        pyautogui.click(attack_btn)
        logging.info("Clicked builder attack button")
        time.sleep(2)

        find_btn = locate_image("misc/b_find.PNG")
        if not find_btn:
            logging.error("Builder find opponent button not found")
            return False
        pyautogui.click(find_btn)
        logging.info("Clicked builder find opponent button")
        time.sleep(2)

        logging.info("Waiting for builder battle screen...")
        for i in range(CONFIG['battle_wait_timeout']):
            if _locate_any_image(["misc/b_battle.PNG", "misc/b_battle_1.PNG"]):
                logging.info("Builder battle screen detected")
                # Zoom out immediately so the whole village is visible before
                # any troop is deployed — deploy coordinates depend on this.
                zoomOutBattlefield()
                time.sleep(0.5)
                return True
            time.sleep(1)

        logging.error(f"Builder battle screen not found after {CONFIG['battle_wait_timeout']} seconds")
        return False
    except Exception:
        logging.error(f"Unexpected error in startAttackingBuilder: {traceback.format_exc()}")
        return False

def _b_hero_heal_loop(hero_pos, stop_event):
    """Background: re-click the builder base hero's icon every 17s to trigger
    his self-heal ability, until stop_event is set (battle over/redeploying)."""
    while not stop_event.wait(17):
        try:
            pyautogui.click(hero_pos)
            logging.debug("Builder hero heal ability triggered")
        except Exception:
            logging.warning(f"Failed to trigger hero heal ability: {traceback.format_exc()}")

def _locate_builder_button(image_path, label, confidences=(0.8, 0.7, 0.6), retries=2):
    """Search for a builder-base UI button, sweeping through looser confidence
    levels before giving up. image_path may be a single path or a list of
    variant paths (e.g. b_hero.PNG OR b_hero_1.PNG) — the first one found at
    any confidence wins. Logs clearly on both success and total failure so
    deploy problems show up in the log instead of failing silently."""
    paths = [image_path] if isinstance(image_path, str) else list(image_path)
    for confidence in confidences:
        for path in paths:
            pos = locate_image(path, confidence=confidence, retries=retries)
            if pos:
                logging.info(f"{label} found via '{path}' at {pos} (confidence={confidence})")
                return pos
    logging.error(f"{label} NOT found (tried {paths}) after trying confidences {confidences}")
    return None

def _locate_any_image(image_paths, confidence=0.8, retries=1):
    """Try each image path in turn, returning the first on-screen match.
    Used where the game shows one of a few visually different variants of the
    same screen/button (e.g. b_battle.PNG OR b_battle_1.PNG)."""
    for path in image_paths:
        pos = locate_image(path, confidence=confidence, retries=retries)
        if pos:
            return pos
    return None

def _locate_all_builder_buttons(image_path, confidence=0.75, max_results=3, min_distance=30):
    """Return up to max_results distinct on-screen matches for image_path (as
    center points), merging near-duplicate detections within min_distance
    pixels of each other. Used where multiple identical buttons can be on
    screen at once (e.g. one ability button per deployed witch)."""
    try:
        boxes = list(pyautogui.locateAllOnScreen(image_path, confidence=confidence))
    except Exception:
        logging.error(f"Error locating all instances of '{image_path}': {traceback.format_exc()}")
        return []

    centers = []
    for box in boxes:
        cx = box.left + box.width // 2
        cy = box.top + box.height // 2
        if all(abs(cx - ex) > min_distance or abs(cy - ey) > min_distance for ex, ey in centers):
            centers.append((cx, cy))
        if len(centers) >= max_results:
            break
    return centers

def deployTroopsBuilder():
    """Deploy Builder Base troops: giants -> hero -> witch. Giants and witches
    both deploy over the same loon/balloon perimeter positions (electro dragon
    positions didn't land on the base). Starts a background thread that
    re-clicks the hero every 17s to heal him, and activates each witch's
    ability (up to 3) 12-16s after deployment. Returns the heal-loop's stop
    Event (or None if the hero wasn't found), so the caller can stop it once
    the battle ends or troops are redeployed."""
    logging.debug("Deploying builder base troops")
    hero_heal_stop = None

    try:
        # Deploy Giants (same deploy points as the witches — electro dragon
        # positions weren't landing on the base, so try the witch/loon spots)
        giant_pos = _locate_builder_button("troops/b_giant.PNG", "Giant button")
        if giant_pos:
            try:
                pyautogui.click(giant_pos)
                time.sleep(0.5)
                for x, y in balloon_positions:
                    pyautogui.click(x + randint(-10, 10), y + randint(-10, 10))
                    time.sleep(0.1)
                logging.info("Giants deployed")
            except Exception:
                logging.error(f"Error while deploying giants: {traceback.format_exc()}")
        else:
            logging.error("Giants NOT deployed — button not found")

        # Deploy Hero
        hero_pos = _locate_builder_button(["heros/b_hero.PNG", "heros/b_hero_1.PNG"], "Hero button")
        if hero_pos:
            try:
                pyautogui.click(hero_pos)
                time.sleep(0.5)
                pyautogui.click(b_hero_position[0] + randint(-5, 5), b_hero_position[1] + randint(-5, 5))
                hero_heal_stop = threading.Event()
                threading.Thread(target=_b_hero_heal_loop, args=(hero_pos, hero_heal_stop),
                                 daemon=True, name="BHeroHeal").start()
                logging.info("Hero deployed, heal loop started")
            except Exception:
                logging.error(f"Error while deploying hero: {traceback.format_exc()}")
        else:
            logging.error("Hero NOT deployed — button not found")

        # Deploy Witch (reuse loon/balloon positions)
        witch_pos = _locate_builder_button("troops/b_witch.PNG", "Witch button")
        if witch_pos:
            try:
                pyautogui.click(witch_pos)
                time.sleep(0.5)
                for x, y in balloon_positions:
                    pyautogui.click(x + randint(-10, 10), y + randint(-10, 10))
                    time.sleep(0.1)
                logging.info("Witches deployed")
            except Exception:
                logging.error(f"Error while deploying witches: {traceback.format_exc()}")
        else:
            logging.error("Witches NOT deployed — button not found")

        # Wait for all troops to settle, then activate every witch's ability —
        # up to 3 witches, each with its own ability button on screen at once.
        # Poll for up to ~25s total instead of one fixed-time check, since the
        # buttons' appearance timing can vary between attacks.
        time.sleep(randint(12, 16))
        witch_powers_clicked = 0
        for attempt in range(10):
            remaining = 3 - witch_powers_clicked
            if remaining <= 0:
                break
            positions = _locate_all_builder_buttons("troops/b_witch_power.PNG",
                                                     confidence=0.75, max_results=remaining)
            if not positions:
                logging.debug(f"Witch power button(s) not found yet "
                             f"(poll {attempt + 1}/10, clicked so far={witch_powers_clicked}/3)")
                time.sleep(1)
                continue
            for (cx, cy) in positions:
                try:
                    pyautogui.click(cx, cy)
                    witch_powers_clicked += 1
                    logging.info(f"Witch ability #{witch_powers_clicked} activated at ({cx}, {cy})")
                    time.sleep(0.4)
                except Exception:
                    logging.error(f"Error clicking witch power button at ({cx},{cy}): {traceback.format_exc()}")
            time.sleep(1)  # let clicked buttons disappear before rescanning

        if witch_powers_clicked == 0:
            logging.error("Witch power NOT activated — no ability buttons found after extended search")
        else:
            logging.info(f"Total witch abilities activated: {witch_powers_clicked}/3")

        logging.info("Builder base troop deployment complete")
        return hero_heal_stop
    except Exception:
        logging.error(f"Unexpected error in deployTroopsBuilder: {traceback.format_exc()}")
        if hero_heal_stop:
            hero_heal_stop.set()
        return None

def finishBattleAndGoHomeBuilder(hero_heal_stop=None):
    """Wait for the builder base 'return home' button and click it. If the
    battle moves to a second battleground (b_secondbattle.PNG shows up first),
    redeploy the same troops there before continuing to wait for home."""
    logging.debug("Entering finishBattleAndGoHomeBuilder")
    redeployed = False
    try:
        while True:
            pos = locate_image("misc/b_home.PNG", retries=1)
            if pos:
                pyautogui.click(pos)
                updateTimestamp('_lastInteraction')
                logging.info("Builder base battle ended, returning home")
                time.sleep(4)
                return True

            if not redeployed and locate_image("misc/b_secondbattle.PNG", retries=1):
                logging.info("Second battleground detected — redeploying troops")
                redeployed = True
                if hero_heal_stop:
                    hero_heal_stop.set()
                    hero_heal_stop = None
                try:
                    hero_heal_stop = deployTroopsBuilder()
                except Exception:
                    logging.error(f"Redeploy on second battleground failed: {traceback.format_exc()}")

            time.sleep(1)
    except Exception:
        logging.error(f"Unexpected error in finishBattleAndGoHomeBuilder: {traceback.format_exc()}")
        return False
    finally:
        if hero_heal_stop:
            hero_heal_stop.set()

def attackBuilderBase():
    """Full Builder Base attack sequence — separate from the main-village flow."""
    logging.debug("Starting builder base attack")
    hero_heal_stop = None
    try:
        if not startAttackingBuilder():
            return False
        hero_heal_stop = deployTroopsBuilder()
        finishBattleAndGoHomeBuilder(hero_heal_stop)
        logging.info("Builder base attack completed successfully")
        return True
    except Exception:
        logging.error(f"Unexpected error in attackBuilderBase: {traceback.format_exc()}")
        if hero_heal_stop:
            hero_heal_stop.set()
        return False

def zoomOutBuilderVillageHome():
    """Zoom out fully on the Builder Base HOME village view (as opposed to
    zoomOutBattlefield(), which zooms the attack battlefield). No center-tap
    afterward — live-verified that tapping the window center to "center the
    base" can land on a building (Wall, Gold Storage, ...) and pop open its
    info/upgrade tray; the Builder Base map is small enough to already fit
    on screen without needing to be re-centered anyway."""
    logging.debug("Zooming out builder base village")
    try:
        if not activate_game_window():
            return False
        pyautogui.keyDown('ctrl')
        time.sleep(0.1)
        for _ in range(5):
            pyautogui.scroll(-500)
            time.sleep(0.2)
        pyautogui.keyUp('ctrl')
        time.sleep(0.3)
        logging.info("Builder base village zoomed out")
        return True
    except Exception:
        logging.error(f"Failed to zoom out builder base village: {traceback.format_exc()}")
        try:
            pyautogui.keyUp('ctrl')
        except Exception:
            pass
        return False

def collectBuilderElixirCart():
    """Every CONFIG['builder_cart_check_every'] Builder Base attacks: zoom
    out to see the whole village, find the elixir cart (its on-screen
    appearance varies, so try every known variant), collect it, then close
    the collection popup."""
    logging.info("Checking for Builder Base elixir cart")
    try:
        if not zoomOutBuilderVillageHome():
            logging.error("Could not zoom out builder base village — skipping elixir cart check")
            return False

        cart_pos = _locate_builder_button(
            ["misc/elixir_cart.PNG", "misc/elixir_cart_1.PNG", "misc/elixir_cart_2.PNG"],
            "Elixir cart"
        )
        if not cart_pos:
            logging.info("No elixir cart visible — nothing to collect")
            return False

        pyautogui.click(cart_pos)
        time.sleep(1)

        collect_pos = locate_image("misc/collect.PNG")
        if collect_pos:
            pyautogui.click(collect_pos)
            logging.info("Elixir cart collected")
            time.sleep(1)
        else:
            logging.warning("Collect button not found after clicking elixir cart")

        close_pos = locate_image("misc/elixir_cart_close.PNG")
        if close_pos:
            pyautogui.click(close_pos)
            time.sleep(0.5)
            logging.info("Elixir cart popup closed")
        else:
            logging.warning("Elixir cart close button not found")

        return True
    except Exception:
        logging.error(f"Unexpected error in collectBuilderElixirCart: {traceback.format_exc()}")
        return False

def attack():
    """Handle full attack sequence."""
    logging.debug("Starting attack")

    if not activate_game_window():
        return False

    # **BUILDER BASE MODE: separate attack flow, troops, and deploy positions**
    if CONFIG['builder_base']:
        return attackBuilderBase()

    #collectorchecker()
    if not startAttacking():
        return False
    # if not zoomOutAndCenter():
    #     return False
    
    # **RANKED MODE: Skip validation, attack directly**
    if CONFIG['ranked_mode']:
        logging.info("Ranked mode enabled - skipping opponent validation")
        if deployTroops():
            # End battle in 1 min
            time.sleep(60)
            endBattle()
            if not finishBattleAndGoHome():
                return False
            # Delete saved gold region screenshots
            for file in gold_region_files:
                try:
                    os.remove(file)
                    logging.info(f"Deleted temp file: {file}")
                except Exception as e:
                    logging.warning(f"Failed to delete temp file {file}: {e}")
            gold_region_files.clear()
            logging.info("Ranked attack completed successfully")
            return True
        else:
            logging.error("Troop deployment failed in ranked mode")
            return False
    
    # **NORMAL MODE: Check opponent and iterate**
    find_match_image = "misc/findmatch_ranked.png" if CONFIG['ranked_mode'] else "misc/find_match.png"
    for attempt in range(20):
        logging.debug(f"Attack attempt {attempt + 1}/20")
        if isGoodOpponentAdvanced():
            if deployTroops():
                #end battle in 1 mnt
                if CONFIG['ranked_mode']:
                    time.sleep(120)
                else:
                    time.sleep(60)
                endBattle()
                if not finishBattleAndGoHome():
                    return False
                # Delete saved gold region screenshots
                for file in gold_region_files:
                    try:
                        os.remove(file)
                        logging.info(f"Deleted temp file: {file}")
                    except Exception as e:
                        logging.warning(f"Failed to delete temp file {file}: {e}")
                gold_region_files.clear()
                logging.info("Attack completed successfully")
                return True
            else:
                logging.error("Troop deployment failed")
                return False
        elif not nextOpponent():
            logging.error("Next opponent failed")
            return False
    logging.warning("No suitable opponent found after 20 attempts")
    return False   # still on the scouting screen — the caller recovers to home

def nextOpponent() -> bool:
    """Skip to next opponent."""
    logging.debug("Entering nextOpponent")
    try:
        pos = locate_image("misc/next_opponent.png")
        if not pos:
            logging.error("Next opponent button not found")
            return False
        pyautogui.click(pos)
        updateTimestamp('_lastInteraction')
        time.sleep(3)
        
        for i in range(30):
            if locate_image("misc/battle_screen.png"):
                logging.info("Next battle screen detected")
                time.sleep(3)
                return True
            time.sleep(1)
            logging.debug(f"Waiting for next battle screen, attempt {i + 1}/30")
        logging.error("Next battle screen not found after 30 seconds")
        return False
    except Exception as e:
        logging.error(f"Error in nextOpponent: {traceback.format_exc()}")
        return False

def startAttacking():
    """Start the attack by clicking attack -> find_match -> wait for battle screen."""
    logging.debug("Starting attack process")

    # Step 1: Locate and click attack button
    attack_btn = locate_image("misc/attack_button.png")
    if not attack_btn:
        logging.error("Attack button not found")
        return False
    pyautogui.click(attack_btn)
    logging.info("Clicked attack button")
    time.sleep(2)

    # Step 2: Locate and click find_match button (different for ranked mode)
    find_match_btn = None
    find_match_image = "misc/findmatch_ranked.png" if CONFIG['ranked_mode'] else "misc/find_match.png"
    
    for i in range(10):
        find_match_btn = locate_image(find_match_image)
        if find_match_btn:
            break
        if i in (1, 4):
            # The first tap right after returning home is sometimes swallowed
            # (live) — if the home Battle button is still there, tap it again.
            again = locate_image("misc/attack_button.png", retries=1)
            if again:
                logging.warning("Find match screen didn't open — tapping attack button again")
                pyautogui.click(again)
        time.sleep(2)

    if not find_match_btn:
        logging.error(f"Find match button not found ({find_match_image})")
        return False
    pyautogui.click(find_match_btn)
    logging.info(f"Clicked find match button ({'RANKED' if CONFIG['ranked_mode'] else 'NORMAL'} mode)")
    time.sleep(2)

    # Step 3: Click main attack button
    attack_btn = locate_image("misc/attack_button_main.png")
    if not attack_btn:
        logging.error("Attack button main not found")
        return False
    pyautogui.click(attack_btn)
    logging.info("Clicked attack button main")
    time.sleep(1)

    # **RANKED MODE: Handle confirmation popup**
    if CONFIG['ranked_mode']:
        logging.info("Ranked mode: Looking for confirmation attack button")
        ranked_confirm_btn = None
        for i in range(10):
            ranked_confirm_btn = locate_image("misc/attack_confirm_ranked.png")
            if ranked_confirm_btn:
                break
            time.sleep(1)
        
        if not ranked_confirm_btn:
            logging.error("Ranked confirmation attack button not found")
            return False
        
        pyautogui.click(ranked_confirm_btn)
        logging.info("Clicked ranked confirmation attack button")
        time.sleep(2)
    battlescreen_image = "misc/battle_screen_ranked.png" if CONFIG['ranked_mode'] else "misc/battle_screen.png"
    # Step 4: Wait until battle screen appears
    logging.info("Waiting for battle screen...")
    for i in range(CONFIG['battle_wait_timeout']):
        if locate_image(battlescreen_image):
            logging.info("Battle screen detected")
            # Let the battle's opening camera move finish first — a zoom sent
            # during it is swallowed and the base stays zoomed in.
            time.sleep(2)
            zoomOutBattlefield()
            time.sleep(0.5)
            return True
        time.sleep(1)

    logging.error(f"Battle screen not found after {CONFIG['battle_wait_timeout']} seconds")
    return False

# def collectorchecker():
#     gold_collector = locate_image("misc/gold_mine.png")
#     elixir_collector = locate_image("misc/elixir_collector.png")
#     dark_collector = locate_image("misc/de_drill.png")
#     pyautogui.click(gold_collector)
#     pyautogui.click(elixir_collector)
#     pyautogui.click(dark_collector)
# def trainTroops():
#     camp = locate_image("misc/camp.png")
#     if not camp:
#         camp = locate_image("misc/camp1.png")
#     pyautogui.click(camp)
#     time.sleep(1)
#     pyautogui.click(locate_image("misc/train.PNG"))
#     time.sleep(1)
#     pyautogui.click(locate_image("misc/train2.PNG"))
#     time.sleep(1)
#     pyautogui.click(locate_image("misc/train3.PNG"))
#     time.sleep(1)
#     pyautogui.click(locate_image("misc/close_barracks.PNG"))
def capture_battlefield():
    """Captures a specific area on LDPlayer based on defined coordinates."""
    try:
        windows = gw.getWindowsWithTitle("LDPlayer")
        if not windows:
            logging.error("LDPlayer window not found for battlefield capture")
            return None
        game_window = windows[0]
        base_x, base_y = game_window.left, game_window.top

        # Define bounding box based on user-defined coordinates
        min_x, min_y = 257, 36
        max_x, max_y = 1047, 583
        width = max_x - min_x
        height = max_y - min_y

        screenshot = pyautogui.screenshot(region=(base_x + min_x, base_y + min_y, width, height))
        filepath = "misc/battlefield.png"
        screenshot.save(filepath)
        logging.info("Battlefield screenshot saved (custom area)")
        return filepath
    except Exception as e:
        logging.error(f"Failed to capture battlefield: {traceback.format_exc()}")
        return None
    except Exception as e:
        logging.error(f"Failed to capture battlefield: {traceback.format_exc()}")
        return None



def find_defense_positions(template_path, battlefield_image, threshold=0.75):
    """Finds key defense structures using template matching."""
    try:
        img = cv2.imread(battlefield_image)
        template = cv2.imread(template_path, 0)
        gray_img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        res = cv2.matchTemplate(gray_img, template, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= threshold)
        positions = list(zip(*loc[::-1]))  # Convert to list of (x, y)

        logging.info(f"Found {len(positions)} instances of {template_path}")
        return positions
    except Exception as e:
        logging.error(f"Error in find_defense_positions: {traceback.format_exc()}")
        return []

def detect_dead_base():
    """Detects if the base has full collectors => Dead base."""
    battle_img = capture_battlefield()
    
    # COMMENT OUT THESE LINES:
    # gold_mines = find_defense_positions("misc/gold_mine_full.png", battle_img)
    # elixir_collectors = find_defense_positions("misc/elixir_collector_full.png", battle_img)
    
    # Instead, return False or implement different logic
    return False  # Or implement different base detection logic
def isGoodOpponentAdvanced():
    """Just use the regular gold check without structure analysis."""
    logging.debug("Using basic gold evaluation only")
    gold_check = isGoodOpponent()  # Use existing gold threshold logic
    
    if gold_check:
        logging.info("Base approved by gold check")
        return True
    logging.info("Base rejected by gold check")
    return False
# -----------------------------
# Smart Spell Targeting
# -----------------------------
def deploy_rage_smart():
    """Deploy Rage Spell near real-time troop clusters."""
    battle_img = capture_battlefield()
    troop_centers = find_troop_clusters(battle_img)

    spell_icon = locate_image("troops/rage_spell.PNG")
    if spell_icon and troop_centers:
        pyautogui.click(spell_icon)
        time.sleep(0.3)
        for x, y in troop_centers[:2]:  # Limit to 2 Rage placements
            pyautogui.click(x + randint(-10, 10), y + randint(-10, 10))
            time.sleep(0.2)
        logging.info("Smart Rage Spells deployed near troop clusters")


def deploy_freeze_smart():
    """Deploy Freeze Spell on high-value defenses like Inferno Tower."""
    battle_img = capture_battlefield()
    infernos = find_defense_positions("misc/inferno_tower.png", battle_img)
    air_defense = find_defense_positions("misc/air_defense.png", battle_img)

    if infernos:
        spell_icon = locate_image("troops/freez_spell.PNG")
        if spell_icon:
            pyautogui.click(spell_icon)
            time.sleep(0.3)
            for pos in infernos[:3]:  # Freeze up to 3 targets
                x, y = pos
                pyautogui.click(x + randint(-5, 5), y + randint(-5, 5))
                time.sleep(0.2)
            logging.info("Smart Freeze Spells deployed")
        else:
            logging.warning("Freeze spell icon not found")
    else:
        if air_defense:
            spell_icon = locate_image("troops/freez_spell.PNG")
            if spell_icon:
                pyautogui.click(spell_icon)
                time.sleep(0.3)
                for pos in air_defense[:3]:  # Freeze up to 3 targets
                    x, y = pos
                    pyautogui.click(x + randint(-5, 5), y + randint(-5, 5))
                    time.sleep(0.2)
                logging.info("Smart Freeze Spells deployed")
            else:
                logging.warning("Freeze spell icon not found")
        else:
            logging.info("No Inferno Towers found for freezing")
            pyautogui.click(locate_image("troops/freez_spell.PNG"))
            time.sleep(0.5)
            for x, y in freeze_spell_positions:
                pyautogui.click(x + randint(-5, 5), y + randint(-5, 5))
def find_troop_clusters(image_path):
    """Detects blue-colored troop blobs (Electro Dragons / Balloons)."""
    img = cv2.imread(image_path)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    # Adjusted blue range for better accuracy
    lower_blue = np.array([90, 80, 50])
    upper_blue = np.array([130, 255, 255])

    mask = cv2.inRange(hsv, lower_blue, upper_blue)
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.dilate(mask, kernel, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    positions = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area > 100:  # Increased threshold to filter small noise
            x, y, w, h = cv2.boundingRect(cnt)
            center = (x + w // 2, y + h // 2)
            positions.append(center)

    logging.info(f"Detected {len(positions)} troop clusters")
    return positions
def _click_close_image():
    """Search for Close_Btn.png on screen and click it — used by watchdogs."""
    try:
        pos = pyautogui.locateCenterOnScreen("Close_Btn.png", confidence=0.8)
        if pos:
            logging.warning(f"[WATCHDOG] Close button found at {pos} — clicking")
            pyautogui.click(pos)
        else:
            logging.warning("[WATCHDOG] Close_Btn.png not found on screen")
    except Exception:
        logging.warning(f"[WATCHDOG] Error finding close button: {traceback.format_exc()}")


def _update_heartbeat():
    """Signal that the bot is making progress."""
    global _last_heartbeat
    _last_heartbeat = time.time()


def _bot_monitor(stop_event):
    """
    Background thread — runs every 5 seconds while the bot is active.
    Checks two conditions independently:
      1. 'Are you there?' AFK prompt  → click reloadgame.png
      2. No heartbeat for >4 minutes  → click Close_Btn.png to escape stuck screen
    """
    _update_heartbeat()
    while not stop_event.wait(5):   # wakes immediately when stop_event is set
        # ── AFK prompt check ─────────────────────────────────────────────
        try:
            pos = pyautogui.locateCenterOnScreen("misc/areyouthere.png", confidence=0.8)
            if pos:
                logging.warning("[MONITOR] 'Are you there?' detected — restarting game")
                try:
                    # locateCenterOnScreen raises (not returns None) when the
                    # image is missing, which used to skip the click entirely.
                    # misc/reloadgame.png is captured at a larger text size
                    # than the popup uses, so try the 118x26 one too, then
                    # fall back to where "RELOAD GAME" sits inside the
                    # areyouthere.png popup (231px left, 59px below centre).
                    reload_pos = None
                    for img in ("misc/reloadgame.png", "reloadgame.PNG"):
                        try:
                            reload_pos = pyautogui.locateCenterOnScreen(img, confidence=0.8)
                        except Exception:
                            reload_pos = None
                        if reload_pos:
                            break
                    if not reload_pos:
                        reload_pos = (pos[0] - 231, pos[1] + 59)
                    pyautogui.click(reload_pos)
                    time.sleep(15)
                    activate_game_window()
                except Exception:
                    logging.error(f"[MONITOR] reload error: {traceback.format_exc()}")
                _update_heartbeat()
        except Exception:
            pass  # image not found or screenshot error — normal

        # ── Stuck detection (no progress for 4 minutes) ───────────────────
        if _last_heartbeat > 0 and (time.time() - _last_heartbeat) > 240:
            logging.warning("[MONITOR] No heartbeat for >4 min — clicking close button")
            _click_close_image()
            _update_heartbeat()   # reset so we don't spam every 5s


def cleanup_session():
    """Truncate the log file and delete all debug/temp PNG images."""
    log_file = 'coc_bot_detailed.log'

    # Close existing log handlers, clear the file, then re-attach
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        try:
            handler.close()
        except Exception:
            pass
        root_logger.removeHandler(handler)

    try:
        open(log_file, 'w').close()
    except Exception:
        pass

    logging.basicConfig(
        filename=log_file,
        level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - %(funcName)s - %(message)s'
    )

    # Delete debug PNG files produced by wall.py and the gold-region OCR
    deleted = 0
    for pattern in ['debug_*.png', 'ocr_*.png', 'gold_region_*.png']:
        for f in glob.glob(pattern):
            try:
                os.remove(f)
                deleted += 1
            except Exception:
                pass

    logging.info(f"Session cleanup: log cleared, {deleted} debug image(s) removed")


def run_wall_upgrades():
    """
    Attempt to upgrade as many walls as current resources allow.
    Runs silently — results go to the log file only.
    """
    global walls_this_session

    if not WALL_UPGRADE_AVAILABLE:
        logging.warning("Wall upgrade skipped: wall.py not available")
        return

    logging.info("Wall upgrade check triggered")
    # Only a confirmed spend continues the loop. "UPGRADED" (dialog closed
    # but no readable spend) once repeated 5x while a popup was swallowing
    # every Confirm — it still counts, but ends this check.
    CONTINUE_ON = {"UPGRADED_WITH_GOLD", "UPGRADED_WITH_ELIXIR"}
    upgraded = 0
    result = None

    MAX_WALLS_PER_CHECK = 50   # hard stop in case success is ever misdetected

    try:
        _wall.activate_game_window()   # sync wall module's window coords
        for _ in range(MAX_WALLS_PER_CHECK):
            result = _wall.upgrade_wall()
            logging.info(f"Wall upgrade result: {result}")
            if result in CONTINUE_ON or result == "UPGRADED":
                upgraded += 1
                walls_this_session += 1
            if result not in CONTINUE_ON:
                break
    except Exception:
        logging.error(f"Wall upgrade error: {traceback.format_exc()}")

    try:
        # Don't leave the side panel covering the Battle button for the next attack
        _wall.close_builder_panel()
    except Exception:
        logging.warning(f"Could not close builder panel: {traceback.format_exc()}")

    logging.info(f"Wall upgrade session complete — {upgraded} wall(s) upgraded")
    return result   # last upgrade_wall() status, e.g. "WALL_NOT_FOUND" once walls are maxed


def main_loop():
    """Main automation loop."""
    logging.info("Starting Clash of Clans bot")
    while True:
        try:
            attack()
            time.sleep(5)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logging.error(traceback.format_exc())
            time.sleep(5)

if __name__ == "__main__":
    import tkinter as tk
    from datetime import timedelta

    bot_running = False
    start_time = None
    MAX_RUNTIME = timedelta(hours=4)

    bot_thread = None

    def toggle_ranked_mode():
        CONFIG['ranked_mode'] = not CONFIG['ranked_mode']
        ranked_btn.config(
            text=f"Ranked: {'ON' if CONFIG['ranked_mode'] else 'OFF'}",
            bg="orange" if CONFIG['ranked_mode'] else "gray",
        )
        logging.info(f"Ranked mode: {CONFIG['ranked_mode']}")

    def toggle_wall_upgrade():
        CONFIG['wall_upgrade'] = not CONFIG['wall_upgrade']
        wall_btn.config(
            text=f"Wall Upgrade: {'ON' if CONFIG['wall_upgrade'] else 'OFF'}",
            bg="green" if CONFIG['wall_upgrade'] else "gray",
        )
        logging.info(f"Wall upgrade: {CONFIG['wall_upgrade']}")

    def toggle_event_troops():
        CONFIG['event_troops'] = not CONFIG['event_troops']
        event_troop_btn.config(
            text=f"Event Troops: {'ON' if CONFIG['event_troops'] else 'OFF'}",
            bg="purple" if CONFIG['event_troops'] else "gray",
        )
        logging.info(f"Event troops: {CONFIG['event_troops']}")

    def toggle_builder_base():
        CONFIG['builder_base'] = not CONFIG['builder_base']
        builder_base_btn.config(
            text=f"Builder Base: {'ON' if CONFIG['builder_base'] else 'OFF'}",
            bg="brown" if CONFIG['builder_base'] else "gray",
        )
        logging.info(f"Builder base mode: {CONFIG['builder_base']}")

    def toggle_ai_deploy():
        CONFIG['ai_deploy'] = not CONFIG['ai_deploy']
        ai_deploy_btn.config(
            text=f"AI Deploy: {'ON' if CONFIG['ai_deploy'] else 'OFF'}",
            bg="#0af" if CONFIG['ai_deploy'] else "gray",
        )
        logging.info(f"AI Deploy: {CONFIG['ai_deploy']}")

    def open_settings_window():
        """All the configurable variables live in a separate popup now,
        opened via the gear button — keeps the main window to just the
        toggles/status/controls. A single 'Save & Exit' button validates and
        applies every field, then closes the popup; on a bad value it stays
        open and shows what needs fixing instead of closing."""
        win = tk.Toplevel(root)
        win.title("Settings")
        win.attributes("-topmost", True)
        win.configure(bg="black")
        win.resizable(False, False)
        win.geometry(f"+{root.winfo_x() - 190}+{root.winfo_y()}")
        win.transient(root)
        win.grab_set()

        tk.Label(win, text="⚙ Settings", fg="white", bg="black",
                 font=("Arial", 11, "bold")).pack(pady=(10, 6))

        def _row(label, default):
            row = tk.Frame(win, bg="black")
            row.pack(fill=tk.X, padx=10, pady=3)
            tk.Label(row, text=label, fg="white", bg="black",
                     font=("Arial", 9), width=16, anchor="w").pack(side=tk.LEFT)
            var = tk.StringVar(value=str(default))
            tk.Entry(row, textvariable=var, font=("Arial", 9),
                     bg="#222", fg="white", insertbackground="white",
                     relief="flat", width=10).pack(side=tk.RIGHT)
            return var

        wall_price_var  = _row("Wall Price", _wall.WALL_PRICE if WALL_UPGRADE_AVAILABLE else 2_400_000)
        min_gold_var    = _row("Min Gold Atk", CONFIG['min_gold_to_attack'])
        check_every_var = _row("Wall Check Every(N)", CONFIG['wall_check_every'])
        event_count_var = _row("Event Troops#", CONFIG['event_troop_count'])
        cart_every_var  = _row("Cart Check Every(N)", CONFIG['builder_cart_check_every'])

        err_lbl = tk.Label(win, text="", fg="#f55", bg="black",
                           font=("Arial", 8), wraplength=210, justify="left")
        err_lbl.pack(padx=10, pady=(4, 0))

        def save_and_exit():
            errors = []
            try:
                wp = int(wall_price_var.get().replace(",", "").replace("_", ""))
                if WALL_UPGRADE_AVAILABLE:
                    _wall.WALL_PRICE = wp
            except ValueError:
                errors.append("Wall Price")

            try:
                mg = int(min_gold_var.get().replace(",", "").replace("_", ""))
                CONFIG['min_gold_to_attack'] = mg
            except ValueError:
                errors.append("Min Gold")

            try:
                ce = int(check_every_var.get())
                CONFIG['wall_check_every'] = max(1, ce)
            except ValueError:
                errors.append("Wall Check Every")

            try:
                ec = int(event_count_var.get())
                CONFIG['event_troop_count'] = max(1, ec)
            except ValueError:
                errors.append("Event Troops#")

            try:
                cce = int(cart_every_var.get())
                CONFIG['builder_cart_check_every'] = max(1, cce)
            except ValueError:
                errors.append("Cart Check Every")

            if errors:
                err_lbl.config(text=f"Bad value: {', '.join(errors)}")
                return

            logging.info(f"Settings saved — WallPrice={_wall.WALL_PRICE if WALL_UPGRADE_AVAILABLE else 'N/A'} "
                         f"MinGold={CONFIG['min_gold_to_attack']} WallCheckEvery={CONFIG['wall_check_every']} "
                         f"EventTroopCount={CONFIG['event_troop_count']} "
                         f"CartCheckEvery={CONFIG['builder_cart_check_every']}")
            win.destroy()

        tk.Button(win, text="Save & Exit", command=save_and_exit,
                  bg="#2a7", fg="white", font=("Arial", 9, "bold")).pack(fill=tk.X, padx=10, pady=(8, 12))

    def start_bot():
        global bot_running, bot_thread, start_time, _monitor_stop_evt
        if not bot_running:
            bot_running = True
            start_time = datetime.now()
            status_label.config(text="Status: Running ✅", fg="green")
            update_timer()
            # Start background monitor thread
            _monitor_stop_evt = threading.Event()
            threading.Thread(target=_bot_monitor, args=(_monitor_stop_evt,),
                             daemon=True, name="BotMonitor").start()
            bot_thread = threading.Thread(target=main_loop_wrapper, daemon=True)
            bot_thread.start()
            logging.info("Bot started from GUI")
            root.iconify()


    def stop_bot():
        global bot_running, _monitor_stop_evt
        bot_running = False
        if _monitor_stop_evt:
            _monitor_stop_evt.set()
            _monitor_stop_evt = None
        status_label.config(text="Status: Stopped ⛔", fg="red")
        timer_label.config(text="Elapsed: 00:00:00")
        logging.info("Bot stop requested from GUI")

    def update_timer():
        if bot_running and start_time:
            elapsed = datetime.now() - start_time
            timer_label.config(text=f"Elapsed: {str(elapsed).split('.')[0]}")
            root.after(1000, update_timer)
        else:
            timer_label.config(text="Elapsed: 00:00:00")


    def restart_bot():
        stop_bot()
        time.sleep(1)
        start_bot()
        root.iconify()

    def main_loop_wrapper():
        global attack_count, walls_this_session
        cleanup_session()
        attack_count = 0
        walls_this_session = 0
        logging.info("Main loop wrapper started")

        # Initial wall upgrade check before first attack
        if CONFIG['wall_upgrade']:
            logging.info("Initial wall upgrade check at session start")
            run_wall_upgrades()

        # Initial Builder Base elixir cart check before the first attack
        if CONFIG['builder_base']:
            logging.info("Initial Builder Base elixir cart check at session start")
            collectBuilderElixirCart()

        session_start = datetime.now()
        while bot_running:
            try:
                if datetime.now() - session_start > MAX_RUNTIME:
                    logging.info("Max runtime of 4 hours reached. Stopping bot.")
                    stop_bot()
                    break

                _update_heartbeat()
                success = attack()
                _update_heartbeat()

                if success:
                    attack_count += 1
                    logging.info(f"Successful attacks this session: {attack_count}")

                    # Wall upgrade check every N successful attacks
                    n = CONFIG['wall_check_every']
                    if (CONFIG['wall_upgrade'] and n > 0 and attack_count % n == 0):
                        logging.info(f"Attack #{attack_count}: triggering wall upgrade check")
                        run_wall_upgrades()
                        _update_heartbeat()

                    # Builder Base elixir cart check every N successful attacks
                    cn = CONFIG['builder_cart_check_every']
                    if (CONFIG['builder_base'] and cn > 0 and attack_count % cn == 0):
                        logging.info(f"Attack #{attack_count}: checking Builder Base elixir cart")
                        collectBuilderElixirCart()
                        _update_heartbeat()
                elif not CONFIG['builder_base']:
                    # A failed attack leaves the bot on whatever screen it
                    # stopped at; without this every later attempt fails too.
                    recover_to_home()
                    _update_heartbeat()

                time.sleep(30)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logging.error(traceback.format_exc())
                time.sleep(60)

    # GUI Setup
    root = tk.Tk()
    root.title("C")
    root.attributes("-topmost", True)
    root.geometry("140x335+1220+10")
    root.resizable(False, False)
    root.configure(bg="black")

    # ── Status / timer ────────────────────────────────────────────────────
    status_label = tk.Label(root, text="Status: Stopped", fg="red", bg="black",
                            font=("Arial", 10, "bold"))
    status_label.pack(pady=2)

    timer_label = tk.Label(root, text="Elapsed: 00:00:00", fg="yellow", bg="black",
                           font=("Arial", 10))
    timer_label.pack(pady=2)

    wall_count_label = tk.Label(root, text="Walls Upgraded: 0", fg="cyan", bg="black",
                                font=("Arial", 9))
    wall_count_label.pack(pady=1)

    def _refresh_wall_count():
        wall_count_label.config(text=f"Walls Upgraded: {walls_this_session}")
        root.after(2000, _refresh_wall_count)

    _refresh_wall_count()

    # ── Toggle buttons ────────────────────────────────────────────────────
    ranked_btn = tk.Button(root, text="Ranked: OFF", command=toggle_ranked_mode,
                           bg="gray", fg="white", font=("Arial", 9, "bold"))
    ranked_btn.pack(fill=tk.X, padx=4, pady=1)

    wall_btn = tk.Button(root, text="Wall Upgrade: OFF", command=toggle_wall_upgrade,
                         bg="gray", fg="white", font=("Arial", 9, "bold"))
    wall_btn.pack(fill=tk.X, padx=4, pady=1)

    event_troop_btn = tk.Button(root, text="Event Troops: OFF", command=toggle_event_troops,
                                bg="gray", fg="white", font=("Arial", 9, "bold"))
    event_troop_btn.pack(fill=tk.X, padx=4, pady=1)

    builder_base_btn = tk.Button(root, text="Builder Base: OFF", command=toggle_builder_base,
                                 bg="gray", fg="white", font=("Arial", 9, "bold"))
    builder_base_btn.pack(fill=tk.X, padx=4, pady=1)

    ai_deploy_btn = tk.Button(root, text="AI Deploy: OFF", command=toggle_ai_deploy,
                              bg="gray", fg="white", font=("Arial", 9, "bold"))
    ai_deploy_btn.pack(fill=tk.X, padx=4, pady=1)

    # ── Settings (gear) button — opens the popup with all configurable
    # variables, replacing the old always-visible inline settings block ────
    sep = tk.Frame(root, height=1, bg="#444")
    sep.pack(fill=tk.X, padx=4, pady=4)

    settings_btn = tk.Button(root, text="⚙ Settings", command=open_settings_window,
                             bg="#333", fg="white", font=("Arial", 9, "bold"))
    settings_btn.pack(fill=tk.X, padx=4, pady=1)

    sep2 = tk.Frame(root, height=1, bg="#444")
    sep2.pack(fill=tk.X, padx=4, pady=3)

    # ── Control buttons ───────────────────────────────────────────────────
    tk.Button(root, text="Start",   command=start_bot,   bg="green", fg="white").pack(fill=tk.X, padx=4, pady=1)
    tk.Button(root, text="Stop",    command=stop_bot,    bg="red",   fg="white").pack(fill=tk.X, padx=4, pady=1)
    tk.Button(root, text="Restart", command=restart_bot, bg="blue",  fg="white").pack(fill=tk.X, padx=4, pady=1)

    root.mainloop()