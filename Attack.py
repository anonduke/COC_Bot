from datetime import datetime
from random import randint
import time
import logging
import pyautogui
import pygetwindow as gw
from typing import Dict
import traceback
import cv2
import pytesseract
import numpy as np
import os
import threading
import tkinter as tk
from datetime import timedelta

# ================================
# SHORT LOGGING CONFIGURATION
# ================================
logging.basicConfig(
    filename='coc_bot.log',
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s'
)

# -----------------------------
# PYTESSERACT PATH
# -----------------------------
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# -----------------------------
# GLOBALS
# -----------------------------
WINDOW_X, WINDOW_Y = 0, 0
timestamps: Dict[str, datetime] = {
    'start': None,
    '_lastInteraction': None
}

# -----------------------------
# CONFIG
# -----------------------------
CONFIG = {
    'goblin_attack': 0,
    'min_gold_to_attack': 100000,
    'max_attack_attempts': 20,
    'battle_wait_timeout': 30,
    'retry_attempts': 3,
    'retry_delay': 2,
}

# ================================================
# WALL UPGRADE ENGINE (FULL)
# ================================================
MAX_STORAGE = 29_000_000          # Your account limit
WALL_COST = 8_000_000             # 8M per wall

# Relative offsets (LDPlayer)
GOLD_OFFSET   = (1019, 59, 107, 27)
ELIXIR_OFFSET = (1005, 121, 124, 28)

UPGRADE_PANEL_OFFSET = (631, 76)
SCROLL_POINT_OFFSET  = (683, 516)
WALL_ENTRY_OFFSET    = (674, 532)

UPGRADE_USING_GOLD   = (738, 566)
UPGRADE_USING_ELIXIR = (879, 564)

# Wall text region (ABSOLUTE)
WALL_TEXT_REGION = (521, 515, 96, 37)


def get_ld_window():
    wins = gw.getWindowsWithTitle("LDPlayer")
    return wins[0] if wins else None


def rel_to_abs(offset):
    win = get_ld_window()
    if win is None:
        return None
    bx, by = win.left, win.top

    if len(offset) == 2:
        return (bx + offset[0], by + offset[1])
    else:
        return (bx + offset[0], by + offset[1], offset[2], offset[3])


def ocr_storage(offset, debug_name):
    """Reads gold/elixir storage with full OCR + safety logic."""
    region = rel_to_abs(offset)
    if region is None:
        logging.error("LDPlayer window not found for storage OCR")
        return 0

    x, y, w, h = region
    img = pyautogui.screenshot(region=(x, y, w, h))
    img.save(debug_name)

    # Preprocess
    img = cv2.cvtColor(np.array(img), cv2.COLOR_BGR2GRAY)
    img = cv2.resize(img, None, fx=3, fy=3, interpolation=cv2.INTER_LINEAR)
    img = cv2.bilateralFilter(img, 9, 75, 75)
    _, img = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    raw = pytesseract.image_to_string(
        img, config="--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789 "
    )

    digits = ''.join(c for c in raw if c.isdigit())

    if digits == "":
        return 0

    # Remove inserted “11”
    while "11" in digits and len(digits) > 7:
        digits = digits.replace("11", "1")

    # Max 8 digits
    if len(digits) > 8:
        digits = digits[-8:]

    value = int(digits)

    # Safety rule → overshoot means OCR failed
    if value > MAX_STORAGE:
        return 0

    return value


