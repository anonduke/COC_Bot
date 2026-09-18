# wall_upgrade_simulation_test.py
import time
import threading
import traceback
import pyautogui
import pytesseract
import cv2
import numpy as np
import pygetwindow as gw

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# -------------------------------------------
#  CONFIG
# -------------------------------------------
DEBUG = 1  # 1 = verbose OCR prints; 0 = silent

# -------------------------------------------
#  RELATIVE OFFSETS (x, y, w, h)
# -------------------------------------------
GOLD_OFFSET    = (947,  58, 182, 27)
ELIXIR_OFFSET  = (945, 121, 186, 31)

BUILDER_AREA   = (581,  55,  76,  34)  # shows "X/Y" free/total builders
LIST_AREA      = (437, 130, 202, 422)  # upgrade list popup region
GOLD_BTN       = (681, 575,  98,  30)  # "Upgrade with Gold" button
ELIXIR_BTN     = (811, 579, 105,  28)  # "Upgrade with Elixir" button
CONFIRM_BTN    = (755, 590, 171, 59)  # "Confirm" button after upgrade
CLOSE_BTN      = (1125, 299,  40, 17) # dismiss/back button — escapes stuck screens
# Wall cost
WALL_PRICE = 8_000_000

# Reference images for "resource is full" detection
GOLD_FULL_REF   = "Gold_Full.PNG"
ELIXIR_FULL_REF = "Elixir_Full.PNG"

FULL_MATCH_THRESHOLD = 0.1

# List scrolling
LIST_SCROLL_AMOUNT = -600
MAX_SCROLL_TRIES   = 15

# Global window position (set by activate_game_window)
WINDOW_X, WINDOW_Y = 0, 0


# ---------------------------------------------------------
#  ACTIVATE LDPLAYER  (mirrors Ranked_Copy.py pattern)
# ---------------------------------------------------------
def activate_game_window():
    global WINDOW_X, WINDOW_Y
    try:
        windows = gw.getWindowsWithTitle("LDPlayer")
        if not windows:
            print("❌ No LDPlayer window found")
            return False

        game_window = windows[0]
        game_window.activate()
        game_window.restore()
        WINDOW_X, WINDOW_Y = game_window.left, game_window.top
        print(f"[INFO] LDPlayer activated at ({WINDOW_X}, {WINDOW_Y})")
        return True

    except Exception:
        print(f"❌ Failed to activate LDPlayer:\n{traceback.format_exc()}")
        return False


# ---------------------------------------------------------
#  GET ABSOLUTE REGION FROM LDPLAYER WINDOW + RELATIVE OFFSET
# ---------------------------------------------------------
def get_absolute_region(offset):
    if not activate_game_window():
        return None

    x = WINDOW_X + offset[0]
    y = WINDOW_Y + offset[1]
    w = offset[2]
    h = offset[3]

    return (x, y, w, h)


