import time
import pyautogui
import pygetwindow as gw

def activate_ldplayer():
    wins = gw.getWindowsWithTitle("LDPlayer")
    if not wins:
        print("LDPlayer not found")
        return None, None

    win = wins[0]
    win.activate()
    win.restore()
    time.sleep(0.6)

    return win.left, win.top

def zoom_out():
    base_x, base_y = activate_ldplayer()
    if base_x is None:
        return

    # Move to center (for 1600x900)
    cx = base_x + 800
    cy = base_y + 450

    print(f"Moving to center ({cx},{cy}) before zoom...")
    pyautogui.moveTo(cx, cy, duration=0.2)
    time.sleep(0.2)

    print("Performing CTRL + scroll zoom out...")
    pyautogui.keyDown('ctrl')
    for _ in range(8):
        pyautogui.scroll(-600)
        time.sleep(0.15)
    pyautogui.keyUp('ctrl')

    print("Zoom done.")

if __name__ == "__main__":
    time.sleep(1)
    zoom_out()
