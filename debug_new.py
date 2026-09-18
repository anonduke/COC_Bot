import time
import logging
import pyautogui
import pygetwindow as gw
import pytesseract
import cv2
import numpy as np
import threading
import tkinter as tk
from PIL import Image

# =========================
# DEBUG SWITCH
# =========================
DEBUG_MODE = 0   # 1 = DEBUG, 0 = RUN
SAVE_DEBUG_IMAGES = False  # Save OCR images for debugging

# =========================
# TESSERACT PATH
# =========================
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# =========================
# LOGGING
# =========================
logging.basicConfig(
    filename="wall_upgrade_advanced.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# =========================
# CONFIG
# =========================
RESOURCE_THRESHOLD = 10_000_000
MAX_WALLS_PER_RUN = 5
MAX_GOLD_STORAGE = 20_000_000
MAX_ELIXIR_STORAGE = 20_000_000

# =========================
# RELATIVE COORDINATES (LDPLAYER)
# =========================
REL_UPGRADE_BUTTON = (560, 76)
REL_WALL_BUTTON = (542, 574)
REL_WALL_ADD_BUTTON = (527, 542)
REL_WALL_UPGRADE_BUTTON_GOLD = (660, 552)
REL_WALL_UPGRADE_BUTTON_ELIXIR = (789, 549)
REL_OK_BUTTON = (709, 465)

REL_AVAILABLE_GOLD_REGION = (976, 58, 152, 31)
REL_AVAILABLE_ELIXIR_REGION = (981, 120, 150, 30)
LIST_REGION = (458, 129, 48, 414)
WALL_COST_REGION = (614, 508, 88, 20)
REL_SCROLL_POINT = (493, 531)

GEM_WARNING_REGION = (315, 201, 563, 338)

MAX_SCROLLS = 20
SCROLL_AMOUNT = -300

LD_X = 0
LD_Y = 0

# =========================
# WINDOW CONTROL
# =========================
def activate_game_window():
    global LD_X, LD_Y
    wins = gw.getWindowsWithTitle("LDPlayer")
    if not wins:
        logging.error("LDPlayer not found")
        return False

    win = wins[0]
    win.activate()
    win.restore()
    time.sleep(1)

    LD_X, LD_Y = win.left, win.top
    logging.info(f"LDPlayer activated at {LD_X},{LD_Y}")
    return True

# =========================
# REL → ABS CONVERTER
# =========================
def rel_to_abs_region(rel):
    return (
        LD_X + rel[0],
        LD_Y + rel[1],
        rel[2],
        rel[3]
    )

# =========================
# TKINTER DEBUG OVERLAY
# =========================
def highlight_regions_tk(abs_regions, duration=3):
    if not DEBUG_MODE:
        return

    def run():
        root = tk.Tk()
        root.attributes("-fullscreen", True)
        root.attributes("-alpha", 0.25)
        root.attributes("-topmost", True)
        root.configure(bg="black")

        canvas = tk.Canvas(root, bg="black", highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        for label, (x, y, w, h) in abs_regions.items():
            canvas.create_rectangle(x, y, x + w, y + h, outline="green", width=4)
            canvas.create_text(
                x + 5, y - 10,
                text=label,
                fill="green",
                anchor="nw",
                font=("Arial", 12, "bold")
            )

        root.after(int(duration * 1000), root.destroy)
        root.mainloop()

    threading.Thread(target=run, daemon=True).start()

# =========================
# NUMBER PARSER
# =========================
def parse_coc_number(raw):
    """Parse Clash of Clans number format (e.g., '10M', '5.2M', '1,234,567')"""
    raw = raw.strip().upper()
    logging.info(f"Parsing raw OCR: '{raw}'")
    
    # Remove common OCR errors
    raw = raw.replace('O', '0').replace('o', '0').replace('l', '1').replace('I', '1')
    
    # Handle 'M' notation (millions)
    if 'M' in raw:
        try:
            num_part = raw.replace('M', '').strip()
            # Remove any non-digit/dot characters
            num_part = ''.join(ch for ch in num_part if ch.isdigit() or ch == '.')
            if num_part:
                value = float(num_part) * 1_000_000
                return int(value)
        except:
            pass
    
    # Handle 'K' notation (thousands)
    if 'K' in raw:
        try:
            num_part = raw.replace('K', '').strip()
            num_part = ''.join(ch for ch in num_part if ch.isdigit() or ch == '.')
            if num_part:
                value = float(num_part) * 1_000
                return int(value)
        except:
            pass
    
    # Handle comma-separated numbers
    if ',' in raw:
        cleaned = raw.replace(',', '')
        digits = ''.join(ch for ch in cleaned if ch.isdigit())
        if digits:
            return int(digits)
    
    # Handle space-separated numbers (OCR artifact)
    cleaned = "".join(ch for ch in raw if ch.isdigit() or ch == " ").strip()
    cleaned = " ".join(cleaned.split())
    digits = "".join(ch for ch in cleaned if ch.isdigit())

    if " " in cleaned:
        merged = "".join(cleaned.split())
        if merged.isdigit() and len(merged) >= 6:
            return int(merged)

    # Direct digit extraction
    if len(digits) >= 6:
        if len(digits) > 8:
            digits = digits[-8:]
        return int(digits)

    return 0

# =========================
# IMPROVED OCR CORE
# =========================
def preprocess_for_ocr(img_array, method='adaptive'):
    """Multiple preprocessing methods for better OCR"""
    gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
    
    if method == 'adaptive':
        # Adaptive thresholding - works well with varying backgrounds
        gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        processed = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
            cv2.THRESH_BINARY, 11, 2
        )
    elif method == 'otsu':
        # Original method
        gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_LINEAR)
        gray = cv2.bilateralFilter(gray, 9, 75, 75)
        _, processed = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    elif method == 'invert':
        # Inverted colors (white text on dark background)
        gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        _, processed = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY_INV)
    else:  # 'simple'
        # Simple threshold
        gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        _, processed = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)
    
    return processed

