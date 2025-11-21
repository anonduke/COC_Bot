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
import os

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
# Configuration dictionary
CONFIG = {
    'goblin_attack': 0,
    'min_gold_to_attack': 100000,
    'max_attack_attempts': 20,
    'battle_wait_timeout': 30,
    'gold_region': (60, 125, 125, 25),  # Relative to WINDOW_X, WINDOW_Y
    'retry_attempts': 3,
    'retry_delay': 2,
}

# Global variables
WINDOW_X, WINDOW_Y = 0, 0
gold_region_files = []
auto_zoomout_var = None

# **Troop & Spell Deployment Coordinates**

goblins_positions = [
    (509, 305),(242, 327),(263, 320),(283, 308),(308, 293),(328, 277),(346, 260),(362, 249),(385, 235),(407, 217),(416, 206),
    (435, 192),(454, 177),(479, 160),(497, 143),(516, 129),(537, 110),(551, 98),(567, 86),(594, 67),(605, 58),(642, 48),(667, 46),(692, 51),(717, 57),(740, 62),(763, 74),(784, 90),(803, 105),(825, 126),(846, 144),(866, 152),
    (880, 159),(901, 168),(912, 176),(934, 189),(945, 201),(965, 218),(988, 235),(998, 242),(1023, 263),(1050, 283),(1062, 290),(1092, 318),
    (1108, 330),(1130, 358),(1131, 368),(1129, 401),(1120, 416),(1101, 433),(1084, 448),(1063, 473),(1050, 488),(1033, 501),(986, 521),
    (979, 526),(971, 540),(956, 552),(938, 565),(927, 571),(908, 578),(508, 577),(483, 573),(452, 558),(430, 541),(410, 518),(390, 506),
    (375, 497),(362, 489),(353, 484),(339, 470),(320, 462),(298, 445),(279, 422),(277, 410),(256, 395),(238, 374),(230, 337),(234, 328),(244, 320),(263, 311)
]
wallbreaker_position = [
    (509, 305),(328, 277),(385, 235),(435, 192),(516, 129),(605, 58),(880, 159),(1101, 433),
    (277, 410), (353, 484),  (277, 410), (1129, 401)]
electro_dragon_positions = [
    (342, 260), (362, 245), (377, 234), (395, 223), (413, 210), 
    (431, 197), (454, 181), (395, 223),(377, 234)
]
balloon_positions = [
    (342, 260), (362, 245), (377, 234), (395, 223), (413, 210), 
    (431, 197), (454, 181), (377, 234), (395, 223), (413, 210),
    (395, 223), (342, 260), (362, 245), (377, 234), (395, 223)
]
warden_position = (395, 223)
archer_queen_position = (207, 353)
king_position = (347, 489)
prince_position = (395, 223)
rc_position = (207, 353)
cc_position = (347, 489)
baby_dragon_position = (395, 223)


rage_spell_positions = [
    (502, 297), (632, 207),
    (581, 407), (702, 314)
]
freeze_spell_positions = [
    (595, 409), (687, 341), (760, 273)
]

def activate_game_window():
    """Bring LDPlayer window to the foreground."""
    global WINDOW_X, WINDOW_Y
    logging.debug("Activating LDPlayer window")
    try:
        windows = gw.getWindowsWithTitle("LDPlayer")

        # --- REMOVED reloadgame.png check ---
        # pos = locate_image("misc/reloadgame.png")
        # if pos:
        #     pyautogui.click(pos)

        if not windows:
            logging.error("No LDPlayer window found")
            return False

        game_window = windows[0]
        game_window.activate()
        game_window.restore()
        WINDOW_X, WINDOW_Y = game_window.left, game_window.top
        logging.info(f"LDPlayer activated at ({WINDOW_X}, {WINDOW_Y})")

        time.sleep(1)
        return True

    except Exception as e:
        logging.error(f"Failed to activate LDPlayer: {traceback.format_exc()}")
        return False


def locate_image(image_path, confidence=0.8, retries=CONFIG['retry_attempts']):
    """Locate an image on screen with retries."""
    logging.debug(f"Looking for '{image_path}' with confidence={confidence}")
    for attempt in range(retries):
        try:
            pos = pyautogui.locateCenterOnScreen(image_path, confidence=confidence)
            if pos:
                logging.info(f"Found '{image_path}' at {pos} (Attempt {attempt + 1})")
                return pos
            time.sleep(CONFIG['retry_delay'])
        except Exception as e:
            logging.error(f"Error locating '{image_path}': {traceback.format_exc()}")
    return None

