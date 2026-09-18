# =========================================================
# ===================== IMPORTS ===========================
# =========================================================
from datetime import datetime, timedelta
from random import randint
import random
import math
import time
import logging
import threading
import traceback
from typing import Dict

import pyautogui
import pygetwindow as gw
import pytesseract
import tkinter as tk

# =========================================================
# ===================== TESSERACT =========================
# =========================================================
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# =========================================================
# ===================== LOGGING ===========================
# =========================================================
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(funcName)s - %(message)s",
    handlers=[
        logging.FileHandler("coc_bot_detailed.log"),
        logging.StreamHandler()
    ]
)

# =========================================================
# ===================== CONFIG ============================
# =========================================================
CONFIG = {
    "retry_attempts": 3,
    "retry_delay": 2,
    "battle_wait_timeout": 30,
}

# =========================================================
# ===================== GLOBAL STATE ======================
# =========================================================
WINDOW_X, WINDOW_Y = 0, 0

timestamps: Dict[str, datetime] = {
    "start": None,
    "collectResources": None,
    "_lastInteraction": None,
}

# =========================================================
# ===================== DEPLOY POSITIONS =================
# =========================================================
archer_positions = [
    (41, 363), (81, 334), (40, 358),
    (66, 333), (57, 351), (71, 330),
    (71, 330), (83, 345), (90, 332),
]

giant_positions = [
    (71, 330)
]

hero_position = (40, 367)

# =========================================================
# ===================== BAN-SAFE HELPERS ==================
# =========================================================
def human_delay(min_s=0.12, max_s=0.45):
    delay = random.uniform(min_s, max_s)
    logging.debug(f"Human delay: {delay:.2f}s")
    time.sleep(delay)


def human_pause(min_s=0.8, max_s=2.2):
    pause = random.uniform(min_s, max_s)
    logging.debug(f"Human pause: {pause:.2f}s")
    time.sleep(pause)


def human_move(x, y, duration_range=(0.15, 0.35)):
    logging.debug(f"Moving mouse to ({x}, {y})")
    sx, sy = pyautogui.position()
    steps = random.randint(8, 15)
    duration = random.uniform(*duration_range)

    for i in range(steps):
        t = i / steps
        curve = math.sin(t * math.pi)
        nx = int(sx + (x - sx) * t + random.randint(-2, 2) * curve)
        ny = int(sy + (y - sy) * t + random.randint(-2, 2) * curve)
        pyautogui.moveTo(nx, ny, duration=duration / steps)


def human_click(x, y):
    logging.debug(f"Clicking at ({x}, {y})")
    human_move(x, y)
    human_delay(0.05, 0.15)
    pyautogui.click()
    human_delay(0.1, 0.25)

# =========================================================
# ===================== CORE HELPERS ======================
# =========================================================
def updateTimestamp(key):
    if key in timestamps:
        timestamps[key] = datetime.now()
        logging.debug(f"Updated timestamp for '{key}'")


def locate_image(image, confidence=0.8, retries=CONFIG["retry_attempts"]):
    logging.info(f"Searching for image: {image} (confidence: {confidence})")
    for attempt in range(retries):
        try:
            logging.debug(f"Attempt {attempt + 1}/{retries}")
            pos = pyautogui.locateCenterOnScreen(image, confidence=confidence)
            if pos:
                logging.info(f"✓ Found '{image}' at {pos}")
                return pos
            logging.debug(f"Image not found, waiting {CONFIG['retry_delay']}s")
            time.sleep(CONFIG["retry_delay"])
        except Exception as e:
            logging.error(f"Error locating image '{image}': {str(e)}")
            logging.error(traceback.format_exc())
    logging.warning(f"✗ Failed to locate '{image}' after {retries} attempts")
    return None