def read_number_from_rel_region(rel_region, clamp_max, label=""):
    abs_region = rel_to_abs_region(rel_region)
    img = pyautogui.screenshot(region=abs_region)
    img_array = np.array(img)
    
    # Try multiple preprocessing methods
    methods = ['adaptive', 'otsu', 'invert', 'simple']
    results = []
    
    for method in methods:
        try:
            processed = preprocess_for_ocr(img_array, method)
            
            # Save debug images
            if SAVE_DEBUG_IMAGES:
                cv2.imwrite(f"debug_{label}_{method}.png", processed)
            
            # Try multiple PSM modes
            psm_modes = [7, 6, 13]  # 7=single line, 6=block, 13=raw line
            
            for psm in psm_modes:
                raw = pytesseract.image_to_string(
                    processed,
                    config=f"--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789MKmk,. "
                )
                
                value = parse_coc_number(raw)
                if value > 0:
                    results.append((value, method, psm, raw))
                    logging.info(f"Method {method}, PSM {psm}: '{raw}' -> {value}")
        except Exception as e:
            logging.error(f"Error in method {method}: {e}")
    
    if not results:
        return 0
    
    # Return the most common non-zero result, or the maximum
    values = [r[0] for r in results]
    
    # Clamp result
    result = min(max(values), clamp_max)
    logging.info(f"Best result for {label}: {result}")
    
    return result

def read_number_with_retry(rel_region, clamp_max, label, retries=2, delay=0.5):
    """Reduced retries since we try multiple methods per attempt"""
    values = []
    for i in range(retries):
        val = read_number_from_rel_region(rel_region, clamp_max, label)
        logging.info(f"OCR {label} attempt {i+1}: {val}")
        if val > 0:
            values.append(val)
        if val > RESOURCE_THRESHOLD:  # If we got a good reading, stop early
            break
        time.sleep(delay)

    if not values:
        logging.error(f"OCR FAILED for {label} - Check debug images: debug_{label}_*.png")
        return 0

    return max(values)

# =========================
# WALL FINDER
# =========================
def scan_and_click_wall():
    abs_region = rel_to_abs_region(LIST_REGION)
    pyautogui.moveTo(LD_X + REL_SCROLL_POINT[0], LD_Y + REL_SCROLL_POINT[1])

    for scroll_attempt in range(MAX_SCROLLS):
        img = pyautogui.screenshot(region=abs_region)
        gray = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)

        # Try with different preprocessing
        _, thresh = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)
        data = pytesseract.image_to_data(thresh, output_type=pytesseract.Output.DICT)

        for i, word in enumerate(data["text"]):
            if "wall" in word.lower().strip():
                click_x = abs_region[0] + data["left"][i] + data["width"][i] // 2
                click_y = abs_region[1] + data["top"][i] + data["height"][i] // 2
                logging.info(f"Found 'wall' at scroll {scroll_attempt}, clicking {click_x},{click_y}")
                pyautogui.click(click_x, click_y)
                time.sleep(1)
                return True

        pyautogui.scroll(SCROLL_AMOUNT)
        time.sleep(0.8)

    logging.error("Wall not found after all scroll attempts")
    return False

# =========================
# MAIN LOGIC
# =========================
def upgrade_walls():
    if not activate_game_window():
        return "WINDOW_NOT_FOUND"

    time.sleep(1)

    # 🔍 DEBUG VISUAL CONFIRMATION
    highlight_regions_tk({
        "GOLD":   rel_to_abs_region(REL_AVAILABLE_GOLD_REGION),
        "ELIXIR": rel_to_abs_region(REL_AVAILABLE_ELIXIR_REGION),
        "WALL COST": rel_to_abs_region(WALL_COST_REGION),
        "UPGRADE LIST": rel_to_abs_region(LIST_REGION)
    }, duration=3)

    logging.info("Starting OCR for resources...")
    gold = read_number_with_retry(REL_AVAILABLE_GOLD_REGION, MAX_GOLD_STORAGE, "GOLD")
    elixir = read_number_with_retry(REL_AVAILABLE_ELIXIR_REGION, MAX_ELIXIR_STORAGE, "ELIXIR")

    logging.info(f"Final readings - Gold={gold}, Elixir={elixir}")

    if gold == 0 and elixir == 0:
        logging.error("OCR completely failed - check debug images in script directory")
        return "OCR_FAILED"

    if gold < RESOURCE_THRESHOLD and elixir < RESOURCE_THRESHOLD:
        return "NOT_ENOUGH_RESOURCES"

    pyautogui.click(LD_X + REL_UPGRADE_BUTTON[0], LD_Y + REL_UPGRADE_BUTTON[1])
    time.sleep(1.5)

    if not scan_and_click_wall():
        return "WALL_NOT_FOUND"

    return "READY_FOR_UPGRADE"

# =========================
# ENTRY
# =========================
if __name__ == "__main__":
    print("Starting Wall Upgrade Bot...")
    print(f"Debug images will be saved to: {SAVE_DEBUG_IMAGES}")
    result = upgrade_walls()
    print(f"Wall upgrade result: {result}")
    if result == "OCR_FAILED":
        print("\n⚠️  Check the debug_GOLD_*.png and debug_ELIXIR_*.png images")
        print("    to see what Tesseract is seeing. Adjust regions if needed.")