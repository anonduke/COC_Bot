import time
import logging
import pyautogui
import pygetwindow as gw
import pytesseract
import cv2
import numpy as np

# =========================
# TESSERACT PATH
# =========================
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# =========================
# LOGGING
# =========================
logging.basicConfig(
    filename="wall_upgrade_scanner.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# =========================
# RELATIVE CONFIG (LDPLAYER)
# =========================

REL_UPGRADE_BUTTON = (560, 76)
REL_WALL_BUTTON = (542, 574)
REL_WALL_ADD_BUTTON = (527, 542)
REL_WALL_UPGRADE_BUTTON_GOLD = (660, 552)
REL_WALL_UPGRADE_BUTTON_Elixir = (789,549)
REL_OK_BUTTON = (709, 465)

REL_AVAILABLE_GOLD_REGION = (976, 58, 152, 31) #NUNBERS
REL_AVAILABLE_ELIXIR_REGION = (981, 120, 150, 30) #NUNBERS
LIST_REGION = (458, 129, 48, 414)   # (x, y, w, h)
WALL_COST_REGION = (614, 508, 88, 20) #NUNBERS

REL_SCROLL_POINT = (493, 531)

MAX_SCROLLS = 10
SCROLL_AMOUNT = -300   # scroll down

# =========================
# GLOBAL WINDOW OFFSET
# =========================
LD_X = 0
LD_Y = 0

# =========================
# WINDOW CONTROL
# =========================
def activate_game_window():
    global LD_X, LD_Y

    try:
        windows = gw.getWindowsWithTitle("LDPlayer")
        if not windows:
            logging.error("LDPlayer window not found")
            return False

        w = windows[0]
        w.activate()
        w.restore()
        time.sleep(1)

        LD_X, LD_Y = w.left, w.top
        logging.info(f"LDPlayer activated at ({LD_X}, {LD_Y})")
        return True

    except Exception as e:
        logging.error(f"Failed to activate LDPlayer: {e}")
        return False

# =========================
# DRAW DEBUG OVERLAY
# =========================
def draw_green_overlay(region, image):
    x, y, w, h = region
    overlay = image.copy()
    cv2.rectangle(
        overlay,
        (0, 0),
        (w, h),
        (0, 255, 0),
        2
    )
    return overlay

# =========================
# OCR SCAN (REGION ONLY)
# =========================
def scan_and_click_wall(abs_region, scan_id):
    """
    Scans the upgrade list region, finds 'wall', and clicks it.
    Returns True if wall was found and clicked.
    """

    # Take REGION-ONLY screenshot
    screenshot = pyautogui.screenshot(region=abs_region)
    frame = np.array(screenshot)

    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)

    # OCR with bounding boxes
    data = pytesseract.image_to_data(
        thresh,
        config="--psm 6",
        output_type=pytesseract.Output.DICT
    )

    n = len(data["text"])
    for i in range(n):
        word = data["text"][i].lower().strip()

        if word == "wall":
            x = data["left"][i]
            y = data["top"][i]
            w = data["width"][i]
            h = data["height"][i]

            # Convert REGION-relative → SCREEN absolute
            click_x = abs_region[0] + x + w // 2
            click_y = abs_region[1] + y + h // 2

            logging.info(
                f"✅ WALL FOUND at region-relative ({x},{y}), clicking at ABS ({click_x},{click_y})"
            )

            pyautogui.click(click_x, click_y)
            time.sleep(1)

            return True

    logging.info(f"No wall found in scan {scan_id}")
    return False


# =========================
# MAIN LOGIC
# =========================
def find_wall_in_upgrade_list():
    logging.info("Starting wall upgrade list scanner")

    if not activate_game_window():
        return

    # Convert REL → ABS
    upgrade_btn_abs = (LD_X + REL_UPGRADE_BUTTON[0], LD_Y + REL_UPGRADE_BUTTON[1])

    abs_region = (
        LD_X + LIST_REGION[0],
        LD_Y + LIST_REGION[1],
        LIST_REGION[2],
        LIST_REGION[3]
    )

    scroll_point_abs = (
        LD_X + REL_SCROLL_POINT[0],
        LD_Y + REL_SCROLL_POINT[1]
    )

    # Open upgrade list
    pyautogui.click(*upgrade_btn_abs)
    logging.info("Clicked upgrade list button")
    time.sleep(1.5)

    # Move mouse inside scroll area
    pyautogui.moveTo(*scroll_point_abs)

    for i in range(1, MAX_SCROLLS + 1):
        logging.info(f"Scanning list (attempt {i}/{MAX_SCROLLS})")

        found = scan_and_click_wall(abs_region, i)

        if found:
            print("WALL FOUND AND CLICKED — stopping scan")
            return

        pyautogui.scroll(SCROLL_AMOUNT)
        time.sleep(0.8)

    logging.info("❌ Wall not found after max scrolls")
    print("Wall NOT found — stopping safely")

# =========================
# ENTRY POINT
# =========================
if __name__ == "__main__":
    print("Starting standalone wall upgrade scanner...")
    find_wall_in_upgrade_list()
