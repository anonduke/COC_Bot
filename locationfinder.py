from pynput import mouse, keyboard
import pygetwindow as gw

GAME_TITLE = "LDPlayer"
log_file = "click_coordinates.txt"
stop_key = keyboard.Key.esc

WINDOW_X = 0
WINDOW_Y = 0

# ---------------------------------------------------
# Activate LDPlayer and get its base coordinates
# ---------------------------------------------------
def get_ldplayer_window():
    global WINDOW_X, WINDOW_Y
    wins = gw.getWindowsWithTitle(GAME_TITLE)
    if not wins:
        print("❌ LDPlayer window not found!")
        return False

    win = wins[0]
    win.activate()
    win.restore()

    WINDOW_X = win.left
    WINDOW_Y = win.top

    print(f"✅ LDPlayer found at ({WINDOW_X}, {WINDOW_Y})")
    return True


# ---------------------------------------------------
# Mouse Callback (Convert ABS → REL)
# ---------------------------------------------------
def on_click(x, y, button, pressed):
    if pressed:
        rel_x = x - WINDOW_X
        rel_y = y - WINDOW_Y

        line = f"ABS: {x}, {y}   |   REL: {rel_x}, {rel_y}\n"

        with open(log_file, "a") as f:
            f.write(line)

        print(f"Logged -> ABS: {x}, {y}   |   REL: {rel_x}, {rel_y}")

# ---------------------------------------------------
# Stop with ESC
# ---------------------------------------------------
def on_press(key):
    if key == stop_key:
        print("🛑 Stopping logger...")
        return False


# ---------------------------------------------------
# MAIN
# ---------------------------------------------------
if __name__ == "__main__":
    if not get_ldplayer_window():
        quit()

    print("\n🖱 START LOGGING CLICKS")
    print("Press ESC to stop.\n")

    mouse_listener = mouse.Listener(on_click=on_click)
    mouse_listener.start()

    with keyboard.Listener(on_press=on_press) as keyboard_listener:
        keyboard_listener.join()