def zoomOutAndCenter():
    """Zoom out and center base."""
    logging.debug("Zooming out and centering base")

    if not activate_game_window():
        return False

    pyautogui.keyDown('ctrl')
    time.sleep(0.1)
    for _ in range(5):
        pyautogui.scroll(-500)
        time.sleep(0.2)
    pyautogui.keyUp('ctrl')
    time.sleep(0.1)

    pyautogui.moveTo(WINDOW_X + 640, WINDOW_Y + 360)
    pyautogui.click()
    time.sleep(0.5)

    logging.info("Base centered")
    return True
def recognize_numbers_from_region(region):
    """Capture a screenshot of the specified region and recognize numbers using pytesseract."""
    try:
        # Capture screenshot of the region
        screenshot = pyautogui.screenshot(region=region)
        
        # Save with timestamp for debugging
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"gold_region_{timestamp}.png"
        screenshot.save(filename)
        gold_region_files.append(filename)
        logging.info(f"Gold region saved as {filename}")
        
        # Read the image with OpenCV
        image = cv2.imread(filename)
        gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # Apply thresholding
        _, threshold_img = cv2.threshold(gray_image, 150, 255, cv2.THRESH_BINARY)
        
        # OCR with pytesseract (digits only)
        custom_config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789'
        recognized_text = pytesseract.image_to_string(threshold_img, config=custom_config)
        
        # Clean and convert to integer
        numbers = ''.join(filter(str.isdigit, recognized_text))
        gold = int(numbers) if numbers else 0
        
        logging.debug(f"Recognized gold amount: {gold}")
        
        # Stop the program if gold is zero
        if gold == 0:
            logging.error("Gold amount is zero. next Openent.")
            nextOpponent()
        
        return gold
    except Exception as e:
        logging.error(f"Error in recognize_numbers_from_region: {traceback.format_exc()}")
        return 0
    
def isGoodOpponent():
    """Check if opponent has enough gold using pytesseract OCR."""
    logging.debug("Entering isGoodOpponent")
    gold_region = (WINDOW_X + CONFIG['gold_region'][0], WINDOW_Y + CONFIG['gold_region'][1], 
                   CONFIG['gold_region'][2], CONFIG['gold_region'][3])
    
    # Visualize gold region (commented out as per your code)
    # draw_green_square(gold_region[0], gold_region[1], gold_region[2], gold_region[3], duration=1)
    
    # Fetch gold amount using OCR
    gold = recognize_numbers_from_region(gold_region)
    logging.info(f"Detected gold: {gold}")
    
    # Check if gold meets the minimum requirement
    result = gold >= CONFIG['min_gold_to_attack']
    logging.debug(f"Is good opponent? {result} (Gold: {gold} vs Min: {CONFIG['min_gold_to_attack']})")
    return result

def updateTimestamp(timer: str):
    """Update timestamp."""
    if timer in timestamps:
        timestamps[timer] = datetime.now()

def finishBattleAndGoHome():
    """End battle and return to village."""
    logging.debug("Entering finishBattleAndGoHome")
    while True:
        pos = locate_image("misc/end_battle.png")
        if pos:
            pyautogui.click(pos)
            updateTimestamp('_lastInteraction')
            logging.info("Battle ended, returning home")
            time.sleep(4)
            #trainTroops()
            return True
        time.sleep(1)

