import threading
import time
import random
import tkinter as tk
import pyautogui
import pygetwindow as gw

# ================================
# CONFIG
# ================================
GAME_TITLE = "LDPlayer"

# (x_rel, y_rel, delay)
CLICK_POINTS_REL = [
    (67, 651,1)
]

WINDOW_X = 0
WINDOW_Y = 0
bot_running = False


# ================================
# HIGHLIGHT CLICK
# ================================
def highlight_click(x, y):
    """Draw a small red circle at given absolute location."""
    hl = tk.Toplevel()
    hl.overrideredirect(True)
    hl.attributes("-topmost", True)
    hl.geometry(f"+{x-10}+{y-10}")
    hl.configure(bg="red")
    hl.wm_attributes("-alpha", 0.6)
    hl.lift()

    # Remove highlight after 0.12s
    hl.after(120, hl.destroy)


# ================================
# LDPLAYER WINDOW
# ================================
def activate_game_window():
    global WINDOW_X, WINDOW_Y

    wins = gw.getWindowsWithTitle(GAME_TITLE)
    if not wins:
        print("LDPlayer window not found!")
        return False

    win = wins[0]
    win.activate()
    win.restore()

    WINDOW_X, WINDOW_Y = win.left, win.top
    return True


# ================================
# CLICK LOOP
# ================================
def click_loop():
    activate_game_window()
    time.sleep(0.3)

    while bot_running:

        for x_rel, y_rel, delay in CLICK_POINTS_REL:

            if not bot_running:
                break

            x_abs = WINDOW_X + x_rel
            y_abs = WINDOW_Y + y_rel

            # Show highlight
            highlight_click(x_abs, y_abs)

            # Perform click
            pyautogui.click(x_abs, y_abs)

            # Tiny human-like jitter
            time.sleep(random.uniform(0.03, 0.09))

            # Apply custom delay
            if isinstance(delay, tuple):
                time.sleep(random.uniform(delay[0], delay[1]))
            else:
                time.sleep(delay)

        time.sleep(0.05)


# ================================
# GUI CONTROLS
# ================================
def start_bot():
    global bot_running
    if bot_running:
        return

    bot_running = True
    status_label.config(text="Status: Running", fg="green")

    t = threading.Thread(target=click_loop, daemon=True)
    t.start()


def stop_bot():
    global bot_running
    bot_running = False
    status_label.config(text="Status: Stopped", fg="red")


# ================================
# GUI WINDOW
# ================================
root = tk.Tk()
root.title("LDPlayer Auto Clicker")
root.geometry("220x150+1200+10")
root.configure(bg="black")
root.resizable(False, False)
root.attributes("-topmost", True)

status_label = tk.Label(
    root, text="Status: Stopped", fg="red", bg="black",
    font=("Arial", 11, "bold")
)
status_label.pack(pady=5)

tk.Button(root, text="Start", bg="green", fg="white", command=start_bot).pack(fill=tk.X, padx=10, pady=3)
tk.Button(root, text="Stop", bg="red", fg="white", command=stop_bot).pack(fill=tk.X, padx=10, pady=3)

root.mainloop()