# ---------------------------------------------------------
#  DEBUG: Screenshot of LDPlayer with red boxes on offsets
# ---------------------------------------------------------
def show_debug_overlay():
    if not activate_game_window():
        print("[DEBUG] Cannot draw overlay — LDPlayer not available")
        return

    windows = gw.getWindowsWithTitle("LDPlayer")
    win = windows[0]
    print(f"[DEBUG] LDPlayer window → left={win.left}, top={win.top}, w={win.width}, h={win.height}")

    # Full window screenshot (window is already on top from activate_game_window)
    screenshot = pyautogui.screenshot(region=(win.left, win.top, win.width, win.height))
    img = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

    # Red box + label for GOLD (offsets are relative to window top-left)
    gx, gy, gw_size, gh_size = GOLD_OFFSET
    cv2.rectangle(img, (gx, gy), (gx + gw_size, gy + gh_size), (0, 0, 255), 2)
    cv2.putText(img, "GOLD", (gx, gy - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
    print(f"[DEBUG] GOLD   offset → x={gx}, y={gy}, w={gw_size}, h={gh_size}")

    # Red box + label for ELIXIR
    ex, ey, ew_size, eh_size = ELIXIR_OFFSET
    cv2.rectangle(img, (ex, ey), (ex + ew_size, ey + eh_size), (0, 0, 255), 2)
    cv2.putText(img, "ELIXIR", (ex, ey - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
    print(f"[DEBUG] ELIXIR offset → x={ex}, y={ey}, w={ew_size}, h={eh_size}")

    cv2.imshow("DEBUG — Gold & Elixir Offsets  (press any key to close)", img)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


# ---------------------------------------------------------
#  CoC SPECIAL NUMBER PARSER (FINAL FIX)
# ---------------------------------------------------------
def parse_coc_number(raw):
    # keep digits + spaces
    cleaned = "".join(ch for ch in raw if ch.isdigit() or ch == " ").strip()

    # normalize spacing
    cleaned = " ".join(cleaned.split())

    # only digits
    only_digits = "".join(ch for ch in cleaned if ch.isdigit())

    # case: returned with spaces ("27 661 335")
    if " " in cleaned:
        parts = cleaned.split()
        merged = "".join(parts)
        if merged.isdigit():
            return int(merged)

    # case: no spaces but correct digits ("27661335")
    if len(only_digits) >= 6:
        # max storage is 8 digits → trim leading artifacts
        if len(only_digits) > 8:
            only_digits = only_digits[-8:]
        return int(only_digits)

    return 0


# ---------------------------------------------------------
#  OCR FUNCTION (gold/elixir)
# ---------------------------------------------------------
def read_storage(offset, label="resource", debug_name="debug.png"):
    region = get_absolute_region(offset)
    if not region:
        return 0

    # screenshot
    img_pil = pyautogui.screenshot(region=region)
    img_pil.save(debug_name)

    img_rgb = np.array(img_pil)
    img_up  = cv2.resize(img_rgb, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)

    # Extract white text using HSV:
    #   - White pixels: high Value (bright) AND low Saturation (not coloured)
    #   - Yellow gold bar: high Value BUT also high Saturation  → excluded
    #   - Purple elixir bar: low Saturation score but also low Value → excluded
    #   - Dark background: low Value → excluded
    # This works regardless of how full the bar is.
    hsv = cv2.cvtColor(img_up, cv2.COLOR_RGB2HSV)
    _, s, v = cv2.split(hsv)
    white_mask = cv2.bitwise_and(
        cv2.threshold(v, 180, 255, cv2.THRESH_BINARY)[1],      # bright
        cv2.threshold(s,  40, 255, cv2.THRESH_BINARY_INV)[1],  # not coloured
    )
    img_inv = cv2.bitwise_not(white_mask)   # Tesseract needs black-on-white

    debug_pre = debug_name.replace(".png", "_pre.png")
    cv2.imwrite(debug_pre, img_inv)

    raw = pytesseract.image_to_string(
        img_inv,
        config="--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789 "
    )

    result = parse_coc_number(raw)

    if DEBUG:
        print(f"[DEBUG] {label}: region     = {region}")
        print(f"[DEBUG] {label}: raw_ocr    = '{raw.strip()}'")
        print(f"[DEBUG] {label}: parsed     = {result:,}")
        print(f"[DEBUG] {label}: raw img    = '{debug_name}'")
        print(f"[DEBUG] {label}: pre img    = '{debug_pre}'")

    return result


# ---------------------------------------------------------
#  RESOURCE FULL CHECK  (image comparison against reference PNG)
# ---------------------------------------------------------
def is_resource_full(offset, reference_path, label="resource"):
    """Returns True if the current resource bar pixel-matches the full-resource reference."""
    region = get_absolute_region(offset)
    if not region:
        return False

    # Capture current state of the resource bar
    current_pil = pyautogui.screenshot(region=region)
    current_img = cv2.cvtColor(np.array(current_pil), cv2.COLOR_RGB2BGR)

    # Load reference full-resource image
    ref_img = cv2.imread(reference_path)
    if ref_img is None:
        print(f"[DEBUG] {label}: ❌ reference not found at '{reference_path}'")
        return False

    if DEBUG:
        print(f"[DEBUG] {label}: current size = {current_img.shape}  (h x w x c)")
        print(f"[DEBUG] {label}: ref     size = {ref_img.shape}  (h x w x c)")

    # Ensure sizes match before comparing (resize current to ref if different)
    if current_img.shape != ref_img.shape:
        ref_h, ref_w = ref_img.shape[:2]
        current_img = cv2.resize(current_img, (ref_w, ref_h))
        if DEBUG:
            print(f"[DEBUG] {label}: resized current → {current_img.shape}")

    # TM_CCOEFF_NORMED: 1.0 = perfect match, 0.0 = no match
    result  = cv2.matchTemplate(current_img, ref_img, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(result)
    is_full = max_val >= FULL_MATCH_THRESHOLD

    if DEBUG:
        print(f"[DEBUG] {label}: match_score = {max_val:.6f}  (threshold = {FULL_MATCH_THRESHOLD})")
        print(f"[DEBUG] {label}: IS FULL     = {is_full}")

    return is_full


# ---------------------------------------------------------
#  BUILDER COUNT  (reads "X/Y" from builder indicator)
# ---------------------------------------------------------
def read_builder_count():
    """
    OCR the builder area for 'X/Y' format.
    Returns (free, total).  Returns (0, 0) on failure.
    """
    import re
    region = (WINDOW_X + BUILDER_AREA[0], WINDOW_Y + BUILDER_AREA[1],
              BUILDER_AREA[2], BUILDER_AREA[3])

    img_pil = pyautogui.screenshot(region=region)
    img_pil.save("debug_builder_count.png")

    img_rgb = np.array(img_pil)
    img_up  = cv2.resize(img_rgb, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)

    # HSV white-text extraction
    hsv = cv2.cvtColor(img_up, cv2.COLOR_RGB2HSV)
    _, s, v = cv2.split(hsv)
    white_mask = cv2.bitwise_and(
        cv2.threshold(v, 160, 255, cv2.THRESH_BINARY)[1],
        cv2.threshold(s,  50, 255, cv2.THRESH_BINARY_INV)[1],
    )
    img_inv = cv2.bitwise_not(white_mask)

    # Crop central 70% of height to remove top/bottom UI chrome bars
    h_inv, w_inv = img_inv.shape
    margin = int(h_inv * 0.15)
    img_cropped = img_inv[margin: h_inv - margin, :]

    # Pad so edge characters aren't clipped by Tesseract
    img_padded = cv2.copyMakeBorder(img_cropped, 20, 20, 20, 20,
                                     cv2.BORDER_CONSTANT, value=255)
    cv2.imwrite("debug_builder_count_pre.png", img_padded)

    print(f"[BUILDER] img saved: debug_builder_count.png / debug_builder_count_pre.png")

    # ── Approach 1: image_to_data finds "/" position; crop left of it for free digit
    for oem in [1, 3]:
        data = pytesseract.image_to_data(
            img_padded,
            config=f"--oem {oem} --psm 11 -c tessedit_char_whitelist=0123456789/",
            output_type=pytesseract.Output.DICT
        )
        slash_x = None
        total_val = None
        for i in range(len(data["text"])):
            txt = data["text"][i].strip()
            if not txt:
                continue
            # "/" token (or "/5" merged) — note its left edge
            if txt.startswith("/"):
                slash_x = data["left"][i]
                digits_after = re.findall(r'\d+', txt)
                if digits_after:
                    total_val = int(digits_after[-1])
            elif re.fullmatch(r'\d+', txt) and total_val is None:
                total_val = int(txt)

        print(f"  [BUILDER] oem={oem} slash_x={slash_x}  total_val={total_val}")

        if slash_x is not None and slash_x > 5:
            # OCR the strip to the LEFT of "/" — that's the free-builder digit
            left_strip = img_padded[:, :slash_x]
            cv2.imwrite("debug_builder_left.png", left_strip)
            for p in [10, 8, 7]:
                free_raw = pytesseract.image_to_string(
                    left_strip,
                    config=f"--oem {oem} --psm {p} -c tessedit_char_whitelist=0123456789"
                ).strip()
                print(f"    left strip psm={p} -> '{free_raw}'")
                free_d = re.findall(r'\d+', free_raw)
                if free_d and total_val:
                    free, total = int(free_d[-1]), total_val
                    print(f"[BUILDER] Parsed (left-strip): {free}/{total}  ({free} free)")
                    return free, total

        # No slash found — try full string sweep
        tokens = sorted(
            [(data["left"][i], data["text"][i].strip())
             for i in range(len(data["text"])) if data["text"][i].strip()],
            key=lambda t: t[0]
        )
        combined = "".join(t[1] for t in tokens)
        print(f"  [BUILDER] combined tokens: '{combined}'")
        m = re.search(r'(\d+)\s*/\s*(\d+)', combined)
        if m:
            free, total = int(m.group(1)), int(m.group(2))
            print(f"[BUILDER] Parsed (tokens): {free}/{total}  ({free} free)")
            return free, total

    # ── Approach 2: contour-based — count character blobs left-to-right,
    #    OCR the leftmost blob (the free-builder digit) individually.
    binary = cv2.bitwise_not(img_padded)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    blobs = sorted(
        [cv2.boundingRect(c) for c in contours if cv2.contourArea(c) > 80],
        key=lambda b: b[0]
    )
    print(f"  [BUILDER] contour blobs (x,y,w,h): {blobs}")
    if len(blobs) >= 2:
        # Rightmost blob = total digit
        rx, ry, rw, rh = blobs[-1]
        total_strip = img_padded[ry:ry+rh, rx:rx+rw]
        total_raw = pytesseract.image_to_string(
            total_strip, config="--oem 1 --psm 10 -c tessedit_char_whitelist=0123456789").strip()
        total_d = re.findall(r'\d+', total_raw)
        total_val = int(total_d[-1]) if total_d else 5  # default 5 if OCR fails

        # Leftmost blob = free digit
        lx, ly, lw, lh = blobs[0]
        free_strip = img_padded[ly:ly+lh, lx:lx+lw]
        cv2.imwrite("debug_builder_free_blob.png", free_strip)
        free_raw = pytesseract.image_to_string(
            free_strip, config="--oem 1 --psm 10 -c tessedit_char_whitelist=0123456789").strip()
        print(f"  [BUILDER] blob OCR: free='{free_raw}'  total='{total_raw}'  blob_w={lw}")

        free_d = re.findall(r'\d+', free_raw)
        if free_d:
            free = int(free_d[-1])
            print(f"[BUILDER] Parsed (contour): {free}/{total_val}  ({free} free)")
            return free, total_val

        # If the free-blob is very narrow it's a "1" — OCR can't read it but shape tells us
        if lw <= 35:
            print(f"[BUILDER] Narrow blob (w={lw}) assumed to be '1'")
            print(f"[BUILDER] Parsed (contour narrow): 1/{total_val}  (1 free)")
            return 1, total_val
            return free, total

    print("[BUILDER] All approaches failed — check debug_builder_count_pre.png")
    return 0, 0


# ---------------------------------------------------------
#  WALL FINDER  (open builder list, scan + scroll for Wall)
# ---------------------------------------------------------
def _preprocess_list_image(img_arr):
    """Preprocess the list screenshot for Tesseract word detection."""
    gray = cv2.cvtColor(img_arr, cv2.COLOR_RGB2GRAY)
    up   = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    # OTSU works well for list text (mixed dark/light rows)
    _, thresh = cv2.threshold(up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return thresh, 3   # return image + scale used


def scan_list_for_wall(list_abs, attempt):
    """
    Screenshot the list area, OCR with bounding boxes, look for 'wall'.
    Returns (found, abs_click_x, abs_click_y).
    Saves debug_list_attempt_N.png and debug_list_attempt_N_pre.png.
    """
    img_pil = pyautogui.screenshot(region=list_abs)
    raw_path = f"debug_list_attempt_{attempt}.png"
    img_pil.save(raw_path)

    img_arr = np.array(img_pil)
    processed, scale = _preprocess_list_image(img_arr)
    pre_path = f"debug_list_attempt_{attempt}_pre.png"
    cv2.imwrite(pre_path, processed)

    print(f"  [SCAN] attempt {attempt} — saved {raw_path} + {pre_path}")

    data = pytesseract.image_to_data(
        processed,
        config="--oem 3 --psm 6",
        output_type=pytesseract.Output.DICT
    )

    n = len(data["text"])
    print(f"  [SCAN] Tesseract returned {n} word boxes")

    # Print every non-empty word detected
    words_found = []
    for i in range(n):
        word = data["text"][i].strip()
        conf = int(data["conf"][i])
        if word and conf > 0:
            words_found.append(f"'{word}'(conf={conf})")

    if words_found:
        print(f"  [SCAN] Words: {', '.join(words_found)}")
    else:
        print(f"  [SCAN] No words detected")

    # Search for "wall"
    for i in range(n):
        word = data["text"][i].strip()
        conf = int(data["conf"][i])
        if "wall" in word.lower() and conf > 20:
            x = data["left"][i]
            y = data["top"][i]
            w = data["width"][i]
            h = data["height"][i]

            # OCR coords are in scaled space → convert back to screen coords
            cx = list_abs[0] + (x + w // 2) // scale
            cy = list_abs[1] + (y + h // 2) // scale

            print(f"  [SCAN] >>> WALL FOUND: text='{word}' conf={conf}")
            print(f"           OCR box (scaled): x={x},y={y},w={w},h={h}")
            print(f"           Click target (screen): ({cx}, {cy})")
            return True, cx, cy

    return False, 0, 0


def find_and_click_wall():
    """
    Full flow:
      1. Read builder count — need at least 1 free.
      2. Click builder button to open upgrade list.
      3. Scan list for 'Wall x'; scroll down and retry if not found.
      4. Click on Wall entry.
    Returns a status string.
    """
    print("\n" + "=" * 55)
    print("  WALL FINDER — STARTING")
    print("=" * 55)

    # ── Step 1: builder count ──────────────────────────────────
    print("\n[STEP 1] Checking builder availability...")
    free, total = read_builder_count()

    if free == 0 and total == 0:
        print("[STEP 1] FAIL: Could not read builder count.")
        return "BUILDER_OCR_FAILED"

    if free == 0:
        print(f"[STEP 1] FAIL: No free builders ({free}/{total}).")
        return "NO_FREE_BUILDER"

    print(f"[STEP 1] OK — {free}/{total} builder(s) free.")

    # ── Step 2: open builder list ──────────────────────────────
    print("\n[STEP 2] Clicking builder button to open upgrade list...")
    btn_cx = WINDOW_X + BUILDER_AREA[0] + BUILDER_AREA[2] // 2
    btn_cy = WINDOW_Y + BUILDER_AREA[1] + BUILDER_AREA[3] // 2
    print(f"  Click at screen ({btn_cx}, {btn_cy})")
    pyautogui.click(btn_cx, btn_cy)
    time.sleep(1.5)

    # ── Step 3: scan + scroll ──────────────────────────────────
    list_abs = (
        WINDOW_X + LIST_AREA[0],
        WINDOW_Y + LIST_AREA[1],
        LIST_AREA[2],
        LIST_AREA[3],
    )
    scroll_x = list_abs[0] + LIST_AREA[2] // 2
    scroll_y = list_abs[1] + LIST_AREA[3] // 2
    print(f"\n[STEP 3] List region (abs): {list_abs}")
    print(f"         Scroll point  (abs): ({scroll_x}, {scroll_y})")

    pyautogui.moveTo(scroll_x, scroll_y)

    for attempt in range(1, MAX_SCROLL_TRIES + 1):
        print(f"\n[STEP 3] Scanning list (attempt {attempt}/{MAX_SCROLL_TRIES})...")
        found, cx, cy = scan_list_for_wall(list_abs, attempt)

        if found:
            print(f"\n[STEP 3] SUCCESS — clicking Wall at ({cx}, {cy})")
            pyautogui.click(cx, cy)
            time.sleep(1)
            return "WALL_CLICKED"

        if attempt < MAX_SCROLL_TRIES:
            print(f"  Scrolling down ({LIST_SCROLL_AMOUNT})...")
            pyautogui.moveTo(scroll_x, scroll_y)
            pyautogui.scroll(LIST_SCROLL_AMOUNT)
            time.sleep(0.8)

    print("\n[STEP 3] FAIL — Wall not found after all scroll attempts.")
    return "WALL_NOT_FOUND"


# ---------------------------------------------------------
#  UPGRADE BUTTON CLICK  (gold priority → elixir fallback)
# ---------------------------------------------------------
def click_upgrade_button(gold, elixir):
    """
    After the wall entry has been selected and the upgrade dialog is open,
    click the correct button.  Gold is tried first; elixir is the fallback.
    Verifies the spend actually happened by re-reading the resource afterward —
    a click that misses the button (stale coords, dialog didn't open, etc.)
    must NOT be reported as a successful upgrade.
    """
    print(f"\n[UPGRADE] Gold={gold:,}  Elixir={elixir:,}  WallPrice={WALL_PRICE:,}")

    confirm_cx = WINDOW_X + CONFIRM_BTN[0] + CONFIRM_BTN[2] // 2
    confirm_cy = WINDOW_Y + CONFIRM_BTN[1] + CONFIRM_BTN[3] // 2

    if gold >= WALL_PRICE:
        cx = WINDOW_X + GOLD_BTN[0] + GOLD_BTN[2] // 2
        cy = WINDOW_Y + GOLD_BTN[1] + GOLD_BTN[3] // 2
        print(f"[UPGRADE] Sufficient GOLD — clicking Gold button at ({cx}, {cy})")
        pyautogui.click(cx, cy)
        time.sleep(0.8)
        print(f"[UPGRADE] Clicking Confirm at ({confirm_cx}, {confirm_cy})")
        pyautogui.click(confirm_cx, confirm_cy)
        time.sleep(1.0)

        gold_after = read_storage(GOLD_OFFSET, label="GOLD_AFTER", debug_name="debug_gold_after.png")
        print(f"[UPGRADE] Gold before={gold:,}  after={gold_after:,}")
        if gold_after <= gold - (WALL_PRICE // 2):
            return "UPGRADED_WITH_GOLD"
        print("[UPGRADE] Gold did not drop — click missed the button, upgrade NOT applied.")
        return "UPGRADE_CLICK_FAILED"

    if elixir >= WALL_PRICE:
        cx = WINDOW_X + ELIXIR_BTN[0] + ELIXIR_BTN[2] // 2
        cy = WINDOW_Y + ELIXIR_BTN[1] + ELIXIR_BTN[3] // 2
        print(f"[UPGRADE] Gold insufficient — using ELIXIR, clicking at ({cx}, {cy})")
        pyautogui.click(cx, cy)
        time.sleep(0.8)
        print(f"[UPGRADE] Clicking Confirm at ({confirm_cx}, {confirm_cy})")
        pyautogui.click(confirm_cx, confirm_cy)
        time.sleep(1.0)

        elixir_after = read_storage(ELIXIR_OFFSET, label="ELIXIR_AFTER", debug_name="debug_elixir_after.png")
        print(f"[UPGRADE] Elixir before={elixir:,}  after={elixir_after:,}")
        if elixir_after <= elixir - (WALL_PRICE // 2):
            return "UPGRADED_WITH_ELIXIR"
        print("[UPGRADE] Elixir did not drop — click missed the button, upgrade NOT applied.")
        return "UPGRADE_CLICK_FAILED"

    print("[UPGRADE] Neither gold nor elixir is sufficient.")
    return "NOT_ENOUGH_RESOURCES"


# ---------------------------------------------------------
#  ESCAPE HELPER — force return to home screen
# ---------------------------------------------------------
def _escape_stuck_screen():
    """Find Close_Btn.png on screen and click it to dismiss any stuck overlay."""
    print("[WATCHDOG] 2-min timeout — searching for Close_Btn.png on screen...")
    try:
        pos = pyautogui.locateCenterOnScreen("Close_Btn.png", confidence=0.8)
        if pos:
            print(f"[WATCHDOG] Close button found at {pos} — clicking")
            pyautogui.click(pos)
            time.sleep(0.5)
        else:
            print("[WATCHDOG] Close_Btn.png not found on screen — trying fixed coords")
            cx = WINDOW_X + CLOSE_BTN[0] + CLOSE_BTN[2] // 2
            cy = WINDOW_Y + CLOSE_BTN[1] + CLOSE_BTN[3] // 2
            for _ in range(4):
                pyautogui.click(cx, cy)
                time.sleep(0.5)
    except Exception as e:
        print(f"[WATCHDOG] Error finding close button: {e}")


# ---------------------------------------------------------
#  FULL WALL UPGRADE FLOW
# ---------------------------------------------------------
def upgrade_wall():
    """
    Complete flow:
      1. Read gold & elixir — bail early if both < WALL_PRICE.
      2. Check free builders — need at least 1.
      3. Open builder list, find Wall, click it.
      4. Wait for upgrade dialog, then click Gold or Elixir button.
    """
    print("\n" + "=" * 55)
    print("  WALL UPGRADE — FULL FLOW")
    print("=" * 55)

    # ── Step 1: read resources ─────────────────────────────
    print("\n[STEP 1] Reading resources...")
    gold   = read_storage(GOLD_OFFSET,   label="GOLD",   debug_name="debug_gold.png")
    elixir = read_storage(ELIXIR_OFFSET, label="ELIXIR", debug_name="debug_elixir.png")

    print(f"[STEP 1] Gold={gold:,}  Elixir={elixir:,}  WallPrice={WALL_PRICE:,}")

    if gold < WALL_PRICE and elixir < WALL_PRICE:
        print("[STEP 1] SKIP — not enough resources for a wall upgrade.")
        return "NOT_ENOUGH_RESOURCES"

    # ── Step 2+3+4: builder check, find wall, confirm upgrade ─
    # Watchdog: if stuck on confirmation screen > 2 min, force-escape
    _escaped = threading.Event()

    def _watchdog():
        if not _escaped.is_set():
            _escape_stuck_screen()

    watchdog = threading.Timer(120.0, _watchdog)
    watchdog.start()

    try:
        result = find_and_click_wall()

        if result != "WALL_CLICKED":
            return result

        # ── Step 4: upgrade dialog should now be open ──────────
        print("\n[STEP 4] Waiting for upgrade dialog...")
        time.sleep(1.5)

        return click_upgrade_button(gold, elixir)
    finally:
        _escaped.set()
        watchdog.cancel()


# ---------------------------------------------------------
#  MAIN SIMULATION
# ---------------------------------------------------------
def simulate_wall_upgrade():
    if not activate_game_window():
        print("❌ Cannot proceed — LDPlayer not found")
        return

    if DEBUG:
        print("=" * 50)
        print("[DEBUG] Debug mode ON")
        print("=" * 50)
        # show_debug_overlay()   # offset verified — overlay disabled

    time.sleep(0.3)

    # --- Resource full check ---
    print("\n--- Checking GOLD full ---")
    gold_full = is_resource_full(GOLD_OFFSET, GOLD_FULL_REF, label="GOLD_FULL")

    print("\n--- Checking ELIXIR full ---")
    elixir_full = is_resource_full(ELIXIR_OFFSET, ELIXIR_FULL_REF, label="ELIXIR_FULL")

    print("\n===== RESOURCE FULL STATUS =====")
    print(f"Gold   full: {gold_full}")
    print(f"Elixir full: {elixir_full}")
    print("================================\n")

    # --- OCR read (kept for reference) ---
    print("\n--- Reading GOLD ---")
    gold = read_storage(GOLD_OFFSET, label="GOLD", debug_name="debug_gold.png")

    print("\n--- Reading ELIXIR ---")
    elixir = read_storage(ELIXIR_OFFSET, label="ELIXIR", debug_name="debug_elixir.png")

    walls_gold   = gold   // WALL_PRICE
    walls_elixir = elixir // WALL_PRICE
    total        = walls_gold + walls_elixir

    print("\n===== WALL UPGRADE SIMULATION =====")
    print(f"Gold:              {gold:,}")
    print(f"Elixir:            {elixir:,}")
    print(f"Walls with GOLD:   {walls_gold}")
    print(f"Walls with ELIXIR: {walls_elixir}")
    print(f"Total possible:    {total}")

    if gold >= WALL_PRICE or elixir >= WALL_PRICE:
        print("\nTRIGGER: Storage >= 8M → Upgrade Allowed\n")
    else:
        print("\nStorage < 8M → No Upgrade Needed\n")

    print("Simulation complete (no clicks performed).")
    print("=========================================\n")


# ---------------------------------------------------------
if __name__ == "__main__":
    if not activate_game_window():
        raise SystemExit("LDPlayer not found")

    CONTINUE_ON  = {"UPGRADED_WITH_GOLD", "UPGRADED_WITH_ELIXIR"}
    MAX_OCR_FAIL = 3   # retries before giving up on builder OCR

    walls_upgraded = 0
    ocr_fail_streak = 0
    run = 1

    while True:
        print(f"\n{'#'*55}")
        print(f"  RUN #{run}  (walls upgraded: {walls_upgraded})")
        print(f"{'#'*55}")

        result = upgrade_wall()
        print(f"\n  --> Run #{run} result: {result}")

        if result in CONTINUE_ON:
            walls_upgraded += 1
            ocr_fail_streak = 0
            print(f"  Wall upgraded! Total this session: {walls_upgraded}")
            print(f"  Waiting 3s before next attempt...")
            time.sleep(3)
            run += 1

        elif result == "BUILDER_OCR_FAILED":
            ocr_fail_streak += 1
            print(f"  Builder OCR failed ({ocr_fail_streak}/{MAX_OCR_FAIL}) — retrying in 2s...")
            if ocr_fail_streak >= MAX_OCR_FAIL:
                print(f"  Too many OCR failures — stopping.")
                break
            time.sleep(2)
            run += 1

        else:
            print(f"\n{'='*55}")
            print(f"  LOOP ENDED: {result}")
            print(f"  Total walls upgraded this session: {walls_upgraded}")
            print(f"{'='*55}")
            break