def deployTroops():
    """Deploy troops with strategic positioning and slight randomness."""
    logging.debug("Deploying troops")
    if CONFIG['goblin_attack']:
        pyautogui.click(locate_image("misc/wall.png"))
        time.sleep(0.5)
        for x, y in wallbreaker_position:
            pyautogui.click(x + randint(-10, 10), y + randint(-10, 10))
            time.sleep(0.1)
        time.sleep(0.5)
        archer_queen_pos = locate_image("heros/archer_queen.PNG")
        if archer_queen_pos:
            pyautogui.click(archer_queen_pos)
            time.sleep(0.5)
            pyautogui.click(archer_queen_position[0] + randint(-5, 5), archer_queen_position[1] + randint(-5, 5))
            pyautogui.click(archer_queen_pos)
            # Deploy king
        king_pos = locate_image("heros/king.PNG")
        if king_pos:
            pyautogui.click(king_pos)
            time.sleep(0.5)
            pyautogui.click(king_position[0] + randint(-5, 5), king_position[1] + randint(-5, 5))
            # Deploy CC
            time.sleep(4)
            pyautogui.click(king_pos)
        cc_pos = locate_image("troops/troop_siegebarracks.PNG")
        if cc_pos:
            pyautogui.click(cc_pos)
            time.sleep(0.5)
            pyautogui.click(cc_position[0] + randint(-5, 5), cc_position[1] + randint(-5, 5))
            pyautogui.click(king_pos)
        pyautogui.click(locate_image("troops/goblin.png"))
        time.sleep(0.5)
        for x, y in goblins_positions:
            pyautogui.click(x + randint(-10, 10), y + randint(-10, 10))
            time.sleep(0.2)
        time.sleep(10)
        pyautogui.click(locate_image("misc/surrender.png"))
        pyautogui.click(locate_image("misc/surrender_ok.png"))
    else:
            # Deploy **Archer Queen**
        archer_queen_pos = locate_image("heros/archer_queen.PNG")
        if archer_queen_pos:
            pyautogui.click(archer_queen_pos)
            time.sleep(0.5)
            pyautogui.click(archer_queen_position[0] + randint(-5, 5), archer_queen_position[1] + randint(-5, 5))
            pyautogui.click(archer_queen_pos)
            # Deploy king
        king_pos = locate_image("heros/king.PNG")
        if king_pos:
            pyautogui.click(king_pos)
            time.sleep(0.5)
            pyautogui.click(king_position[0] + randint(-5, 5), king_position[1] + randint(-5, 5))
            # Deploy CC
            time.sleep(4)
        cc_pos = locate_image("troops/troop_siegebarracks.PNG")
        if cc_pos:
            pyautogui.click(cc_pos)
            time.sleep(0.5)
            pyautogui.click(cc_position[0] + randint(-5, 5), cc_position[1] + randint(-5, 5))

        # Deploy **Electro Dragons**
        pyautogui.click(locate_image("troops/troops_electro.png"))
        time.sleep(0.5)
        for x, y in electro_dragon_positions:
            pyautogui.click(x + randint(-10, 10), y + randint(-10, 10))
            time.sleep(0.1)

        # Deploy **Balloons**
        pyautogui.click(locate_image("troops/troops_loon.png"))
        time.sleep(0.1)
        for x, y in balloon_positions:
            pyautogui.click(x + randint(-10, 10), y + randint(-10, 10))
            time.sleep(0.1)

        # Deploy **Warden**
        warden_pos = locate_image("heros/warden.PNG")
        if warden_pos:
            pyautogui.click(warden_pos)
            time.sleep(0.1)
            pyautogui.click(warden_position[0] + randint(-5, 5), warden_position[1] + randint(-5, 5))
        # Deploy Prince
        prince_pos = locate_image("heros/warden.PNG")
        if prince_pos:
            pyautogui.click(prince_pos)
            time.sleep(0.1)
            pyautogui.click(prince_position[0] + randint(-5, 5), prince_position[1] + randint(-5, 5))   
        # Wait **3 seconds**, then enable **Warden Ability**
        time.sleep(3)
        pyautogui.click(warden_pos)
        if prince_pos:
            time.sleep(1)
            pyautogui.click(prince_pos)
        rc_pos = locate_image("heros/rc.PNG")
        if rc_pos:
            pyautogui.click(rc_pos)
            time.sleep(0.1)
            pyautogui.click(rc_position[0] + randint(-5, 5), rc_position[1] + randint(-5, 5))
        baby_dragon_pos = locate_image("troops/troop_babydragon.PNG")
        if baby_dragon_pos:
            pyautogui.click(baby_dragon_pos)
            time.sleep(0.1)
            pyautogui.click(baby_dragon_position[0] + randint(-5, 5), baby_dragon_position[1] + randint(-5, 5))
        # Wait **3 seconds**, then deploy **first 2 Rage Spells**
        time.sleep(3)
        #deploy_rage_smart()
        # Deploy **Freeze Spells** together
        #time.sleep(4)
        #deploy_rage_smart()
        #deploy_freeze_smart()
        pyautogui.click(locate_image("troops/rage_spell.PNG"))
        time.sleep(0.5)
        for x, y in rage_spell_positions[:2]:
            pyautogui.click(x + randint(-5, 5), y + randint(-5, 5))
            time.sleep(0.2)

        # Wait **4 seconds**, then deploy **next 2 Rage Spells**
        time.sleep(9)
        for x, y in rage_spell_positions[2:]:
            pyautogui.click(x + randint(-5, 5), y + randint(-5, 5))
            time.sleep(0.2)

        # Deploy **Freeze Spells** together
        time.sleep(1)
        pyautogui.click(locate_image("troops/freez_spell.PNG"))
        time.sleep(0.5)
        for x, y in freeze_spell_positions:
            pyautogui.click(x + randint(-5, 5), y + randint(-5, 5))
    logging.info("Troop and spell deployment complete")
    return True
