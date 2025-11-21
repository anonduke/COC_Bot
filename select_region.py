import cv2
import numpy as np
import pyautogui
import pygetwindow as gw
import time

GAME_TITLE = "Clash of Clans - FasaluRahmanfrkp"

# Variables
selecting = False
start_x = start_y = end_x = end_y = 0
final_roi = None

def wait_for_game_window():
    print("🔍 Waiting for game window:", GAME_TITLE)

    while True:
        wins = gw.getWindowsWithTitle(GAME_TITLE)
        if wins:
            win = wins[0]
            print("✅ Game window detected!")
            return win

        print("⏳ Game window not found, retrying...")
        time.sleep(1)


def capture_game_window(win):
    """Capture ONLY the Clash of Clans window area."""
    x, y = win.left, win.top
    w, h = win.width, win.height

    print(f"📸 Capturing game window at ({x},{y}) {w}x{h}")

    screenshot = pyautogui.screenshot(region=(x, y, w, h))
    img = cv2.cvtColor(np.array(screenshot), cv2.COLOR_BGR2RGB)

    return img, x, y, w, h


def mouse_callback(event, x, y, flags, param):
    global selecting, start_x, start_y, end_x, end_y, final_roi

    if event == cv2.EVENT_LBUTTONDOWN:
        selecting = True
        start_x, start_y = x, y

    elif event == cv2.EVENT_MOUSEMOVE and selecting:
        end_x, end_y = x, y

    elif event == cv2.EVENT_LBUTTONUP:
        selecting = False
        end_x, end_y = x, y
        final_roi = (start_x, start_y, end_x, end_y)


def main():
    global final_roi

    # STEP 1: Wait for game window
    win = wait_for_game_window()
    win.activate()
    time.sleep(1)

    # STEP 2: Capture game window ONLY
    img, base_x, base_y, win_w, win_h = capture_game_window(win)
    clone = img.copy()

    cv2.namedWindow("Select Troop Bar Region")
    cv2.setMouseCallback("Select Troop Bar Region", mouse_callback)

    print("\n🖱 INSTRUCTIONS:")
    print(" → A window will appear showing ONLY the game screen.")
    print(" → Click and DRAG over the troop bar region.")
    print(" → Release mouse, then press ENTER to confirm.")
    print(" → Press ESC to cancel.\n")

    while True:
        temp = clone.copy()
        if selecting or final_roi:
            cv2.rectangle(temp, (start_x, start_y), (end_x, end_y), (0, 255, 0), 2)

        cv2.imshow("Select Troop Bar Region", temp)
        key = cv2.waitKey(1)

        if key == 13:  # ENTER key
            break
        if key == 27:  # ESC key
            print("❌ Cancelled.")
            cv2.destroyAllWindows()
            return

    cv2.destroyAllWindows()

    if final_roi:
        x1, y1, x2, y2 = final_roi
        left = min(x1, x2)
        top = min(y1, y2)
        width = abs(x2 - x1)
        height = abs(y2 - y1)

        # Convert to SCREEN COORDINATES
        screen_left = base_x + left
        screen_top = base_y + top

        print("\n🎯 FINAL TROOP BAR REGION (SCREEN COORDINATES)")
        print("------------------------------------------------")
        print(f"Left   (X): {screen_left}")
        print(f"Top    (Y): {screen_top}")
        print(f"Width:     {width}")
        print(f"Height:    {height}")
        print("------------------------------------------------")

        print("\n📌 COPY THIS INTO YOUR MAIN BOT CODE:")
        print(f"TROOP_BAR_REGION = ({screen_left}, {screen_top}, {width}, {height})\n")

    else:
        print("❌ No region selected.")


if __name__ == "__main__":
    main()
