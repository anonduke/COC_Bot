import pyautogui
import pygetwindow as gw
import cv2
import numpy as np
import time
import os
import random
import traceback

# ======================================================
#  REGIONS (YOUR FINAL VALUES)
# ======================================================
DONATE_BUTTON_REGION = (633, 360, 522, 857)        # donate button only
TROOP_BAR_REGION     = (1163, 658, 731, 626)       # troops area
SPELL_REGION         = (1185, 1080, 693, 132)      # corrected spell region

GAME_TITLE = "Clash of Clans - FasaluRahmanfrkp"

# SPEED CONFIG
CLICK_DELAY = (0.02, 0.05)
SCAN_DELAY  = (0.08, 0.15)
MAX_DONATE_TIME = 1.4

RANDOM_MOUSE = True
MOUSE_CHANCE = 0.25

# ======================================================
#  THRESHOLDS — VERY IMPORTANT
# ======================================================
THRESHOLDS = {
    "troop": 0.58,     # your match score = ~0.617 → must be < that
    "spell": 0.60,
    "siege": 0.60
}

# ======================================================
#  MULTI CLICK TABLE
# ======================================================
MULTI_CLICK = {
    "archer": 4, "barbarian": 4, "goblin": 4,
    "wizard": 2, "giant": 2, "wallbreaker": 2,
    "minion": 3,

    # Single-unit troops
    "dragon": 1, "pekka": 1, "healer": 1,
    "electro_titan": 1, "yeti": 1, "hog": 1,
    "valkyrie": 1, "root_rider": 1, "edrag": 1,
    "dragon_rider": 1,

    # Spells
    "lightning": 1, "rage": 1, "freeze": 1, "jump": 1,
    "heal": 1, "poison": 1, "quake": 1, "invisibility": 1,
    "haste": 1, "bat": 1, "skeleton": 1, "recall": 1,
    "overgrowth": 1, "iceblock": 1,

    # Siege machines
    "wall_wrecker": 1, "battle_blimp": 1, "stone_slammer": 1,
    "siege_barracks": 1, "log_launcher": 1, "flame_flinger": 1,
    "battle_drill": 1,
}

# ======================================================
#  LOAD PNG IMAGES
# ======================================================
loaded_misc = {}
loaded_troops = {}

def preload_images():
    for f in os.listdir("misc"):
        if f.endswith(".png"):
            loaded_misc[f] = cv2.imread(os.path.join("misc", f), cv2.IMREAD_COLOR)

    for f in os.listdir("troops"):
        if f.endswith(".png"):
            loaded_troops[f] = cv2.imread(os.path.join("troops", f), cv2.IMREAD_COLOR)

    print(f"[+] Loaded {len(loaded_misc)} misc images")
    print(f"[+] Loaded {len(loaded_troops)} troop images")

# ======================================================
#  ACTIVATE GAME WINDOW
# ======================================================
def activate_window():
    try:
        win = gw.getWindowsWithTitle(GAME_TITLE)[0]
        win.activate()
        win.restore()
        time.sleep(0.4)
        return True
    except:
        print("❌ Game window not found!")
        return False

# ======================================================
#  SMART TEMPLATE MATCH (HSV + AUTO-RESIZE + COLOR CHECK)
# ======================================================
def match_template(img_file, region, threshold, is_troop=False):
    """Detect troop/spell with correct color (skip grayscale icons)."""

    try:
        template = loaded_troops[img_file] if is_troop else loaded_misc[img_file]
        th, tw = template.shape[:2]

        # Resize for scaling accuracy
        template_small = cv2.resize(template, (int(tw * 0.75), int(th * 0.75)))

        # Convert to HSV
        template_hsv = cv2.cvtColor(template_small, cv2.COLOR_BGR2HSV)
        template_hsv = cv2.GaussianBlur(template_hsv, (3, 3), 0)

        # Capture region
        screenshot = pyautogui.screenshot(region=region)
        scr_bgr = np.array(screenshot)
        scr_hsv = cv2.cvtColor(scr_bgr, cv2.COLOR_BGR2HSV)
        scr_hsv = cv2.GaussianBlur(scr_hsv, (3, 3), 0)

        # Template match
        res = cv2.matchTemplate(scr_hsv, template_hsv, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)

        if max_val < threshold:
            return None

        # Coordinates for clicking
        cx = region[0] + max_loc[0] + template_small.shape[1] // 2
        cy = region[1] + max_loc[1] + template_small.shape[0] // 2

        # ======================================================
        #  COLOR CHECK → DO NOT CLICK GRAYED TROOPS
        # ======================================================
        if is_troop:
            x1 = max_loc[0]
            y1 = max_loc[1]
            x2 = x1 + template_small.shape[1]
            y2 = y1 + template_small.shape[0]

            troop_box = scr_hsv[y1:y2, x1:x2]
            sat_mean = troop_box[:, :, 1].mean()  # saturation value

            if sat_mean < 35:      # Gray icons have LOW saturation
                return None        # SKIP GRAY TROOPS

        return (cx, cy)

    except Exception as e:
        print("Error:", e)
        return None