def zoom_out():
    """Zoom out inside LDPlayer (1600x900)."""
    try:
        # Activate window
        windows = gw.getWindowsWithTitle("LDPlayer")
        if not windows:
            logging.error("LDPlayer not found for zoom")
            return False

        win = windows[0]
        win.activate()
        win.restore()
        time.sleep(0.5)

        # Center of 1600x900
        cx = win.left + 800
        cy = win.top + 450

        # Move inside gameplay area
        pyautogui.moveTo(cx, cy, duration=0.2)
        time.sleep(0.2)

        # Zoom out
        pyautogui.keyDown("ctrl")
        for _ in range(7):
            pyautogui.scroll(-600)
            time.sleep(0.15)
        pyautogui.keyUp("ctrl")

        logging.info("Zoomed out successfully")
        return True

    except Exception as e:
        logging.error(f"Zoom out failed: {traceback.format_exc()}")
        return False

# ============================================================
#                  WALL UPGRADE ENGINE
# ============================================================

# Maximum real storage in your account
MAX_STORAGE = 29_000_000

# Relative offsets based on LDPlayer window (x, y, w, h)
GOLD_OFFSET   = (1019, 59, 107, 27)
ELIXIR_OFFSET = (1005, 121, 124, 28)

# Upgrade buttons (LDPlayer-relative offsets)
UPGRADE_USING_GOLD   = (738, 566)
UPGRADE_USING_ELIXIR = (879, 564)

# Wall list entry (LDPlayer-relative)
WALL_ENTRY_OFFSET = (674, 532)

# Scroll area (LDPlayer-relative)
SCROLL_POINT_OFFSET = (683, 516)

# Upgrade panel open button (LDPlayer-relative)
UPGRADE_PANEL_OFFSET = (631, 76)

# Wall text region (ABSOLUTE after conversion through LDPlayer)
WALL_TEXT_OFFSET = (521, 515, 96, 37)


# ---------------- INTERNAL HELPERS ----------------

def get_ldplayer_window():
    wins = gw.getWindowsWithTitle("LDPlayer")
    return wins[0] if wins else None


def rel_to_abs(offset):
    win = get_ldplayer_window()
    if win is None:
        return None
    bx, by = win.left, win.top
    return (bx + offset[0], by + offset[1], offset[2], offset[3]) if len(offset)==4 else (bx + offset[0], by + offset[1])


def ocr_storage(offset, debug_file):
    """Reads storage using OCR with all fixes."""
    region = rel_to_abs(offset)
    if region is None:
        logging.error("LDPlayer not found for storage OCR")
        return 0

    x, y, w, h = region
    img = pyautogui.screenshot(region=(x, y, w, h))
    img.save(debug_file)

    img = cv2.cvtColor(np.array(img), cv2.COLOR_BGR2GRAY)
    img = cv2.resize(img, None, fx=3, fy=3, interpolation=cv2.INTER_LINEAR)
    img = cv2.bilateralFilter(img, 9, 75, 75)
    _, img = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    raw = pytesseract.image_to_string(
        img,
        config="--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789 "
    )
    digits = ''.join(c for c in raw if c.isdigit())

    # If no digits at all
    if digits == "":
        return 0

    # Remove raw OCR accidents like "11" inserted
    while "11" in digits and len(digits) > 7:
        digits = digits.replace("11", "1")

    # Trim to 8 digits max since CoC storage NEVER exceeds this
    if len(digits) > 8:
        digits = digits[-8:]

    value = int(digits)

    # FINAL SAFETY RULE:
    # If OCR overshoots max storage → return 0 (force retry)
    if value > MAX_STORAGE:
        return 0

    return value


