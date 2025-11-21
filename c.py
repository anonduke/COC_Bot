from datetime import datetime
from math import floor
from random import randint
import time
from typing import List, Dict
import logging
import pyautogui
import pygetwindow as gw
import traceback
from PIL import Image, ImageDraw, ImageGrab
import os
import pytesseract

# Enhanced Logging Configuration
logging.basicConfig(
    filename='coc_bot_detailed.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(funcName)s - %(message)s'
)

# Global variables
timestamps: Dict[str, datetime] = {
    'start': None,
    'trainTroops': None,
    'clearObstacles': None,
    'collectResources': None,
    'collectStats': None,
    '_lastInteraction': None
}

# Army composition (adjust as needed for 2025 meta)
myArmy: List[int] = [80, 80, 10, 20, 8, 0, 0, 0, 0, 0]  # Barbs, Archers, Giants, Goblins, WB
trainTimes: List[int] = [20, 25, 120, 30, 120, 480, 480, 900, 1800, 2700]  # Verify these

minGoldToAttack: int = 200000
minElixirToAttack: int = 0
minDeToAttack: int = 0

# LDPlayer window position
WINDOW_X, WINDOW_Y = 0, 0

def capture_screenshot(prefix="error"):
    """Capture and save a screenshot for debugging (unused)."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"{prefix}_{timestamp}.png"
    pyautogui.screenshot(filename)
    logging.error(f"Screenshot saved as {filename}")
    return filename

def draw_green_square(x, y, width, height, duration=1):
    """Draw a green square on the screen at the specified coordinates for a duration."""
    try:
        # Capture the current screen
        screenshot = pyautogui.screenshot()
        img = screenshot.convert("RGBA")
        draw = ImageDraw.Draw(img)
        
        # Draw green rectangle (left, top, right, bottom)
        draw.rectangle(
            [(x, y), (x + width, y + height)],
            outline=(0, 255, 0, 255),  # Green color in RGBA
            width=2
        )
        
        # Save to a temporary file
        temp_file = "temp_overlay.png"
        img.save(temp_file)
        
        # Display using default image viewer (Windows-specific)
        os.startfile(temp_file)
        time.sleep(duration)
        os.remove(temp_file)  # Clean up
    except Exception as e:
        logging.error(f"Error drawing green square: {traceback.format_exc()}")

def activate_game_window():
    """Bring LDPlayer window to the foreground."""
    global WINDOW_X, WINDOW_Y
    logging.debug("Attempting to activate LDPlayer window")
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

def locate_image(image_path, confidence=0.8, region=None):
    """Enhanced image detection with detailed logging."""
    logging.debug(f"Searching for '{image_path}' with confidence={confidence}, region={region}")
    try:
        pos = pyautogui.locateCenterOnScreen(image_path, confidence=confidence, region=region)
        if pos:
            logging.info(f"Found '{image_path}' at {pos}")
        else:
            logging.warning(f"'{image_path}' not found with confidence {confidence}")
        return pos
    except pyautogui.ImageNotFoundException:
        logging.warning(f"'{image_path}' not found (confidence too low)")
        return None
    except Exception as e:
        logging.error(f"Error locating '{image_path}': {traceback.format_exc()}")
        return None

def zoomOutAndCenter() -> None:
    """Zoom out and center view at 1280x720."""
    logging.debug("Entering zoomOutAndCenter")
    if not activate_game_window():
        logging.error("Could not proceed due to window activation failure")
        return
    logging.info("Zooming out and centering village")
    pyautogui.hotkey('ctrl', '-')
    time.sleep(0.5)
    pyautogui.hotkey('ctrl', '-')
    time.sleep(0.5)
    pyautogui.hotkey('ctrl', '-')
    time.sleep(0.5)
    updateTimestamp('_lastInteraction')
    logging.debug("Zoom out completed")

def attack() -> bool:
    """Handle attack sequence."""
    logging.debug("Entering attack")
    if not activate_game_window():
        logging.error("Window activation failed")
        return False
    if not startAttacking():
        logging.error("Start attacking failed")
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

def startAttacking() -> bool:
    """Initiate attack."""
    logging.debug("Entering startAttacking")
    try:
        pos = locate_image("attack_button.png")
        if not pos:
            logging.error("Attack button not found")
            return False
        pyautogui.click(pos)
        updateTimestamp('_lastInteraction')
        time.sleep(1)
        
        pos = locate_image("find_match.png")
        if not pos:
            logging.error("Find match button not found")
            return False
        pyautogui.click(pos)
        updateTimestamp('_lastInteraction')
        time.sleep(1)
        
        for i in range(30):
            if locate_image("battle_screen.png"):
                logging.info("Battle screen detected")
                return True
            time.sleep(1)
            logging.debug(f"Waiting for battle screen, attempt {i + 1}/30")
        logging.error("Battle screen not found after 30 seconds")
        return False
    except Exception as e:
        logging.error(f"Error in startAttacking: {traceback.format_exc()}")
        return False

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

def isGoodOpponent() -> bool:
    """Check if opponent has enough loot with visual gold region indicator."""
    logging.debug("Entering isGoodOpponent")
    #goldRegion = (WINDOW_X + 60, WINDOW_Y + 125, 125, 25)  # Updated to your latest value
    goldRegion = (149, 130, 149 + 125, 130 + 25)
    elixirRegion = (WINDOW_X + 100, WINDOW_Y + 149, 135, 25)
    deRegion = (WINDOW_X + 100, WINDOW_Y + 188, 125, 25)
    
    try:
        # Draw green square around gold region only
        logging.debug(f"Highlighting gold region: {goldRegion}")
        draw_green_square(goldRegion[0], goldRegion[1], goldRegion[2], goldRegion[3], duration=2)
        
        gold = numberOCR(goldRegion, 'opponentLoot')
        elixir = numberOCR(elixirRegion, 'opponentLoot')
        de = numberOCR(deRegion, 'opponentLoot')
        
        logging.info(f"Detected loot - Gold: {gold}, Elixir: {elixir}, DE: {de}")
        result = (gold >= minGoldToAttack and 
                  elixir >= minElixirToAttack and 
                  de >= minDeToAttack)
        logging.debug(f"Is good opponent? {result}")
        return result
    except Exception as e:
        logging.error(f"Error in isGoodOpponent: {traceback.format_exc()}")
        return False

def deployTroops() -> bool:
    """Deploy troops at 1280x720."""
    logging.debug("Entering deployTroops")
    try:
        landmark = locate_image("attack_landmark.png")
        if not landmark:
            logging.error("Attack landmark not found")
            return False
        
        pyautogui.moveTo(landmark)
        logging.debug(f"Moved to landmark at {landmark}")
        
        deployPoints = [
            (landmark[0] + 200, landmark[1] + 100),
            (landmark[0] + 250, landmark[1] + 75),
            (landmark[0] + 300, landmark[1] + 50),
            (landmark[0] + 350, landmark[1] + 25),
            (landmark[0] + 150, landmark[1] + 125),
        ]
        
        troops = {
            "barbarians": "troops_barbarians.png",
            "archers": "troops_archers.png",
            "giants": "troops_giants.png",
            "goblins": "troops_goblins.png",
            "wallbreakers": "troops_wallbreakers.png"
        }
        
        for troop_type, img in troops.items():
            pos = locate_image(img)
            if pos:
                pyautogui.click(pos)
                troop_count = myArmy[{"barbarians": 0, "archers": 1, "giants": 2, 
                                      "goblins": 3, "wallbreakers": 4}[troop_type]]
                logging.debug(f"Deploying {troop_count} {troop_type}")
                for _ in range(troop_count // 2):
                    deploy_pos = deployPoints[randint(0, len(deployPoints)-1)]
                    pyautogui.click(deploy_pos, duration=0.1, pause=0.05)
                    logging.debug(f"Clicked deployment at {deploy_pos}")
            else:
                logging.warning(f"Could not find {troop_type}")
        
        updateTimestamp('_lastInteraction')
        logging.info("Troops deployed successfully")
        return True
    except Exception as e:
        logging.error(f"Error in deployTroops: {traceback.format_exc()}")
        return False

def finishBattleAndGoHome() -> bool:
    """End battle and return to village."""
    logging.debug("Entering finishBattleAndGoHome")
    try:
        while True:
            pos = locate_image("end_battle.png")
            if pos:
                pyautogui.click(pos)
                updateTimestamp('_lastInteraction')
                logging.info("Battle ended, returning home")
                return True
            time.sleep(1)
            logging.debug("Waiting for end battle button")
    except Exception as e:
        logging.error(f"Error in finishBattleAndGoHome: {traceback.format_exc()}")
        return False

def trainTroops(troops: List[int]) -> None:
    """Train troops."""
    logging.debug("Entering trainTroops")
    village = (WINDOW_X + 210, WINDOW_Y + 85, 860, 550)
    
    theBarracks = [[0]*10 for _ in range(4)]
    totalTroops = 0
    
    for index, count in enumerate(troops):
        for i in range(4):
            theBarracks[i][index] = floor(count / 4)
        totalTroops += count
        if count % 4 > 0:
            for i in range(count % 4):
                j = barracksWithLeastTroops(theBarracks)
                theBarracks[j][index] += 1
    
    if totalTroops == 0:
        logging.info("No troops to train")
        return
    
    try:
        pos = locate_image("barracks_icon.png")
        if not pos:
            logging.error("Barracks icon not found")
            return
        pyautogui.click(pos)
        updateTimestamp('_lastInteraction')
        time.sleep(0.5)
        
        for i in range(4):
            logging.debug(f"Training in barracks {i + 1}")
            for index, qty in enumerate(theBarracks[i]):
                if qty > 0:
                    target = {
                        0: "troops_barbarians.png",
                        1: "troops_archers.png",
                        2: "troops_giants.png",
                        3: "troops_goblins.png",
                        4: "troops_wallbreakers.png"
                    }.get(index)
                    if target:
                        pos = locate_image(target, region=village)
                        if pos:
                            for _ in range(qty):
                                pyautogui.click(pos)
                                updateTimestamp('_lastInteraction')
                                time.sleep(0.1)
                                logging.debug(f"Clicked {target} at {pos}")
                        else:
                            logging.warning(f"Could not find {target}")
            pos = locate_image("next_barracks.png")
            if pos:
                pyautogui.click(pos)
                time.sleep(0.5)
            else:
                logging.error("Next barracks button not found")
                break
        
        pos = locate_image("close_barracks.png")
        if pos:
            pyautogui.click(pos)
            updateTimestamp('trainTroops')
            logging.info("Troops training completed")
        else:
            logging.error("Close barracks button not found")
    except Exception as e:
        logging.error(f"Error in trainTroops: {traceback.format_exc()}")

def barracksWithLeastTroops(barracks: List[List[int]]) -> int:
    """Find barracks with least training time."""
    logging.debug("Entering barracksWithLeastTroops")
    lowest_time = calcTrainTime(barracks[0])
    lowest_i = 0
    
    for i, barrack in enumerate(barracks):
        time = calcTrainTime(barrack)
        if time < lowest_time:
            lowest_i = i
            lowest_time = time
    logging.debug(f"Selected barracks {lowest_i} with time {lowest_time}")
    return lowest_i

def calcTrainTime(barrack: List[int]) -> int:
    """Calculate training time for a barrack."""
    logging.debug("Entering calcTrainTime")
    time = sum(count * trainTimes[i] for i, count in enumerate(barrack))
    logging.debug(f"Calculated time: {time}")
    return time

def collectResources() -> None:
    """Collect resources."""
    logging.debug("Entering collectResources")
    resources = ["gold_mine.png", "elixir_collector.png", "de_drill.png"]
    window = (WINDOW_X + 210, WINDOW_Y + 85, 860, 550)
    
    try:
        for resource in resources:
            logging.debug(f"Searching for {resource}")
            for pos in pyautogui.locateAllOnScreen(resource, confidence=0.8, region=window):
                logging.debug(f"Found {resource} at {pos}")
                pyautogui.click(pos)
                updateTimestamp('_lastInteraction')
        logging.info("Resources collected")
    except Exception as e:
        logging.error(f"Error in collectResources: {traceback.format_exc()}")

def preventIdle() -> None:
    """Prevent idle timeout."""
    logging.debug("Entering preventIdle")
    try:
        pos = (640 + WINDOW_X, 360 + WINDOW_Y)
        pyautogui.click(pos)
        updateTimestamp('_lastInteraction')
        time.sleep(1)
        logging.debug(f"Clicked at {pos} to prevent idle")
    except Exception as e:
        logging.error(f"Error in preventIdle: {traceback.format_exc()}")

def numberOCR(region: tuple, ocrType: str) -> int:
    """OCR for numbers using Tesseract."""
    logging.debug(f"Entering numberOCR with region {region}")
    
    try:
        # Capture the specified region as an image
        image = ImageGrab.grab(bbox=region)
        
        # Convert image to grayscale to improve OCR accuracy
        gray_image = image.convert("L")
        
        # Perform OCR with Tesseract, specifying only digits for better accuracy
        extracted_text = pytesseract.image_to_string(gray_image, config='--psm 6 digits')
        
        # Clean and filter the result to extract numbers only
        result = int(''.join(filter(str.isdigit, extracted_text)))
        
        logging.debug(f"OCR result: {result}")
        return result

    except Exception as e:
        logging.error(f"Error in numberOCR: {traceback.format_exc()}")
        return 0
    
def timeToTrainArmy() -> int:
    """Estimate army training time."""
    logging.debug("Entering timeToTrainArmy")
    time = 1455  # Adjust based on current meta
    logging.debug(f"Returning training time: {time}")
    return time

def updateTimestamp(timer: str) -> None:
    """Update timestamp."""
    logging.debug(f"Entering updateTimestamp with timer {timer}")
    if timer in timestamps:
        timestamps[timer] = datetime.now()
        logging.debug(f"Updated timestamp {timer} to {timestamps[timer]}")
    else:
        logging.warning(f"Invalid timestamp: {timer}")

def main_loop():
    """Main automation loop."""
    logging.debug("Entering main_loop")
    if not activate_game_window():
        logging.error("Failed to activate game window. Exiting.")
        return
    
    zoomOutAndCenter()
    logging.info("Clash of Clans bot started")
    
    while True:
        try:
            logging.debug("Starting loop iteration")
            
            if not timestamps['collectResources'] or \
               (datetime.now() - timestamps['collectResources']).seconds > 900:
                logging.info("Collecting resources")
                collectResources()
            
            if not timestamps['trainTroops'] or \
               (datetime.now() - timestamps['trainTroops']).seconds > timeToTrainArmy():
                logging.info("Starting attack cycle")
                trainTroops(myArmy)
                attack()
            
            if not timestamps['_lastInteraction'] or \
               (datetime.now() - timestamps['_lastInteraction']).seconds > 120:
                logging.info("Preventing idle")
                preventIdle()
            
            logging.debug("Sleeping for 30 seconds")
            time.sleep(30)
        except KeyboardInterrupt:
            logging.info("Script stopped by user (Ctrl+C)")
            break
        except Exception as e:
            logging.error(f"Main loop error: {traceback.format_exc()}")
            time.sleep(60)

if __name__ == "__main__":
    logging.info("Script started")
    try:
        main_loop()
    except Exception as e:
        logging.critical(f"Script crashed: {traceback.format_exc()}")