# ======================================================
#  HUMAN-LIKE MOUSE
# ======================================================
def random_mouse():
    if random.random() > MOUSE_CHANCE:
        return
    try:
        win = gw.getWindowsWithTitle(GAME_TITLE)[0]
        rx = random.randint(win.left+50, win.left+win.width-50)
        ry = random.randint(win.top+50, win.top+win.height-50)
        pyautogui.moveTo(rx, ry, duration=random.uniform(0.10, 0.24))
    except:
        pass

# ======================================================
#  CLICK DONATE BUTTON
# ======================================================
def click_donate_button():
    img = "donate_button.png"
    if img not in loaded_misc:
        print("❌ donate_button.png missing")
        return False

    pos = match_template(img, DONATE_BUTTON_REGION, 0.65, False)
    if pos:
        print("👉 DONATE CLICKED")
        pyautogui.click(pos)
        time.sleep(random.uniform(*CLICK_DELAY))
        return True
    return False

# ======================================================
#  DETECT TROOP/SPELL
# ======================================================
def detect_position(img_file):
    name = img_file.replace(".png", "").replace("troop_", "")

    # Spell?
    if name in MULTI_CLICK and MULTI_CLICK[name] == 1 and name in [
        "lightning","rage","jump","freeze","heal","poison","quake",
        "invisibility","haste","bat","skeleton","recall","overgrowth","iceblock"
    ]:
        return match_template(img_file, SPELL_REGION, THRESHOLDS["spell"], True)

    # Siege?
    if name in ["wall_wrecker","battle_blimp","stone_slammer","siege_barracks",
                "log_launcher","flame_flinger","battle_drill"]:
        return match_template(img_file, TROOP_BAR_REGION, THRESHOLDS["siege"], True)

    # Troop
    return match_template(img_file, TROOP_BAR_REGION, THRESHOLDS["troop"], True)

# ======================================================
#  DONATE UNITS
# ======================================================
def donate_units():
    start = time.time()
    donated = 0

    for img_file in loaded_troops:
        if time.time() - start > MAX_DONATE_TIME:
            break

        pos = detect_position(img_file)

        if pos:
            name = img_file.replace(".png", "").replace("troop_", "")
            clicks = MULTI_CLICK.get(name, 1)

            print("  ➜ Donating:", name)

            for _ in range(clicks):
                pyautogui.click(pos)
                time.sleep(random.uniform(*CLICK_DELAY))

            donated += clicks

    return donated

# ======================================================
#  CLOSE WINDOW
# ======================================================
def close_window():
    img = "close_donate.png"
    if img not in loaded_misc:
        return False

    pos = match_template(img, TROOP_BAR_REGION, 0.60, False)
    if pos:
        pyautogui.click(pos)
        time.sleep(0.1)
        return True
    return False

# ======================================================
#  MAIN LOOP
# ======================================================
def main():
    preload_images()

    if not activate_window():
        return

    total = 0
    print("🤖 Donation Bot Running...")

    while True:
        try:
            random_mouse()

            if click_donate_button():
                time.sleep(0.15)

                donated = donate_units()
                total += donated
                print(f"✔ Donated: {donated} | Total: {total}")

                close_window()

            time.sleep(random.uniform(*SCAN_DELAY))

        except KeyboardInterrupt:
            print("🛑 Bot stopped")
            break

        except:
            print(traceback.format_exc())
            time.sleep(1)

if __name__ == "__main__":
    main()