def ocr_wall_text():
    """Detects 'wall' text from the upgrade list after scrolling."""
    wx, wy, ww, wh = WALL_TEXT_OFFSET
    img = pyautogui.screenshot(region=(wx, wy, ww, wh))
    img.save("wall_region_debug.png")

    gray = cv2.cvtColor(np.array(img), cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)

    text = pytesseract.image_to_string(th).lower()
    return "wall" in text


def click_rel(offset):
    abs_pos = rel_to_abs(offset)
    pyautogui.click(abs_pos[0], abs_pos[1])


# --------------------------------------------------
#                 WALL UPGRADE MAIN
# --------------------------------------------------

def auto_wall_upgrade():
    """Main wall-upgrade logic. Runs BEFORE every attack."""
    try:
        win = get_ldplayer_window()
        if win is None:
            logging.error("LDPlayer not found — skip wall upgrade")
            return

        # Read gold + elixir
        gold   = ocr_storage(GOLD_OFFSET,   "debug_gold.png")
        elixir = ocr_storage(ELIXIR_OFFSET, "debug_elixir.png")

        # Require at least 8M
        if gold < 8_000_000 and elixir < 8_000_000:
            return  # No upgrade needed

        logging.info(f"[WALL] Triggered: Gold={gold}, Elixir={elixir}")

        # Open upgrade panel
        click_rel(UPGRADE_PANEL_OFFSET)
        time.sleep(0.5)

        # Scroll upgrade list
        abs_scroll = rel_to_abs(SCROLL_POINT_OFFSET)
        pyautogui.moveTo(abs_scroll[0], abs_scroll[1], duration=0.2)
        for _ in range(6):
            pyautogui.scroll(-600)
            time.sleep(0.2)
        time.sleep(0.8)

        # Check for "wall" in text
        if not ocr_wall_text():
            logging.error("[WALL] Wall entry NOT found after scrolling")
            return

        # Click wall entry
        click_rel(WALL_ENTRY_OFFSET)
        time.sleep(0.4)

        # Determine which resource to use
        walls_gold   = gold   // 8_000_000
        walls_elixir = elixir // 8_000_000

        total = walls_gold + walls_elixir

        logging.info(f"[WALL] Upgrading {total} walls (G:{walls_gold}, E:{walls_elixir})")

        # Upgrade using GOLD first
        for _ in range(walls_gold):
            click_rel(UPGRADE_USING_GOLD)
            time.sleep(0.3)

        # Then upgrade using ELIXIR
        for _ in range(walls_elixir):
            click_rel(UPGRADE_USING_ELIXIR)
            time.sleep(0.3)

        logging.info("[WALL] Wall upgrade finished")

    except Exception as e:
        logging.error(f"[WALL] ERROR: {traceback.format_exc()}")

def attack():
    """Handle full attack sequence."""
    logging.debug("Starting attack")
    # -----------------------------
    # AUTO WALL UPGRADE BEFORE ATTACK
    # -----------------------------
    try:
        if auto_wall_upgrade_var.get():     # Check GUI toggle
            logging.info("Auto Wall Upgrade ON — checking storage...")
            auto_wall_upgrade()
        else:
            logging.info("Auto Wall Upgrade OFF")
    except Exception as e:
        logging.error(f"Wall upgrade check failed: {traceback.format_exc()}")

    if not activate_game_window():
        return False
    # Only zoom if user enabled checkbox
    try:
        if auto_zoomout_var.get():
            logging.info("Auto zoom is ON — performing zoom out")
            zoom_out()
        else:
            logging.info("Auto zoom is OFF — skipping zoom out")
    except:
        logging.error("Failed reading zoom checkbox state")


    collectorchecker()
    if not startAttacking():
        return False
    # if not zoomOutAndCenter():
    #     return False
    for attempt in range(20):
        logging.debug(f"Attack attempt {attempt + 1}/20")
        if isGoodOpponentAdvanced():
            if deployTroops():
                finishBattleAndGoHome()
                # Delete saved gold region screenshots
                for file in gold_region_files:
                    try:
                        os.remove(file)
                        logging.info(f"Deleted temp file: {file}")
                    except Exception as e:
                        logging.warning(f"Failed to delete temp file {file}: {e}")
                gold_region_files.clear()
                logging.info("Attack completed successfully")
                return True
            else:
                logging.error("Troop deployment failed")
                return False
        elif not nextOpponent():
            logging.error("Next opponent failed")
            return False
    logging.warning("No suitable opponent found after 20 attempts")
    return True
