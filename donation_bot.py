from datetime import datetime, timedelta
import time
import logging
import random
import threading
import tkinter as tk
import traceback
import pyautogui
import pygetwindow as gw
import cv2
import numpy as np

logging.basicConfig(
    filename='donation_bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ------------------------------------------------------
# GLOBALS
# ------------------------------------------------------
WINDOW_X = 0
WINDOW_Y = 0
bot_running = False
GAME_TITLE = "LDPlayer"

# ------------------------------------------------------
# RELATIVE REGIONS
# ------------------------------------------------------

# Chat open/close
CHAT_OPEN = (49, 334)
CHAT_CLOSE = (487, 335)

# Donation button region
DONATION_REGION_REL = (7, 82, 443, 559)

# Previous donation alert
PREV_REGION_REL = (410, 100, 37, 45)
PREVIOUS_DONATION_IMG = "misc/previous_donation.png"

# Next donation alert
NEXT_REGION_REL = (408, 590, 38, 46)
NEXT_DONATION_IMG = "misc/next_donation.png"

# Donation popup region
DONATION_POPUP_REL = (482, 149, 609, 370)

# Exit donation popup
EXIT_DONATION = (1156, 366)

# Donation button image
DONATION_BUTTON_IMG = "misc/donate_button.png"

# ------------------------------------------------------
# TROOPS
# ------------------------------------------------------
TROOPS = [
    {
        "name": "dark_troop",
        "active": "misc/donate_dark_active.png",
        "inactive": "misc/donate_dark_inactive.png"
    },
    {
        "name": "elixir_troop",
        "active": "misc/donate_elixir_active.png",
        "inactive": "misc/donate_elixir_inactive.png"
    },
    {
        "name": "dark_spell",
        "active": "misc/donate_dark_spell_active.png",
        "inactive": "misc/donate_dark_inactive.png"
    },
    {
        "name": "elixir_spell",
        "active": "misc/donate_elixir_spell_active.png",
        "inactive": "misc/donate_elixir_spell_inactive.png"
    }
]

# ------------------------------------------------------
# RANDOM FALLBACK CLICK LOCATIONS (RELATIVE)
# ------------------------------------------------------
RANDOM_FALLBACK = [
    (617, 451), (689, 451), (764, 452), (863, 447),
    (933, 449), (1032, 446), (533, 446), (522, 185),
    (525, 294), (599, 298), (625, 197), (685, 200),
    (690, 308), (767, 289), (780, 186), (859, 187),
    (863, 292), (938, 297), (941, 202), (1044, 197),
    (1044, 291)
]


# ------------------------------------------------------
# HELPER FUNCTIONS
# ------------------------------------------------------
def activate_game_window():
    global WINDOW_X, WINDOW_Y
    try:
        wins = gw.getWindowsWithTitle(GAME_TITLE)
        if not wins:
            return False
        win = wins[0]
        win.activate()
        win.restore()
        WINDOW_X, WINDOW_Y = win.left, win.top
        return True
    except:
        return False


def abs_region(rel):
    x, y, w, h = rel
    return (WINDOW_X + x, WINDOW_Y + y, w, h)


# ------------------------------------------------------
# IMAGE FINDERS
# ------------------------------------------------------
def find_donation_button():
    region = abs_region(DONATION_REGION_REL)
    try:
        return pyautogui.locateCenterOnScreen(
            DONATION_BUTTON_IMG,
            region=region,
            confidence=0.78
        )
    except pyautogui.ImageNotFoundException:
        return None
    except:
        return None



def find_previous():
    region = abs_region(PREV_REGION_REL)
    try:
        return pyautogui.locateCenterOnScreen(
            PREVIOUS_DONATION_IMG,
            region=region,
            confidence=0.78
        )
    except pyautogui.ImageNotFoundException:
        return None
    except:
        return None



def find_next():
    region = abs_region(NEXT_REGION_REL)
    try:
        return pyautogui.locateCenterOnScreen(
            NEXT_DONATION_IMG,
            region=region,
            confidence=0.78
        )
    except pyautogui.ImageNotFoundException:
        return None
    except:
        return None



# ------------------------------------------------------
# COLOR CHECKER
# ------------------------------------------------------
def is_colored_pixel(x, y):
    """Confirm troop icon is colored, not grayscale."""
    
    # Convert to int to avoid PyAutoGUI crash
    x = int(x)
    y = int(y)

    snap = pyautogui.screenshot(region=(x - 3, y - 3, 6, 6))
    img = cv2.cvtColor(np.array(snap), cv2.COLOR_BGR2RGB)

    avg = img.mean(axis=(0, 1))
    r, g, b = avg

    # grayscale check
    if abs(r - g) < 18 and abs(g - b) < 18:
        return False

    return True

def donation_popup_open():
    """
    Detect if donation popup is still open by checking for 'closed' indicator image.
    If closed.png is visible → donation popup is closed.
    If not visible → popup is still open.
    """
    region = abs_region(DONATION_POPUP_REL)

    try:
        closed_found = pyautogui.locateOnScreen(
            "misc/closed.png",
            region=region,
            confidence=0.85
        )
    except pyautogui.ImageNotFoundException:
        closed_found = None
    except:
        closed_found = None

    # If closed indicator found → popup is CLOSED
    if closed_found:
        return False

    # Otherwise popup is still OPEN
    return True

# ------------------------------------------------------
# TROOP DETECTION
# ------------------------------------------------------
def detect_troop(active_path, inactive_path):
    region = abs_region(DONATION_POPUP_REL)

    try:
        active = pyautogui.locateCenterOnScreen(
            active_path, region=region, confidence=0.92
        )
    except pyautogui.ImageNotFoundException:
        active = None

    try:
        inactive = pyautogui.locateCenterOnScreen(
            inactive_path, region=region, confidence=0.97
        )
    except pyautogui.ImageNotFoundException:
        inactive = None

    # inactive but no active = skip
    if inactive and not active:
        return None

    if active and is_colored_pixel(active[0], active[1]):
        return active

    return None


# ------------------------------------------------------
# FALLBACK RANDOM TROOP CLICK LOGIC
# ------------------------------------------------------
def fallback_random_clicks():
    print("⚠ No active troops found → Running fallback clicks")

    for loc in RANDOM_FALLBACK:

        # Before clicking, check if popup is still open
        if not donation_popup_open():
            print("❌ Donation popup closed → stopping fallback clicks")
            return

        clicks = random.randint(4, 9)

        for _ in range(clicks):

            # Stop instantly if popup disappears mid-click
            if not donation_popup_open():
                print("❌ Donation popup closed → stopping fallback clicks")
                return

            pyautogui.click(WINDOW_X + loc[0], WINDOW_Y + loc[1])
            time.sleep(0.01)



# ------------------------------------------------------
# DONATION PROCESS
# ------------------------------------------------------
def perform_donation():
    print("➡ Donation popup detected")
    donated = False

    for troop in TROOPS:
        pos = detect_troop(troop["active"], troop["inactive"])
        if pos:
            print(f"✔ Donating {troop['name']}")
            pyautogui.click(pos)
            time.sleep(0.01)
            donated = True
            break
        if donation_popup_open():
            pyautogui.click(WINDOW_X + EXIT_DONATION[0], WINDOW_Y + EXIT_DONATION[1])
            time.sleep(0.2)

    # Close chat safely
    pyautogui.click(WINDOW_X + CHAT_CLOSE[0], WINDOW_Y + CHAT_CLOSE[1])       
    if not donated:
        fallback_random_clicks()

    # EXIT donation popup
    pyautogui.click(WINDOW_X + EXIT_DONATION[0], WINDOW_Y + EXIT_DONATION[1])
    time.sleep(0.5)
    pyautogui.click(WINDOW_X + CHAT_CLOSE[0], WINDOW_Y + CHAT_CLOSE[1])
    pyautogui.click(WINDOW_X + CHAT_OPEN[0], WINDOW_Y + CHAT_OPEN[1])



# ------------------------------------------------------
# MAIN LOOP
# ------------------------------------------------------
def donation_loop():
    activate_game_window()
    #time.sleep(0.5)

    while bot_running:

        # OPEN CHAT
        pyautogui.click(WINDOW_X + CHAT_OPEN[0], WINDOW_Y + CHAT_OPEN[1])
        time.sleep(1)

        # TRY 1: normal donation button
        pos = find_donation_button()

        # TRY 2: previous donation
        if not pos:
            prev = find_previous()
            if prev:
                pyautogui.click(prev)
                time.sleep(0.5)
                pos = find_donation_button()

        # TRY 3: next donation
        if not pos:
            nxt = find_next()
            if nxt:
                pyautogui.click(nxt)
                time.sleep(0.5)
                pos = find_donation_button()

        # PROCESS DONATION
        if pos:
            pyautogui.click(pos)
            time.sleep(0.01)
            perform_donation()
            continue

        # NO DONATION FOUND
        print("⏳ No donation → waiting 2 minutes")
        for _ in range(1):
            if not bot_running:
                return
            time.sleep(1)

        # CLOSE CHAT AFTER WAIT
        pyautogui.click(WINDOW_X + CHAT_CLOSE[0], WINDOW_Y + CHAT_CLOSE[1])
        rest = random.randint(1, 10)
        time.sleep(rest)


# ------------------------------------------------------
# GUI
# ------------------------------------------------------
def start_bot():
    global bot_running, bot_thread, start_time
    if not bot_running:
        bot_running = True
        start_time = datetime.now()
        status_label.config(text="Status: Running", fg="green")
        update_timer()
        bot_thread = threading.Thread(target=donation_loop, daemon=True)
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


root = tk.Tk()
root.title("COC Donation Bot")
root.attributes("-topmost", True)
root.geometry("190x160+1200+10")
root.resizable(False, False)
root.configure(bg="black")

status_label = tk.Label(root, text="Status: Stopped", fg="red", bg="black")
status_label.pack()
timer_label = tk.Label(root, text="Elapsed: 00:00:00", fg="yellow", bg="black")
timer_label.pack()

tk.Button(root, text="Start", bg="green", fg="white", command=start_bot).pack(fill=tk.X)
tk.Button(root, text="Stop", bg="red", fg="white", command=stop_bot).pack(fill=tk.X)
tk.Button(root, text="Restart", bg="blue", fg="white", command=restart_bot).pack(fill=tk.X)

root.mainloop()
