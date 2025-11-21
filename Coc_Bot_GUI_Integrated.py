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
import threading
import tkinter as tk
from PIL import Image, ImageTk

# Specify the path to the Tesseract executable (Windows-specific)
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

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
CONFIG = {
    'min_gold_to_attack': 500000,
    'max_attack_attempts': 20,
    'battle_wait_timeout': 30,
    'gold_region': (60, 125, 125, 25),
    'retry_attempts': 3,
    'retry_delay': 2,
}

WINDOW_X, WINDOW_Y = 0, 0

electro_dragon_positions = [(342, 260), (362, 245), (377, 234), (395, 223), (413, 210), (431, 197), (454, 181)]
balloon_positions = [(342, 260), (362, 245), (377, 234), (395, 223), (413, 210), (431, 197), (454, 181), (377, 234), (395, 223), (413, 210)]
warden_position = (395, 223)
archer_queen_position = (207, 353)
rage_spell_positions = [(502, 297), (632, 207), (581, 407), (702, 314)]
freeze_spell_positions = [(595, 409), (687, 341), (760, 273)]

def locate_image(image_path, confidence=0.8, retries=CONFIG['retry_attempts']):
    try:
        for attempt in range(retries):
            pos = pyautogui.locateCenterOnScreen(image_path, confidence=confidence)
            if pos:
                return pos
            time.sleep(CONFIG['retry_delay'])
    except:
        return None

def attack():
    logging.info("Attack started")
    time.sleep(2)
    logging.info("Attack completed")

# -----------------------------
# Floating Control Panel (GUI)
# -----------------------------
bot_running = False
bot_thread = None

def start_bot():
    global bot_running, bot_thread
    if not bot_running:
        bot_running = True
        status_label.config(text="Status: Running ✅", fg="green")
        bot_thread = threading.Thread(target=main_loop_wrapper, daemon=True)
        bot_thread.start()
        logging.info("Bot started from GUI")

def stop_bot():
    global bot_running
    bot_running = False
    status_label.config(text="Status: Stopped ⛔", fg="red")
    logging.info("Bot stop requested from GUI")

def restart_bot():
    stop_bot()
    time.sleep(1)
    start_bot()

def main_loop_wrapper():
    logging.info("Main loop wrapper started")
    while bot_running:
        try:
            attack()
            time.sleep(30)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logging.error(traceback.format_exc())
            time.sleep(60)

def load_icon(path, size=(20, 20)):
    try:
        image = Image.open(path).resize(size)
        return ImageTk.PhotoImage(image)
    except:
        return None

# GUI Setup
root = tk.Tk()
root.title("Clash Bot Control")
root.attributes("-topmost", True)
root.geometry("180x170+1300+20")  # Top-right corner
root.resizable(False, False)
root.configure(bg="black")

start_icon = load_icon("start.png")
stop_icon = load_icon("stop.png")
restart_icon = load_icon("restart.png")

tk.Button(root, image=start_icon, text=" Start", compound="left", command=start_bot, bg="green", fg="white").pack(fill=tk.X, pady=2)
tk.Button(root, image=stop_icon, text=" Stop", compound="left", command=stop_bot, bg="red", fg="white").pack(fill=tk.X, pady=2)
tk.Button(root, image=restart_icon, text=" Restart", compound="left", command=restart_bot, bg="blue", fg="white").pack(fill=tk.X, pady=2)

status_label = tk.Label(root, text="Status: Stopped ⛔", fg="red", bg="black", font=("Arial", 10, "bold"))
status_label.pack(pady=5)

root.mainloop()