def nextOpponent() -> bool:
    """Skip to next opponent."""
    logging.debug("Entering nextOpponent")
    try:
        pos = locate_image("misc/next_opponent.png")
        if not pos:
            logging.error("Next opponent button not found")
            return False
        pyautogui.click(pos)
        updateTimestamp('_lastInteraction')
        time.sleep(3)
        
        for i in range(30):
            if locate_image("misc/battle_screen.png"):
                logging.info("Next battle screen detected")
                time.sleep(3)
                return True
            time.sleep(1)
            logging.debug(f"Waiting for next battle screen, attempt {i + 1}/30")
        logging.error("Next battle screen not found after 30 seconds")
        return False
    except Exception as e:
        logging.error(f"Error in nextOpponent: {traceback.format_exc()}")
        return False
def startAttacking():
    """Start the attack by clicking attack -> find_match -> wait for battle screen."""
    logging.debug("Starting attack process")

    # Step 1: Locate and click attack button
    attack_btn = locate_image("misc/attack_button.png")
    if not attack_btn:
        logging.error("Attack button not found")
        return False
    pyautogui.click(attack_btn)
    logging.info("Clicked attack button")
    time.sleep(1)

    

    #Step 2: Locate and click find_match button
    find_match_btn = None
    for i in range(10):
        find_match_btn = locate_image("misc/find_match.png")
        if find_match_btn:
            break
        time.sleep(1)

    if not find_match_btn:
        logging.error("Find match button not found")
        return False
    pyautogui.click(find_match_btn)
    logging.info("Clicked find match button")
    time.sleep(2)

    attack_btn = locate_image("misc/attack_button_main.png")
    if not attack_btn:
        logging.error("Attack button not found")
        return False
    pyautogui.click(attack_btn)
    logging.info("Clicked attack button main")
    time.sleep(1)

    # Step 3: Wait until battle screen appears
    logging.info("Waiting for battle screen...")
    for i in range(CONFIG['battle_wait_timeout']):
        if locate_image("misc/battle_screen.png"):
            logging.info("Battle screen detected")
            return True
        time.sleep(1)

    logging.error(f"Battle screen not found after {CONFIG['battle_wait_timeout']} seconds")
    return False
def collectorchecker():
    gold_collector = locate_image("misc/gold_mine.png")
    elixir_collector = locate_image("misc/elixir_collector.png")
    dark_collector = locate_image("misc/de_drill.png")
    pyautogui.click(gold_collector)
    pyautogui.click(elixir_collector)
    pyautogui.click(dark_collector)
def trainTroops():
    camp = locate_image("misc/camp.png")
    if not camp:
        camp = locate_image("misc/camp1.png")
    pyautogui.click(camp)
    time.sleep(1)
    pyautogui.click(locate_image("misc/train.PNG"))
    time.sleep(1)
    pyautogui.click(locate_image("misc/train2.PNG"))
    time.sleep(1)
    pyautogui.click(locate_image("misc/train3.PNG"))
    time.sleep(1)
    pyautogui.click(locate_image("misc/close_barracks.PNG"))
def capture_battlefield():
    """Captures a specific area on LDPlayer based on defined coordinates."""
    try:
        windows = gw.getWindowsWithTitle("LDPlayer")
        if not windows:
            logging.error("LDPlayer window not found for battlefield capture")
            return None
        game_window = windows[0]
        base_x, base_y = game_window.left, game_window.top

        # Define bounding box based on user-defined coordinates
        min_x, min_y = 257, 36
        max_x, max_y = 1047, 583
        width = max_x - min_x
        height = max_y - min_y

        screenshot = pyautogui.screenshot(region=(base_x + min_x, base_y + min_y, width, height))
        filepath = "misc/battlefield.png"
        screenshot.save(filepath)
        logging.info("Battlefield screenshot saved (custom area)")
        return filepath
    except Exception as e:
        logging.error(f"Failed to capture battlefield: {traceback.format_exc()}")
        return None
    except Exception as e:
        logging.error(f"Failed to capture battlefield: {traceback.format_exc()}")
        return None

