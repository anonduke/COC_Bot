from datetime import datetime
import time
import random
import threading
import tkinter as tk
import pyautogui
import pygetwindow as gw

# -------------------------------------
# GLOBALS
# -------------------------------------
WINDOW_X = 0
WINDOW_Y = 0
bot_running = False
GAME_TITLE = "LDPlayer"

# Chat open/close
CHAT_OPEN = (49, 334)
CHAT_CLOSE = (487, 335)

# Donation button image
DONATION_BUTTON_IMG = "misc/donate_button.png"

# Donation scanning region (relative)
DONATION_REGION_REL = (7, 82, 443, 559)

# Donation popup exit button (relative)
EXIT_DONATION = (1156, 366)

# 6 NEW CLICK LOCATIONS
CLICK_POINTS = [
    (534, 234),
    (604, 231),
    (592, 362),
    (543, 343),
    (540, 472),
    (602, 498)
]


# -------------------------------------
# LDPLAYER WINDOW
# -------------------------------------
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


# -------------------------------------
# DONATION BUTTON FINDER
# -------------------------------------
def find_donation_button():
    region = abs_region(DONATION_REGION_REL)
    try:
        return pyautogui.locateCenterOnScreen(
            DONATION_BUTTON_IMG,
            region=region,
            confidence=0.78
        )
    except:
        return None


# -------------------------------------
# PERFORM DONATION (new version)
# -------------------------------------
def perform_donation():
    print("➡ Donation popup opened")

    for x, y in CLICK_POINTS:
        clicks = random.randint(3, 4)
        for _ in range(clicks):
            pyautogui.click(WINDOW_X + x, WINDOW_Y + y)
            time.sleep(0.05)

    # Close donation popup
    pyautogui.click(WINDOW_X + EXIT_DONATION[0], WINDOW_Y + EXIT_DONATION[1])
    time.sleep(0.3)

    # Close chat
    pyautogui.click(WINDOW_X + CHAT_CLOSE[0], WINDOW_Y + CHAT_CLOSE[1])
    time.sleep(0.5)


# -------------------------------------
# MAIN DONATION LOOP
# -------------------------------------
def donation_loop():
    activate_game_window()

    while bot_running:

        # Open chat
        pyautogui.click(WINDOW_X + CHAT_OPEN[0], WINDOW_Y + CHAT_OPEN[1])
        time.sleep(1)

        pos = find_donation_button()

        if pos:
            pyautogui.click(pos)
            time.sleep(0.3)
            perform_donation()
            continue

        # Wait 2 minutes if nothing found
        print("⏳ No donation found → waiting 2 minutes")
        for _ in range(2):
            if not bot_running:
                return
            time.sleep(1)

        # Close chat
        pyautogui.click(WINDOW_X + CHAT_CLOSE[0], WINDOW_Y + CHAT_CLOSE[1])

        # Random cooldown
        rest = random.randint(1, 10)
        time.sleep(rest)


# -------------------------------------
# GUI
# -------------------------------------
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
root.title("COC Donation Bot V2")
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
