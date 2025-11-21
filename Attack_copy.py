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
    'min_gold_to_attack': 50000000,
    'max_attack_attempts': 20,
    'battle_wait_timeout': 30,
    'gold_region': (60, 125, 125, 25),  # Relative to WINDOW_X, WINDOW_Y
    'retry_attempts': 3,
    'retry_delay': 2,
}

# Global variables
WINDOW_X, WINDOW_Y = 0, 0

# **Troop & Spell Deployment Coordinates**
electro_dragon_positions = [
    (342, 260), (362, 245), (377, 234), (395, 223), (413, 210), 
    (431, 197), (454, 181)
]
balloon_positions = [
    (342, 260), (362, 245), (377, 234), (395, 223), (413, 210), 
    (431, 197), (454, 181), (377, 234), (395, 223), (413, 210)
]
warden_position = (395, 223)
archer_queen_position = (207, 353)

rage_spell_positions = [
    (560, 347), (666, 251),
    (643, 434), (774, 316)
]
freeze_spell_positions = [
    (657, 415), (728, 372), (792, 303)
]

def activate_game_window():
    """Bring LDPlayer window to the foreground."""
    global WINDOW_X, WINDOW_Y
    logging.debug("Activating LDPlayer window")
    try:
        windows = gw.getWindowsWithTitle("LDPlayer")
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
            logging.error("Gold amount is zero. Stopping the program.")
            exit(1)
        
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
        pos = locate_image("end_battle.png")
        if pos:
            pyautogui.click(pos)
            updateTimestamp('_lastInteraction')
            logging.info("Battle ended, returning home")
            return True
        time.sleep(1)

def deployTroops():
    """Deploy troops with strategic positioning and slight randomness."""
    logging.debug("Deploying troops")

    # Deploy **Electro Dragons**
    pyautogui.click(locate_image("troops_electro.png"))
    time.sleep(0.5)
    for x, y in electro_dragon_positions:
        pyautogui.click(x + randint(-10, 10), y + randint(-10, 10))
        time.sleep(0.1)

    # Deploy **Balloons**
    pyautogui.click(locate_image("troops_loon.png"))
    time.sleep(0.1)
    for x, y in balloon_positions:
        pyautogui.click(x + randint(-10, 10), y + randint(-10, 10))
        time.sleep(0.1)

    # Deploy **Warden**
    warden_pos = locate_image("warden.PNG")
    if warden_pos:
        pyautogui.click(warden_pos)
        time.sleep(0.1)
        pyautogui.click(warden_position[0] + randint(-5, 5), warden_position[1] + randint(-5, 5))
    
    # Wait **3 seconds**, then enable **Warden Ability**
    time.sleep(3)
    pyautogui.click(warden_pos)

    # Deploy **Archer Queen**
    archer_queen_pos = locate_image("archer_queen.PNG")
    if archer_queen_pos:
        pyautogui.click(archer_queen_pos)
        time.sleep(0.5)
        pyautogui.click(archer_queen_position[0] + randint(-5, 5), archer_queen_position[1] + randint(-5, 5))

    # Wait **3 seconds**, then deploy **first 2 Rage Spells**
    time.sleep(3)
    pyautogui.click(locate_image("rage_spell.PNG"))
    time.sleep(0.5)
    for x, y in rage_spell_positions[:2]:
        pyautogui.click(x + randint(-5, 5), y + randint(-5, 5))
        time.sleep(0.2)

    # Wait **4 seconds**, then deploy **next 2 Rage Spells**
    time.sleep(6)
    for x, y in rage_spell_positions[2:]:
        pyautogui.click(x + randint(-5, 5), y + randint(-5, 5))
        time.sleep(0.2)

    # Deploy **Freeze Spells** together
    time.sleep(2)
    pyautogui.click(locate_image("freez_spell.PNG"))
    time.sleep(0.5)
    for x, y in freeze_spell_positions:
        pyautogui.click(x + randint(-5, 5), y + randint(-5, 5))

    logging.info("Troop and spell deployment complete")
    return True

def attack():
    """Handle full attack sequence."""
    logging.debug("Starting attack")

    if not activate_game_window():
        return False
    if not startAttacking():
        return False
    if not zoomOutAndCenter():
        return False
    for attempt in range(20):
        logging.debug(f"Attack attempt {attempt + 1}/20")
        if isGoodOpponent():
            if deployTroops():
                finishBattleAndGoHome()
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
        pos = locate_image("next_opponent.png")
        if not pos:
            logging.error("Next opponent button not found")
            return False
        pyautogui.click(pos)
        updateTimestamp('_lastInteraction')
        time.sleep(3)
        
        for i in range(30):
            if locate_image("battle_screen.png"):
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
    attack_btn = locate_image("attack_button.png")
    if not attack_btn:
        logging.error("Attack button not found")
        return False
    pyautogui.click(attack_btn)
    logging.info("Clicked attack button")
    time.sleep(1)

    # Step 2: Locate and click find_match button
    find_match_btn = None
    for i in range(10):
        find_match_btn = locate_image("find_match.png")
        if find_match_btn:
            break
        time.sleep(1)

    if not find_match_btn:
        logging.error("Find match button not found")
        return False
    pyautogui.click(find_match_btn)
    logging.info("Clicked find match button")
    time.sleep(2)

    # Step 3: Wait until battle screen appears
    logging.info("Waiting for battle screen...")
    for i in range(CONFIG['battle_wait_timeout']):
        if locate_image("battle_screen.png"):
            logging.info("Battle screen detected")
            return True
        time.sleep(1)

    logging.error(f"Battle screen not found after {CONFIG['battle_wait_timeout']} seconds")
    return False


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
    main_loop()