def find_defense_positions(template_path, battlefield_image, threshold=0.75):
    """Finds key defense structures using template matching."""
    try:
        img = cv2.imread(battlefield_image)
        template = cv2.imread(template_path, 0)
        gray_img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        res = cv2.matchTemplate(gray_img, template, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= threshold)
        positions = list(zip(*loc[::-1]))  # Convert to list of (x, y)

        logging.info(f"Found {len(positions)} instances of {template_path}")
        return positions
    except Exception as e:
        logging.error(f"Error in find_defense_positions: {traceback.format_exc()}")
        return []

def detect_dead_base():
    """Detects if the base has full collectors => Dead base."""
    battle_img = capture_battlefield()
    gold_mines = find_defense_positions("misc/gold_mine_full.png", battle_img)
    elixir_collectors = find_defense_positions("misc/elixir_collector_full.png", battle_img)

    if len(gold_mines) >= 4 or len(elixir_collectors) >= 4:
        logging.info("Dead base detected based on full collectors")
        return True
    else:
        logging.info("Base appears active")
        return False
def isGoodOpponentAdvanced():
    """Improves base evaluation using structure analysis."""
    logging.debug("Running advanced base evaluation")
    base_dead = detect_dead_base()
    gold_check = isGoodOpponent()  # Use existing gold threshold logic

    #if base_dead or (gold_check):
    if gold_check:
        logging.info("Base approved by advanced analysis")
        return True
    logging.info("Base rejected by advanced analysis")
    return False
# -----------------------------
# Smart Spell Targeting
# -----------------------------
def deploy_rage_smart():
    """Deploy Rage Spell near real-time troop clusters."""
    battle_img = capture_battlefield()
    troop_centers = find_troop_clusters(battle_img)

    spell_icon = locate_image("troops/rage_spell.PNG")
    if spell_icon and troop_centers:
        pyautogui.click(spell_icon)
        time.sleep(0.3)
        for x, y in troop_centers[:2]:  # Limit to 2 Rage placements
            pyautogui.click(x + randint(-10, 10), y + randint(-10, 10))
            time.sleep(0.2)
        logging.info("Smart Rage Spells deployed near troop clusters")


def deploy_freeze_smart():
    """Deploy Freeze Spell on high-value defenses like Inferno Tower."""
    battle_img = capture_battlefield()
    infernos = find_defense_positions("misc/inferno_tower.png", battle_img)
    air_defense = find_defense_positions("misc/air_defense.png", battle_img)

    if infernos:
        spell_icon = locate_image("troops/freez_spell.PNG")
        if spell_icon:
            pyautogui.click(spell_icon)
            time.sleep(0.3)
            for pos in infernos[:3]:  # Freeze up to 3 targets
                x, y = pos
                pyautogui.click(x + randint(-5, 5), y + randint(-5, 5))
                time.sleep(0.2)
            logging.info("Smart Freeze Spells deployed")
        else:
            logging.warning("Freeze spell icon not found")
    else:
        if air_defense:
            spell_icon = locate_image("troops/freez_spell.PNG")
            if spell_icon:
                pyautogui.click(spell_icon)
                time.sleep(0.3)
                for pos in air_defense[:3]:  # Freeze up to 3 targets
                    x, y = pos
                    pyautogui.click(x + randint(-5, 5), y + randint(-5, 5))
                    time.sleep(0.2)
                logging.info("Smart Freeze Spells deployed")
            else:
                logging.warning("Freeze spell icon not found")
        else:
            logging.info("No Inferno Towers found for freezing")
            pyautogui.click(locate_image("troops/freez_spell.PNG"))
            time.sleep(0.5)
            for x, y in freeze_spell_positions:
                pyautogui.click(x + randint(-5, 5), y + randint(-5, 5))
