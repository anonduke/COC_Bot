import pyautogui
import pygetwindow as gw
import time
from pynput import keyboard
import tkinter as tk
import threading

GAME_TITLE = "LDPlayer"

spacebar_pressed = False
start_x = start_y = end_x = end_y = 0
final_roi = None
overlay = None
canvas = None
root = None


# -------------------------------------------------
# WAIT FOR LDPLAYER WINDOW
# -------------------------------------------------
def wait_for_game_window():
    print("🔍 Waiting for game window:", GAME_TITLE)
    while True:
        wins = gw.getWindowsWithTitle(GAME_TITLE)
        if wins:
            print("✅ Game window detected!")
            return wins[0]
        time.sleep(1)


# -------------------------------------------------
# DRAW RED RECTANGLE WHILE DRAGGING
# -------------------------------------------------
def update_rectangle():
    global canvas, start_x, start_y, end_x, end_y
    if canvas:
        canvas.delete("all")
        canvas.create_rectangle(start_x, start_y, end_x, end_y,
                                outline='red', width=3)


# -------------------------------------------------
# SPACEBAR PRESS → START POINT
# -------------------------------------------------
def on_press(key):
    global spacebar_pressed, start_x, start_y, root
    if key == keyboard.Key.space and not spacebar_pressed:
        spacebar_pressed = True
        pos = pyautogui.position()
        start_x, start_y = pos.x, pos.y
        if root:
            root.deiconify()
        print(f"✅ Start: ({start_x}, {start_y})")


# -------------------------------------------------
# SPACEBAR RELEASE → END POINT
# -------------------------------------------------
def on_release(key):
    global spacebar_pressed, end_x, end_y, final_roi, root
    if key == keyboard.Key.space and spacebar_pressed:
        spacebar_pressed = False
        pos = pyautogui.position()
        end_x, end_y = pos.x, pos.y
        final_roi = (start_x, start_y, end_x, end_y)
        if root:
            root.withdraw()
        print(f"✅ End: ({end_x}, {end_y})")
        return False


# -------------------------------------------------
# OVERLAY WINDOW
# -------------------------------------------------
def run_overlay():
    global root, canvas
    root = tk.Tk()
    root.attributes('-fullscreen', True)
    root.attributes('-alpha', 0.3)
    root.attributes('-topmost', True)
    root.configure(bg='black')

    canvas = tk.Canvas(root, highlightthickness=0, bg='black')
    canvas.pack(fill='both', expand=True)

    root.withdraw()
    root.mainloop()


# -------------------------------------------------
# MAIN
# -------------------------------------------------
def main():
    global final_roi, spacebar_pressed, end_x, end_y

    win = wait_for_game_window()
    win.activate()
    time.sleep(1)

    print("\n🖱 INSTRUCTIONS:")
    print(" 1. Move mouse to START point")
    print(" 2. HOLD SPACEBAR")
    print(" 3. Move to END point")
    print(" 4. RELEASE SPACEBAR\n")

    # Start overlay
    overlay_thread = threading.Thread(target=run_overlay, daemon=True)
    overlay_thread.start()
    time.sleep(0.5)

    # Keyboard listener
    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()

    # Real-time rectangle drawing
    while listener.is_alive():
        if spacebar_pressed:
            pos = pyautogui.position()
            end_x, end_y = pos.x, pos.y
            update_rectangle()
        time.sleep(0.01)

    if root:
        root.quit()

    # -------------------------------------------------
    # FINAL ROI PROCESSING (RELATIVE LOGIC ADDED)
    # -------------------------------------------------
    if final_roi:
        x1, y1, x2, y2 = final_roi

        win = gw.getWindowsWithTitle(GAME_TITLE)[0]
        base_x, base_y = win.left, win.top

        left_abs = min(x1, x2)
        top_abs = min(y1, y2)
        width = abs(x2 - x1)
        height = abs(y2 - y1)

        # Absolute region (screen coords)
        abs_region = (left_abs, top_abs, width, height)

        # Relative region
        rel_left = left_abs - base_x
        rel_top = top_abs - base_y

        relative_region = (rel_left, rel_top, width, height)

        print("\n============ 📌 COPY THESE VALUES ============\n")
        print("ABSOLUTE REGION (full screen coords):")
        print(f"ABS_REGION = ({abs_region[0]}, {abs_region[1]}, {width}, {height})\n")

        print("RELATIVE REGION (LDPlayer-based → USE THIS):")
        print(f"REL_REGION = ({relative_region[0]}, {relative_region[1]}, {width}, {height})\n")

        print("==============================================\n")


if __name__ == "__main__":
    main()
