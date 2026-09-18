from datetime import datetime, timedelta
from random import randint
import time
import logging
import pyautogui
import pygetwindow as gw
import traceback
import cv2
import pytesseract
import numpy as np
import os
import threading
import tkinter as tk

# =========================
# TESSERACT CONFIG (WINDOWS)
# =========================
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# =========================
# LOGGING
# =========================
logging.basicConfig(
    filename="coc_bot.log",
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# =========================
# GLOBAL STATE
# =========================
WINDOW_X, WINDOW_Y = 0, 0
gold_region_files = []
bot_running = False
start_time = None
MAX_RUNTIME = timedelta(hours=4)

# =========================
# CONFIG
# =========================
CONFIG = {
    "min_gold_to_attack": 100000,
    "max_attack_attempts": 20,
    "battle_wait_timeout": 30,
    "gold_region": (60, 125, 125, 25),
    "retry_attempts": 3,
    "retry_delay": 2,
}

# =========================
# DEPLOYMENT COORDINATES
# =========================
goblins_positions = [(509,305),(242,327),(263,320),(283,308),(308,293),(328,277),(346,260)]
electro_dragon_positions = [(342,260),(362,245),(377,234),(395,223)]
balloon_positions = [(342,260),(362,245),(377,234)]
rage_spell_positions = [(502,297),(632,207),(581,407),(702,314)]
freeze_spell_positions = [(595,409),(687,341),(760,273)]

archer_queen_position = (207,353)
king_position = (347,489)
warden_position = (395,223)

# =========================
# HELPERS
# =========================
def safe_click(pos, delay=0.3):
    if pos:
        pyautogui.click(pos)
        time.sleep(delay)
        return True
    return False

def locate_image(path, confidence=0.8):
    for _ in range(CONFIG["retry_attempts"]):
        try:
            pos = pyautogui.locateCenterOnScreen(path, confidence=confidence)
            if pos:
                return pos
        except Exception:
            pass
        time.sleep(CONFIG["retry_delay"])
    return None

def cleanup_temp_files():
    for f in gold_region_files:
        try:
            os.remove(f)
        except Exception:
            pass
    gold_region_files.clear()

# =========================
# WINDOW CONTROL
# =========================
def activate_game_window():
    global WINDOW_X, WINDOW_Y
    try:
        windows = gw.getWindowsWithTitle("LDPlayer")
        if not windows:
            logging.error("LDPlayer window not found")
            return False
        w = windows[0]
        w.activate()
        w.restore()
        WINDOW_X, WINDOW_Y = w.left, w.top
        time.sleep(1)
        return True
    except Exception:
        logging.error(traceback.format_exc())
        return False

# =========================
# OCR
# =========================
def recognize_numbers_from_region(region):
    try:
        img = pyautogui.screenshot(region=region)
        fname = f"gold_{datetime.now():%H%M%S}.png"
        img.save(fname)
        gold_region_files.append(fname)

        gray = cv2.cvtColor(np.array(img), cv2.COLOR_BGR2GRAY)
        _, gray = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)

        text = pytesseract.image_to_string(
            gray, config="--psm 6 -c tessedit_char_whitelist=0123456789"
        )

        digits = "".join(filter(str.isdigit, text))
        return int(digits) if digits else 0
    except Exception:
        logging.error(traceback.format_exc())
        return 0

def isGoodOpponent():
    region = (
        WINDOW_X + CONFIG["gold_region"][0],
        WINDOW_Y + CONFIG["gold_region"][1],
        CONFIG["gold_region"][2],
        CONFIG["gold_region"][3]
    )
    gold = recognize_numbers_from_region(region)
    logging.info(f"Gold detected: {gold}")
    return gold >= CONFIG["min_gold_to_attack"]

# =========================
# ATTACK FLOW
# =========================
def startAttacking():
    if not safe_click(locate_image("misc/attack_button.png")):
        return False
    if not safe_click(locate_image("misc/find_match.png")):
        return False
    if not safe_click(locate_image("misc/attack_button_main.png")):
        return False

    for _ in range(CONFIG["battle_wait_timeout"]):
        if locate_image("misc/battle_screen.png"):
            return True
        time.sleep(1)
    return False

def nextOpponent():
    if not safe_click(locate_image("misc/next_opponent.png")):
        return False
    time.sleep(3)
    return True

def finishBattleAndGoHome():
    safe_click(locate_image("misc/end_battle.png"))
    time.sleep(3)

def deployTroops():
    if not safe_click(locate_image("heros/archer_queen.PNG")):
        return False
    pyautogui.click(archer_queen_position)
    time.sleep(0.5)

    safe_click(locate_image("heros/king.PNG"))
    pyautogui.click(king_position)

    safe_click(locate_image("troops/troops_electro.png"))
    for p in electro_dragon_positions:
        pyautogui.click(p)

    safe_click(locate_image("troops/troops_loon.png"))
    for p in balloon_positions:
        pyautogui.click(p)

    safe_click(locate_image("troops/rage_spell.PNG"))
    for p in rage_spell_positions:
        pyautogui.click(p)

    safe_click(locate_image("troops/freez_spell.PNG"))
    for p in freeze_spell_positions:
        pyautogui.click(p)

    return True

def attack():
    if not activate_game_window():
        return False
    if not startAttacking():
        return False

    for _ in range(CONFIG["max_attack_attempts"]):
        if not bot_running:
            return False

        if isGoodOpponent():
            deployTroops()
            time.sleep(60)
            finishBattleAndGoHome()
            cleanup_temp_files()
            return True

        nextOpponent()
    return False

# =========================
# MAIN LOOP
# =========================
def main_loop():
    global start_time
    start_time = datetime.now()
    while bot_running:
        if datetime.now() - start_time > MAX_RUNTIME:
            stop_bot()
            break
        try:
            attack()
            time.sleep(10)
        except Exception:
            logging.error(traceback.format_exc())
            time.sleep(10)

# =========================
# GUI
# =========================
def start_bot():
    global bot_running
    if not bot_running:
        bot_running = True
        threading.Thread(target=main_loop, daemon=True).start()
        status_label.config(text="Running", fg="green")

def stop_bot():
    global bot_running
    bot_running = False
    status_label.config(text="Stopped", fg="red")

root = tk.Tk()
root.title("Clash Bot")
root.geometry("200x120")
root.attributes("-topmost", True)

status_label = tk.Label(root, text="Stopped", fg="red")
status_label.pack(pady=5)

tk.Button(root, text="Start", command=start_bot).pack(fill="x")
tk.Button(root, text="Stop", command=stop_bot).pack(fill="x")

root.mainloop()