def find_troop_clusters(image_path):
    """Detects blue-colored troop blobs (Electro Dragons / Balloons)."""
    img = cv2.imread(image_path)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    # Adjusted blue range for better accuracy
    lower_blue = np.array([90, 80, 50])
    upper_blue = np.array([130, 255, 255])

    mask = cv2.inRange(hsv, lower_blue, upper_blue)
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.dilate(mask, kernel, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    positions = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area > 100:  # Increased threshold to filter small noise
            x, y, w, h = cv2.boundingRect(cnt)
            center = (x + w // 2, y + h // 2)
            positions.append(center)

    logging.info(f"Detected {len(positions)} troop clusters")
    return positions
def main_loop():
    """Main automation loop."""
    logging.info("Starting Clash of Clans bot")
    while True:
        try:
            attack()
            time.sleep(30)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logging.error(traceback.format_exc())
            time.sleep(60)

if __name__ == "__main__":
    import threading
    import tkinter as tk
    from datetime import timedelta

    bot_running = False
    start_time = None
    MAX_RUNTIME = timedelta(hours=4)

    bot_thread = None

    def start_bot():
        global bot_running, bot_thread, start_time
        if not bot_running:
            bot_running = True
            start_time = datetime.now()
            status_label.config(text="Status: Running ✅", fg="green")
            update_timer()
            bot_thread = threading.Thread(target=main_loop_wrapper, daemon=True)
            bot_thread.start()
            logging.info("Bot started from GUI")


    def stop_bot():
        global bot_running
        bot_running = False
        status_label.config(text="Status: Stopped ⛔", fg="red")
        timer_label.config(text="Elapsed: 00:00:00")
        logging.info("Bot stop requested from GUI")

    def update_timer():
        if bot_running and start_time:
            elapsed = datetime.now() - start_time
            timer_label.config(text=f"Elapsed: {str(elapsed).split('.')[0]}")
            root.after(1000, update_timer)
        else:
            timer_label.config(text="Elapsed: 00:00:00")


    def restart_bot():
        stop_bot()
        time.sleep(1)
        start_bot()

    def main_loop_wrapper():
        logging.info("Main loop wrapper started")
        start_time = datetime.now()
        while bot_running:
            try:
                if datetime.now() - start_time > MAX_RUNTIME:
                    logging.info("Max runtime of 3 hours reached. Stopping bot.")
                    stop_bot()
                    break
                attack()
                time.sleep(30)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logging.error(traceback.format_exc())
                time.sleep(60)

    # GUI Setup
    root = tk.Tk()
    root.title("Clash Bot Control")
    root.attributes("-topmost", True)
    root.geometry("180x140+1200+10")  # Adjust position as needed
    root.resizable(False, False)
    root.configure(bg="black")

    # Status label (defined before use)
    status_label = tk.Label(root, text="Status: Stopped ⛔", fg="red", bg="black", font=("Arial", 10, "bold"))
    status_label.pack(pady=2)

    # Timer label (defined before use)
    timer_label = tk.Label(root, text="Elapsed: 00:00:00", fg="yellow", bg="black", font=("Arial", 10))
    timer_label.pack(pady=2)

    # Auto Zoomout Checkbox
    auto_zoomout_var = tk.BooleanVar(value=True)  # DEFAULT = enabled
    zoom_chk = tk.Checkbutton(
        root,
        text="Auto Zoomout",
        variable=auto_zoomout_var,
        bg="black",
        fg="white",
        selectcolor="black",
    )
    zoom_chk.pack(pady=2)

    # Auto Wall Upgrade Checkbox
    auto_wall_upgrade_var = tk.BooleanVar(value=True)
    wall_chk = tk.Checkbutton(
        root,
        text="Auto Wall Upgrade",
        variable=auto_wall_upgrade_var,
        bg="black",
        fg="white",
        selectcolor="black",
    )
    wall_chk.pack(pady=2)

    # Buttons
    tk.Button(root, text="Start", command=start_bot, bg="green", fg="white").pack(fill=tk.X, pady=1)
    tk.Button(root, text="Stop", command=stop_bot, bg="red", fg="white").pack(fill=tk.X, pady=1)
    tk.Button(root, text="Restart", command=restart_bot, bg="blue", fg="white").pack(fill=tk.X, pady=1)

    root.mainloop()