def activate_game_window():
    global WINDOW_X, WINDOW_Y
    logging.info("Attempting to activate game window")
    try:
        windows = gw.getWindowsWithTitle("LDPlayer")
        if not windows:
            logging.error("✗ LDPlayer window not found")
            return False

        w = windows[0]
        logging.info(f"Found LDPlayer window: {w.title}")
        w.restore()
        w.activate()
        WINDOW_X, WINDOW_Y = w.left, w.top
        logging.info(f"✓ Window activated at position ({WINDOW_X}, {WINDOW_Y})")
        return True
    except Exception as e:
        logging.error(f"✗ Failed to activate window: {str(e)}")
        logging.error(traceback.format_exc())
        return False

# =========================================================
# ===================== ATTACK FLOW =======================
# =========================================================
def startAttacking():
    logging.info("=== Starting Attack Sequence ===")
    
    logging.info("Step 1: Looking for attack button")
    attack_btn = locate_image("misc/b_attack.png")
    if not attack_btn:
        logging.error("✗ Attack button not found")
        return False
    human_click(*attack_btn)
    logging.info("✓ Attack button clicked")
    human_pause()

    logging.info("Step 2: Looking for find match button")
    find_match = locate_image("misc/b_find.png")
    if not find_match:
        logging.error("✗ Find match button not found")
        return False
    human_click(*find_match)
    logging.info("✓ Find match button clicked")
    human_pause(1.2, 2.5)

    logging.info("Step 3: Waiting for battle screen")
    for i in range(CONFIG["battle_wait_timeout"]):
        logging.debug(f"Checking for battle screen... ({i+1}/{CONFIG['battle_wait_timeout']})")
        if locate_image("misc/b_battle.png"):
            logging.info("✓ Battle screen detected!")
            return True
        time.sleep(1)

    logging.error("✗ Battle screen timeout")
    return False


def deployTroops():
    logging.info("=== Deploying Troops ===")

    if random.random() < 0.25:
        scroll_amount = random.randint(-250, 250)
        logging.debug(f"Random scroll action: {scroll_amount}")
        pyautogui.scroll(scroll_amount)
        human_pause()

    logging.info("Step 1: Deploying heroes")
    heroes = [
        ("heros/b_hero.PNG", hero_position)
    ]
    random.shuffle(heroes)

    for img, pos in heroes:
        logging.info(f"Looking for hero: {img}")
        hero_btn = locate_image(img)
        if hero_btn:
            human_click(*hero_btn)
            human_delay()
            deploy_x = pos[0] + randint(-8, 8)
            deploy_y = pos[1] + randint(-8, 8)
            logging.info(f"Deploying hero at ({deploy_x}, {deploy_y})")
            human_click(deploy_x, deploy_y)
            human_pause(0.6, 1.4)
        else:
            logging.warning(f"Hero not found: {img}")

    logging.info("Step 2: Deploying giants")
    giant_btn = locate_image("troops/b_giant.png")
    if giant_btn:
        human_click(*giant_btn)
        points = giant_positions[:]
        random.shuffle(points)
        logging.info(f"Deploying {len(points)} giants")
        for idx, (x, y) in enumerate(points):
            deploy_x = x + randint(-12, 12)
            deploy_y = y + randint(-12, 12)
            logging.debug(f"Giant {idx+1}/{len(points)} at ({deploy_x}, {deploy_y})")
            human_click(deploy_x, deploy_y)
            human_delay(0.15, 0.4)
    else:
        logging.warning("Giant button not found")
    
    logging.info("Waiting before deploying archers")
    human_delay(4, 5)
    
    logging.info("Step 3: Deploying archers")
    archer_btn = locate_image("troops/b_archer.png")
    if archer_btn:
        human_click(*archer_btn)
        points = archer_positions[:]
        random.shuffle(points)
        logging.info(f"Deploying {len(points)} archers")
        for idx, (x, y) in enumerate(points):
            deploy_x = x + randint(-12, 12)
            deploy_y = y + randint(-12, 12)
            logging.debug(f"Archer {idx+1}/{len(points)} at ({deploy_x}, {deploy_y})")
            human_click(deploy_x, deploy_y)
            human_delay(0.15, 0.4)
    else:
        logging.warning("Archer button not found")
    
    logging.info("✓ Troop deployment complete")
    return True