def detect_wall_text():
    """Detects if the text 'wall' exists in the upgrade list."""
    x, y, w, h = WALL_TEXT_REGION
    img = pyautogui.screenshot(region=(x, y, w, h))
    img.save("wall_text_debug.png")

    gray = cv2.cvtColor(np.array(img), cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
    text = pytesseract.image_to_string(th).lower()

    return "wall" in text


def click_rel(offset):
    px, py = rel_to_abs(offset)
    pyautogui.click(px, py)


def auto_wall_upgrade():
    """Main wall upgrade logic."""
    try:
        win = get_ld_window()
        if win is None:
            logging.error("LDPlayer not found — skipping wall upgrade")
            return

        # Read storage
        gold   = ocr_storage(GOLD_OFFSET,   "gold_debug.png")
        elixir = ocr_storage(ELIXIR_OFFSET, "elixir_debug.png")

        if gold < WALL_COST and elixir < WALL_COST:
            return  # not enough

        logging.info(f"[WALL] Triggered (Gold={gold}, Elixir={elixir})")

        # Open upgrade panel
        click_rel(UPGRADE_PANEL_OFFSET)
        time.sleep(0.5)

        # Scroll list
        sx, sy = rel_to_abs(SCROLL_POINT_OFFSET)
        pyautogui.moveTo(sx, sy)
        for _ in range(6):
            pyautogui.scroll(-600)
            time.sleep(0.2)
        time.sleep(0.6)

        # Detect wall
        if not detect_wall_text():
            logging.error("[WALL] Wall entry NOT found")
            return

        # Click wall
        click_rel(WALL_ENTRY_OFFSET)
        time.sleep(0.3)

        # Walls possible
        walls_gold   = gold   // WALL_COST
        walls_elixir = elixir // WALL_COST
        total = walls_gold + walls_elixir

        logging.info(f"[WALL] Upgrading {total} walls (G:{walls_gold}, E:{walls_elixir})")

        # Gold upgrades
        for _ in range(walls_gold):
            click_rel(UPGRADE_USING_GOLD)
            time.sleep(0.25)

        # Elixir upgrades
        for _ in range(walls_elixir):
            click_rel(UPGRADE_USING_ELIXIR)
            time.sleep(0.25)

        logging.info("[WALL] Done")

    except:
        logging.error("[WALL] ERROR: " + traceback.format_exc())


# ================================================
# ZOOM OUT FEATURE
# ================================================
def zoom_out():
    """Zoom out inside LDPlayer 1600x900."""
    try:
        win = get_ld_window()
        if not win:
            logging.error("LDPlayer not found for zoom")
            return False

        win.activate()
        win.restore()
        time.sleep(0.5)

        cx = win.left + 800
        cy = win.top + 450

        pyautogui.moveTo(cx, cy, duration=0.2)
        pyautogui.keyDown("ctrl")
        for _ in range(7):
            pyautogui.scroll(-600)
            time.sleep(0.15)
        pyautogui.keyUp("ctrl")

        logging.info("Zoom out complete")
        return True

    except:
        logging.error("Zoom out failed: " + traceback.format_exc())
        return False


# ================================================
# WINDOW ACTIVATION
# ================================================
def activate_game_window():
    global WINDOW_X, WINDOW_Y

    try:
        win = get_ld_window()
        if not win:
            logging.error("LDPlayer not found")
            return False

        win.activate()
        win.restore()

        WINDOW_X, WINDOW_Y = win.left, win.top
        return True

    except:
        logging.error("Failed activating LDPlayer: " + traceback.format_exc())
        return False
# ============================================================
#              IMAGE LOCATOR (SHORT LOG)
# ============================================================
def locate_image(image_path, confidence=0.8, retries=CONFIG['retry_attempts']):
    for _ in range(retries):
        try:
            pos = pyautogui.locateCenterOnScreen(image_path, confidence=confidence)
            if pos:
                return pos
            time.sleep(CONFIG['retry_delay'])
        except:
            pass
    return None


# ============================================================
#           OPPONENT GOLD OCR (EXISTING)
# ============================================================
def recognize_numbers_from_region(region):
    try:
        screenshot = pyautogui.screenshot(region=region)
        gray = cv2.cvtColor(np.array(screenshot), cv2.COLOR_BGR2GRAY)
        _, th = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
        raw = pytesseract.image_to_string(th, config="--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789")
        digits = ''.join(c for c in raw if c.isdigit())
        return int(digits) if digits else 0
    except:
        return 0


def isGoodOpponent():
    region = (WINDOW_X + 60, WINDOW_Y + 125, 125, 25)
    gold = recognize_numbers_from_region(region)
    return gold >= CONFIG['min_gold_to_attack']


# ============================================================
#            DEAD BASE DETECTION
# ============================================================
def capture_battlefield():
    try:
        win = get_ld_window()
        if not win:
            return None
        bx, by = win.left, win.top

        # Fixed bounds
        min_x, min_y = 257, 36
        max_x, max_y = 1047, 583
        w = max_x - min_x
        h = max_y - min_y

        img = pyautogui.screenshot(region=(bx + min_x, by + min_y, w, h))
        path = "battlefield.png"
        img.save(path)
        return path

    except:
        return None


def find_defense_positions(template, battlefield_image, threshold=0.75):
    try:
        img = cv2.imread(battlefield_image)
        template_img = cv2.imread(template, 0)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        res = cv2.matchTemplate(gray, template_img, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= threshold)
        return list(zip(*loc[::-1]))
    except:
        return []


def detect_dead_base():
    bf = capture_battlefield()
    if not bf:
        return False

    gold_mines = find_defense_positions("misc/gold_mine_full.png", bf)
    elixir_collectors = find_defense_positions("misc/elixir_collector_full.png", bf)

    return len(gold_mines) >= 4 or len(elixir_collectors) >= 4


def isGoodOpponentAdvanced():
    return isGoodOpponent()


# ============================================================
#            FINISH BATTLE
# ============================================================
def finishBattleAndGoHome():
    while True:
        pos = locate_image("misc/end_battle.png")
        if pos:
            pyautogui.click(pos)
            time.sleep(4)
            return True
        time.sleep(1)


# ============================================================
#            TROOP DEPLOYMENT (UNCHANGED)
# ============================================================
# !!! YOUR ORIGINAL TROOP LOGIC IS KEPT EXACT !!!
# (NOT REPEATED HERE — paste your troop code BELOW this comment)
# ============================================================
#            START ATTACK FLOW
# ============================================================
def startAttacking():
    attack_btn = locate_image("misc/attack_button.png")
    if not attack_btn:
        return False

    pyautogui.click(attack_btn)
    time.sleep(1)

    # find match
    for _ in range(10):
        fm = locate_image("misc/find_match.png")
        if fm:
            pyautogui.click(fm)
            break
        time.sleep(1)

    time.sleep(2)

    main_attack_btn = locate_image("misc/attack_button_main.png")
    if not main_attack_btn:
        return False

    pyautogui.click(main_attack_btn)
    time.sleep(1)

    # wait battle screen
    for _ in range(CONFIG['battle_wait_timeout']):
        if locate_image("misc/battle_screen.png"):
            return True
        time.sleep(1)

    return False


# ============================================================
#                   ATTACK() — PATCHED
# ============================================================
def attack():
    # AUTO WALL UPGRADE FIRST
    try:
        if auto_wall_upgrade_var.get():
            auto_wall_upgrade()
    except:
        pass

    if not activate_game_window():
        return False

    # AUTO ZOOM
    try:
        if auto_zoomout_var.get():
            zoom_out()
    except:
        pass

    # collect resources
    try:
        col1 = locate_image("misc/gold_mine.png")
        col2 = locate_image("misc/elixir_collector.png")
        col3 = locate_image("misc/de_drill.png")
        for c in (col1, col2, col3):
            if c:
                pyautogui.click(c)
    except:
        pass

    # begin match
    if not startAttacking():
        return False

    # 20 opponent tries
    for _ in range(20):
        if isGoodOpponentAdvanced():
            if deployTroops():
                finishBattleAndGoHome()
                return True
            else:
                return False
        else:
            nxt = locate_image("misc/next_opponent.png")
            if nxt:
                pyautogui.click(nxt)
                time.sleep(3)

    return True


# ============================================================
#         MAIN LOOP (UNCHANGED BUT CLEAN)
# ============================================================
def main_loop():
    while True:
        try:
            attack()
            time.sleep(30)
        except KeyboardInterrupt:
            break
        except:
            time.sleep(60)
# ============================================================
#                   MAIN LOOP WRAPPER
# ============================================================

import threading
import tkinter as tk
from datetime import timedelta

bot_running = False
start_time = None
MAX_RUNTIME = timedelta(hours=4)
bot_thread = None


def start_bot():
    global bot_running, bot_thread, start_time
    if not bot_running:
        bot_running = True
        start_time = datetime.now()
        status_label.config(text="Status: Running", fg="green")
        update_timer()
        bot_thread = threading.Thread(target=main_loop_wrapper, daemon=True)
        bot_thread.start()


def stop_bot():
    global bot_running
    bot_running = False
    status_label.config(text="Status: Stopped", fg="red")
    timer_label.config(text="Elapsed: 00:00:00")


def update_timer():
    if bot_running and start_time:
        elapsed = datetime.now() - start_time
        timer_label.config(text=f"Elapsed: {str(elapsed).split('.')[0]}")
        root.after(1000, update_timer)


def restart_bot():
    stop_bot()
    time.sleep(1)
    start_bot()


def main_loop_wrapper():
    global bot_running

    start_t = datetime.now()

    while bot_running:

        # Stop automatically if runtime exceeds
        if datetime.now() - start_t > MAX_RUNTIME:
            stop_bot()
            break

        try:
            attack()
            time.sleep(30)
        except:
            time.sleep(60)


# ============================================================
#                        GUI SETUP
# ============================================================

root = tk.Tk()
root.title("Clash Bot Control")
root.attributes("-topmost", True)
root.geometry("200x170+1200+10")
root.resizable(False, False)
root.configure(bg="black")

# STATUS
status_label = tk.Label(root, text="Status: Stopped", fg="red", bg="black", font=("Arial", 10, "bold"))
status_label.pack(pady=2)

# TIMER
timer_label = tk.Label(root, text="Elapsed: 00:00:00", fg="yellow", bg="black", font=("Arial", 10))
timer_label.pack(pady=2)

# AUTO ZOOMOUT CHECKBOX
auto_zoomout_var = tk.BooleanVar(value=True)
zoom_chk = tk.Checkbutton(root, text="Auto Zoomout", variable=auto_zoomout_var,
                          bg="black", fg="white", selectcolor="black")
zoom_chk.pack(pady=2)

# AUTO WALL UPGRADE CHECKBOX (NEW)
auto_wall_upgrade_var = tk.BooleanVar(value=True)
wall_chk = tk.Checkbutton(root, text="Auto Wall Upgrade", variable=auto_wall_upgrade_var,
                          bg="black", fg="white", selectcolor="black")
wall_chk.pack(pady=2)

# BUTTONS
tk.Button(root, text="Start", command=start_bot, bg="green", fg="white").pack(fill=tk.X, pady=1)
tk.Button(root, text="Stop", command=stop_bot, bg="red", fg="white").pack(fill=tk.X, pady=1)
tk.Button(root, text="Restart", command=restart_bot, bg="blue", fg="white").pack(fill=tk.X, pady=1)

root.mainloop()