def finishBattleAndGoHome():
    logging.info("=== Waiting for battle to finish ===")
    check_count = 0
    while True:
        check_count += 1
        logging.debug(f"Checking for home button... (check #{check_count})")
        end_btn = locate_image("misc/b_home.png")
        if end_btn:
            logging.info("✓ Home button found, returning to base")
            human_click(*end_btn)
            human_pause(3, 5)
            logging.info("✓ Returned to base")
            return True
        time.sleep(1)


def attack():
    logging.info("\n" + "="*60)
    logging.info("STARTING NEW ATTACK CYCLE")
    logging.info("="*60)
    
    if not activate_game_window():
        logging.error("Failed to activate game window")
        return False

    if not startAttacking():
        logging.error("Failed to start attack")
        return False

    deployTroops()
    finishBattleAndGoHome()
    
    logging.info("="*60)
    logging.info("ATTACK CYCLE COMPLETED")
    logging.info("="*60 + "\n")
    return True

# =========================================================
# ===================== MAIN LOOP =========================
# =========================================================
bot_running = False
MAX_RUNTIME = timedelta(hours=4)
start_time = datetime.now()

def main_loop():
    logging.info("*** BOT MAIN LOOP STARTED ***")
    loop_start = datetime.now()
    attack_count = 0
    
    while bot_running:
        if datetime.now() - loop_start > MAX_RUNTIME:
            logging.info(f"Max runtime reached ({MAX_RUNTIME})")
            break
        
        try:
            attack_count += 1
            logging.info(f"\n{'#'*60}")
            logging.info(f"ATTACK #{attack_count}")
            logging.info(f"{'#'*60}")
            
            attack()

            if random.random() < 0.15:
                cooldown = random.randint(60, 180)
                logging.info(f"Random cooldown activated: {cooldown}s")
                time.sleep(cooldown)

            wait_time = random.randint(25, 55)
            logging.info(f"Waiting {wait_time}s before next attack\n")
            time.sleep(wait_time)

        except Exception as e:
            logging.error(f"ERROR IN MAIN LOOP: {str(e)}")
            logging.error(traceback.format_exc())
            logging.info("Waiting 60s before retry")
            time.sleep(60)
    
    logging.info("*** BOT MAIN LOOP STOPPED ***")

# =========================================================
# ===================== GUI ===============================
# =========================================================
def start_bot():
    global bot_running, start_time
    if not bot_running:
        logging.info("\n" + "="*60)
        logging.info("BOT STARTED BY USER")
        logging.info("="*60)
        bot_running = True
        start_time = datetime.now()
        threading.Thread(target=main_loop, daemon=True).start()
        status_label.config(text="Status: Running ✅", fg="green")
        update_timer()


def stop_bot():
    global bot_running
    logging.info("\n" + "="*60)
    logging.info("BOT STOPPED BY USER")
    logging.info("="*60)
    bot_running = False
    status_label.config(text="Status: Stopped ⛔", fg="red")
    timer_label.config(text="Elapsed: 00:00:00")


def update_timer():
    if bot_running:
        elapsed = datetime.now() - start_time
        timer_label.config(text=f"Elapsed: {str(elapsed).split('.')[0]}")
        root.after(1000, update_timer)


logging.info("="*60)
logging.info("CLASH OF CLANS BOT INITIALIZED")
logging.info("="*60)

root = tk.Tk()
root.title("Clash Bot Control")
root.geometry("180x140+1200+10")
root.configure(bg="black")
root.attributes("-topmost", True)

status_label = tk.Label(root, text="Status: Stopped ⛔", fg="red", bg="black")
status_label.pack(pady=4)

timer_label = tk.Label(root, text="Elapsed: 00:00:00", fg="yellow", bg="black")
timer_label.pack(pady=2)

tk.Button(root, text="Start", command=start_bot, bg="green", fg="white").pack(fill=tk.X)
tk.Button(root, text="Stop", command=stop_bot, bg="red", fg="white").pack(fill=tk.X)

logging.info("GUI Initialized - Ready to start")
root.mainloop()