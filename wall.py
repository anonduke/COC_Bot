# wall_upgrade_simulation_test.py
import os
import sys
import socket
import time
import threading
import traceback
import pyautogui
import pytesseract
import cv2
import numpy as np
import pygetwindow as gw

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Debug prints use "→", "✅" etc. — on a cp1252 console/pipe those raise
# UnicodeEncodeError and kill the whole flow, so degrade to "?" instead.
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

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
LIST_AREA      = (412, 130, 227, 422)  # upgrade list popup region
# NOTE: left edge shifted 25px further left (and widened by the same amount)
# vs. the original (437, 130, 202, 422) — that crop sliced through the first
# glyph of every unindented row ("Wall" -> "Vall", "Dark" -> "ark", "Gold" ->
# "old"), which is why Tesseract never matched "wall" in the upgrade list.
CONFIRM_BTN    = (755, 590, 171, 59)  # "Confirm" button on the single-button
# upgrade dialog ("Upgrade Wall to Level N?") that opens after tapping a row
# in the upgrades list — there's no separate Gold/Elixir choice step, so no
# GOLD_BTN/ELIXIR_BTN constants are needed here.
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

# "otsu" = dark/light list rows (centered popup list);
# "white" = white text on brown rows (right-side "Buildings & Heroes" panel)
LIST_TEXT_MODE = "otsu"

# Walls upgrade instantly but Confirm still needs a free builder (otherwise
# the gem-priced Goblin Builder offer pops up), so check the counter first.
REQUIRE_FREE_BUILDER = True

# Right-side builder panel (newer UI only). None = not used on this machine.
BUILDER_PANEL_TITLE_AREA = None   # "Buildings & Heroes" title text
BUILDER_PANEL_CLOSE      = None   # red X button (x, y)

# On the newer UI, tapping the Wall row selects a wall and shows Upgrade
# buttons (gold / elixir / wall rings) instead of opening the dialog
# directly. This hammer icon finds the gold/elixir ones wherever they are.
UPGRADE_HAMMER_REF = "misc/wall_upgrade_hammer.png"

# ---------------------------------------------------------
#  PER-MACHINE OVERRIDES (keyed by hostname)
# ---------------------------------------------------------
# Each PC's LDPlayer renders the HUD differently (different resource-bar
# spacing, builder counter position, builder list layout), so the offsets
# above only fit the machine they were measured on. Machines not listed
# here use the defaults above unchanged.
MACHINE_PROFILES = {
    # 1366x768 display, LDPlayer window 1236x708 at (84, 10), newer CoC UI.
    # All values measured live on 2026-11-12.
    "DESKTOP-GSD3MHF": {
        "GOLD_OFFSET":   (1000,  58, 142, 26),
        "ELIXIR_OFFSET": (1000, 103, 142, 26),
        "BUILDER_AREA":  (1060, 538,  56, 26),   # "0/7" next to the Build button
        "LIST_AREA":     ( 880,  85, 315, 545),  # right-side builder panel
        "LIST_TEXT_MODE": "white",
        "LIST_SCROLL_AMOUNT": -300,  # -600 skipped past rows
        "MAX_SCROLL_TRIES":   25,
        "REQUIRE_FREE_BUILDER": True,   # Confirm needs a free builder (live: "all your Builders are busy")
        "BUILDER_PANEL_TITLE_AREA": (950, 40, 180, 30),
        "BUILDER_PANEL_CLOSE":      (1176, 55),
    },
}
MACHINE_NAME = socket.gethostname().upper()

# Global window position (set by activate_game_window)
WINDOW_X, WINDOW_Y = 0, 0


# ---------------------------------------------------------
#  ACTIVATE LDPLAYER  (mirrors Ranked_Copy.py pattern)
# ---------------------------------------------------------
def force_foreground(win, tries=3):
    """Bring `win` to the front and confirm it is really the foreground
    window. Windows refuses SetForegroundWindow while another app holds focus
    (pygetwindow then raises 'Error code 0 - The operation completed
    successfully'); live, the bot then searched/clicked over VS Code for
    minutes. Minimize + restore gets past that lock."""
    for _ in range(tries):
        try:
            if win.isMinimized:
                win.restore()
            win.activate()
        except Exception:
            pass
        time.sleep(0.15)
        active = gw.getActiveWindow()
        if active is not None and active._hWnd == win._hWnd:
            return True
        try:
            win.minimize()
            time.sleep(0.3)
            win.restore()
            time.sleep(0.5)
        except Exception:
            pass
    active = gw.getActiveWindow()
    return active is not None and active._hWnd == win._hWnd


def activate_game_window():
    global WINDOW_X, WINDOW_Y
    try:
        windows = gw.getWindowsWithTitle("LDPlayer")
        if not windows:
            print("❌ No LDPlayer window found")
            return False

        game_window = windows[0]
        if not force_foreground(game_window):
            print("❌ LDPlayer could not be brought to the front")
            return False
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
# Sample digits of the game font (same file Ranked_Copy uses for loot).
# Tesseract occasionally doubles or drops a digit on clean storage masks
# ('649 545' -> 6495545 live), so digits are matched one by one instead.
try:
    _DIGITS = np.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "misc", "loot_digits.npz"))
    _DIGIT_SAMPLES = [(d, img) for d in _DIGITS.files for img in _DIGITS[d].astype(np.float32) / 255]
except Exception:
    _DIGIT_SAMPLES = []


def _classify_digit_mask(mask):
    """Read a white-on-black digit mask by matching each digit blob against
    the samples. Two touching digits form one wide blob and are split.
    Returns '' if there are no samples or no blobs."""
    if not _DIGIT_SAMPLES:
        return ""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = ""
    for i in sorted(range(1, n), key=lambda i: stats[i, cv2.CC_STAT_LEFT]):
        x, y, w, h = stats[i, :4]
        blob = (labels[y:y + h, x:x + w] == i).astype(np.float32)
        parts = max(1, round(w / (0.82 * h))) if w > h * 1.3 else 1
        for p in range(parts):
            piece = cv2.resize(blob[:, p * w // parts:(p + 1) * w // parts], (20, 30),
                               interpolation=cv2.INTER_AREA)
            dist, digit = min((np.abs(s - piece).sum(), d) for d, s in _DIGIT_SAMPLES)
            # Real digits match within ~30; bright scenery behind the bar
            # leaves blobs at ~170 that would otherwise add a leading digit.
            if dist <= 80:
                out += digit
    return out


def _storage_mask(img_rgb):
    """White digit mask of a gold/elixir storage crop (4x upscaled)."""
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
    # Drop blobs much shorter than a digit — the bar's animated sparkle leaves
    # specks that intermittently OCR as a leading extra digit (7518938 → 77518938).
    # The number is right-aligned, so also keep only the rightmost cluster of
    # blobs (digit-group spaces are far narrower than a digit's height).
    n, labels, stats, _ = cv2.connectedComponentsWithStats(white_mask, 8)
    if n > 1:
        max_h = stats[1:, cv2.CC_STAT_HEIGHT].max()
        keep = [i for i in range(1, n) if stats[i, cv2.CC_STAT_HEIGHT] >= max_h * 0.6]
        keep.sort(key=lambda i: stats[i, cv2.CC_STAT_LEFT], reverse=True)
        cluster = keep[:1]
        for i in keep[1:]:
            prev = cluster[-1]
            gap = stats[prev, cv2.CC_STAT_LEFT] - (stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH])
            if gap > max_h:
                break
            cluster.append(i)
        white_mask = np.where(np.isin(labels, cluster), 255, 0).astype(np.uint8)
    return white_mask


def read_storage(offset, label="resource", debug_name="debug.png"):
    region = get_absolute_region(offset)
    if not region:
        return 0

    # screenshot
    img_pil = pyautogui.screenshot(region=region)
    img_pil.save(debug_name)

    white_mask = _storage_mask(np.array(img_pil))
    img_inv = cv2.bitwise_not(white_mask)   # Tesseract needs black-on-white

    debug_pre = debug_name.replace(".png", "_pre.png")
    cv2.imwrite(debug_pre, img_inv)

    raw = _classify_digit_mask(white_mask)
    if raw:
        result = int(raw[-8:])   # max storage is 8 digits
    else:
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
    if LIST_TEXT_MODE == "white":
        # White row titles on brown rows — OTSU can't separate those, so keep
        # only bright, unsaturated pixels (same idea as read_storage).
        up = cv2.resize(img_arr, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        _, s, v = cv2.split(cv2.cvtColor(up, cv2.COLOR_RGB2HSV))
        white_mask = cv2.bitwise_and(
            cv2.threshold(v, 190, 255, cv2.THRESH_BINARY)[1],
            cv2.threshold(s,  60, 255, cv2.THRESH_BINARY_INV)[1],
        )
        return cv2.bitwise_not(white_mask), 3

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

    # Search for "wall" — also accept "vall", the misread you get if the crop
    # ever clips the leading "W" stroke again (see LIST_AREA note above).
    for i in range(n):
        word = data["text"][i].strip()
        conf = int(data["conf"][i])
        word_lower = word.lower()
        if ("wall" in word_lower or "vall" in word_lower) and conf > 20:
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
    if REQUIRE_FREE_BUILDER:
        print("\n[STEP 1] Checking builder availability...")
        free, total = read_builder_count()

        if free == 0 and total == 0:
            print("[STEP 1] FAIL: Could not read builder count.")
            return "BUILDER_OCR_FAILED"

        if free == 0:
            print(f"[STEP 1] FAIL: No free builders ({free}/{total}).")
            return "NO_FREE_BUILDER"

        print(f"[STEP 1] OK — {free}/{total} builder(s) free.")
    else:
        print("\n[STEP 1] Skipped — walls don't need a free builder on this machine's UI.")

    # ── Step 2: open builder list ──────────────────────────────
    # The side panel stays open after an upgrade; tapping the builder
    # counter again would land on a random list row instead.
    panel_was_open = builder_panel_open()
    if panel_was_open:
        print("\n[STEP 2] Builder list already open.")
    else:
        print("\n[STEP 2] Clicking builder button to open upgrade list...")
        btn_cx = WINDOW_X + BUILDER_AREA[0] + BUILDER_AREA[2] // 2
        btn_cy = WINDOW_Y + BUILDER_AREA[1] + BUILDER_AREA[3] // 2
        print(f"  Click at screen ({btn_cx}, {btn_cy})")
        pyautogui.click(btn_cx, btn_cy)
        time.sleep(1.5)
        # Live: one tap right after returning from a battle didn't open the
        # panel, and the 25 scans of the home screen then reported
        # WALL_NOT_FOUND (which reads as "all walls maxed"). Verify and retry.
        if BUILDER_PANEL_TITLE_AREA:
            for _ in range(2):
                if builder_panel_open():
                    break
                print("  Builder panel didn't open — tapping again")
                pyautogui.click(btn_cx, btn_cy)
                time.sleep(2)
            else:
                if not builder_panel_open():
                    print("[STEP 2] FAIL — builder panel would not open.")
                    return "PANEL_NOT_OPEN"

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

    if panel_was_open:
        # Left scrolled from a previous run — start again from the top so the
        # downward scan can't begin below the Wall row.
        for _ in range(15):
            pyautogui.scroll(1000)
            time.sleep(0.1)
        time.sleep(0.8)

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
#  UPGRADE BUTTON CLICK
# ---------------------------------------------------------
def read_storage_retry(offset, label="resource", debug_name="debug.png", max_tries=4, delay=0.7):
    """read_storage() wrapped with retries — the elixir bar in particular has
    turned out to be OCR-flaky live (truncated reads like '2893' or '210'
    from a real, correctly-changed number), and any single unretried read
    that comes back 0 can poison success-detection downstream (0 always
    looks like 'dropped to nothing'). Keeps trying until a plausible non-zero
    read comes back, since real balances here are never actually 0."""
    value = 0
    for attempt in range(max_tries):
        value = read_storage(offset, label=label, debug_name=debug_name)
        if value > 0:
            return value
        print(f"[UPGRADE] {label} read as 0 (OCR miss?) — retry {attempt + 1}/{max_tries}")
        time.sleep(delay)
    return value

def _read_resources_after_upgrade(max_tries=4, delay=0.9):
    """Re-read gold/elixir after a Confirm click. Right after tapping Confirm
    the resource bar can be briefly obscured by dialog-close animation, or
    just OCR-flaky (elixir especially), which makes read_storage() come back
    0 even though the real number is fine — that must never be treated as
    'balance dropped to 0', so retry each currency independently until a
    non-zero read comes back (real balances here are always well above 0)."""
    gold_after = elixir_after = 0
    for attempt in range(max_tries):
        if gold_after == 0:
            gold_after = read_storage(GOLD_OFFSET, label="GOLD_AFTER", debug_name="debug_gold_after.png")
        if elixir_after == 0:
            elixir_after = read_storage(ELIXIR_OFFSET, label="ELIXIR_AFTER", debug_name="debug_elixir_after.png")
        if gold_after > 0 and elixir_after > 0:
            return gold_after, elixir_after
        print(f"[UPGRADE] Resource bar partially unreadable — retry {attempt + 1}/{max_tries}")
        time.sleep(delay)
    return gold_after, elixir_after

# Fixed screen regions (relative to window) for the two dialog states we
# need to tell apart. Both live-verified: title text is large/clear and OCRs
# reliably, unlike the tiny "CONFIRM" button text or resource digit strings.
MAIN_DIALOG_TITLE_AREA = (150, 55, 800, 55)   # "Upgrade Wall to Level N?"
NEED_MORE_POPUP_AREA   = (280, 185, 650, 95)  # "You Need More Gold/Elixir"

def _ocr_region_text(region_offset):
    """OCR a fixed window-relative region, return lowercased text (or '' on
    any failure — treated as 'not found' by callers, which is the safe
    direction: if we can't confirm a popup is gone, we keep retrying rather
    than assuming success)."""
    try:
        region = (WINDOW_X + region_offset[0], WINDOW_Y + region_offset[1],
                   region_offset[2], region_offset[3])
        img = pyautogui.screenshot(region=region)
        return pytesseract.image_to_string(img, config="--oem 3 --psm 6").strip().lower()
    except Exception:
        return ""

def _need_more_currency_popup_open():
    """True if the 'You need more Gold/Elixir — buy with gems?' upsell is on
    screen. Live-verified to appear when the specific wall being confirmed
    needs more of a currency than is on hand, even though some OTHER
    currency was sufficient overall — must be detected and backed out of
    without ever tapping the gem-purchase button."""
    text = _ocr_region_text(NEED_MORE_POPUP_AREA)
    return "need" in text and ("more" in text or "elixir" in text or "gold" in text)

def _click_close_btn():
    """Click the topmost red X (Close_Btn.PNG). Returns False if none is on
    screen — pyscreeze raises rather than returning None when it's missing."""
    try:
        pos = pyautogui.locateCenterOnScreen("Close_Btn.PNG", confidence=0.7)
    except Exception:
        pos = None
    if not pos:
        return False
    pyautogui.click(pos)
    time.sleep(0.8)
    return True

BUILDERS_BUSY_POPUP_AREA  = (560, 160, 560, 180)  # "Hey Chief! ... all your Builders are busy"
BUILDERS_BUSY_POPUP_CLOSE = (1069, 178)           # that popup's red X (live-verified)

def _builders_busy_popup_open():
    """True if Confirm brought up 'Hey Chief! Looks like all your Builders are
    busy' — the offer to finish with the gem-paid Goblin Builder. Walls DO
    need a free builder at Confirm, and the builder counter can count the
    Goblin Builder as free ('1/7'). Live: this popup hid the dialog title, so
    it was read as a successful upgrade and the loop repeated. Back out with
    the red X only — never the gem buttons."""
    text = _ocr_region_text(BUILDERS_BUSY_POPUP_AREA)
    return "busy" in text or "chief" in text or "goblin" in text

def _main_upgrade_dialog_open():
    """True if the 'Upgrade Wall to Level N?' confirmation dialog is still
    showing (title bar still visible)."""
    text = _ocr_region_text(MAIN_DIALOG_TITLE_AREA)
    return "upgrade" in text or "level" in text

def builder_panel_open():
    """True if the right-side 'Buildings & Heroes' panel is showing. Always
    False on machines whose profile doesn't define the panel."""
    if not BUILDER_PANEL_TITLE_AREA:
        return False
    text = _ocr_region_text(BUILDER_PANEL_TITLE_AREA)
    return "building" in text or "heroes" in text

def close_builder_panel():
    """Close the right-side builder panel if it's open (no-op otherwise)."""
    if not BUILDER_PANEL_CLOSE or not activate_game_window():
        return
    if builder_panel_open():
        pyautogui.click(WINDOW_X + BUILDER_PANEL_CLOSE[0], WINDOW_Y + BUILDER_PANEL_CLOSE[1])
        time.sleep(1)
        print("[PANEL] Builder panel closed")

def _find_upgrade_buttons():
    """Locate the hammer Upgrade buttons shown under a selected wall and tell
    gold from elixir by the colour of the price icon at each button's top
    right (yellow coin vs pink drop). Their position shifts depending on
    whether the side panel is open, so fixed coordinates don't work.
    Returns {"gold": (x, y), "elixir": (x, y)} in screen coords (either key
    may be missing)."""
    tpl = cv2.imread(UPGRADE_HAMMER_REF)
    if tpl is None:
        print(f"[UPGRADE] Hammer reference '{UPGRADE_HAMMER_REF}' missing")
        return {}
    shot = cv2.cvtColor(np.array(pyautogui.screenshot(
        region=(WINDOW_X, WINDOW_Y, 1236, 708))), cv2.COLOR_RGB2BGR)
    res = cv2.matchTemplate(shot, tpl, cv2.TM_CCOEFF_NORMED)
    th, tw = tpl.shape[:2]

    centers = []
    ys, xs = np.where(res >= 0.7)
    for x, y in sorted(zip(xs, ys), key=lambda p: -res[p[1], p[0]]):
        cx, cy = int(x) + tw // 2, int(y) + th // 2
        if all(abs(cx - ex) > 20 or abs(cy - ey) > 20 for ex, ey in centers):
            centers.append((cx, cy))

    buttons = {}
    for cx, cy in centers[:3]:
        icon = shot[max(0, cy - 46):cy - 28, cx + 36:cx + 58]
        if icon.size == 0:
            continue
        h, s, v = cv2.split(cv2.cvtColor(icon, cv2.COLOR_BGR2HSV))
        gold_px   = int(((h >= 15) & (h <= 35) & (s > 120) & (v > 150)).sum())
        elixir_px = int(((h >= 135) & (h <= 170) & (s > 80) & (v > 100)).sum())
        kind = "gold" if gold_px > elixir_px else "elixir"
        if max(gold_px, elixir_px) >= 30 and kind not in buttons:
            buttons[kind] = (WINDOW_X + cx, WINDOW_Y + cy)
        if DEBUG:
            print(f"[UPGRADE] hammer at ({cx},{cy}) gold_px={gold_px} elixir_px={elixir_px} -> {kind}")
    return buttons

def _open_upgrade_dialog(gold):
    """Newer UI: the Wall row only selects a wall, so tap its gold (or
    elixir) Upgrade button to bring up the 'Upgrade Wall to Level N?' dialog.
    Returns True if a button was tapped."""
    buttons = _find_upgrade_buttons()
    currency = "gold" if gold >= WALL_PRICE else "elixir"
    pos = buttons.get(currency)
    if not pos:
        print(f"[UPGRADE] {currency} Upgrade button not found (found: {list(buttons)})")
        return False
    print(f"[UPGRADE] Tapping {currency} Upgrade button at {pos}")
    pyautogui.click(pos)
    time.sleep(2.0)
    return True

def click_upgrade_button(gold, elixir, max_attempts=4):
    """
    Clicking the Wall row in the upgrades list opens a single "Upgrade Wall
    to Level N?" dialog with ONE Confirm button — there is no separate
    Gold/Elixir choice screen (the currency is fixed by building type and
    already shown on the Confirm button itself). Live-verified against the
    actual game: the old code clicked GOLD_BTN before CONFIRM_BTN, but that
    first click landed in the dead space below the dialog's info panel and
    dismissed it before Confirm ever registered — which is exactly why walls
    were detected but never actually upgraded.

    Also live-verified, two more failure modes this now guards against:
      1. A single Confirm click is not 100% reliable — the dialog has an
         open animation, and clicking while it's still settling can miss the
         button entirely with no visible error. So this retries the click
         (idempotent: if a prior click already succeeded and closed the
         dialog, a stray extra click just taps empty village background).
      2. gold/elixir being individually sufficient overall does NOT mean
         THIS specific wall's required currency is sufficient — if it isn't,
         confirming opens a "buy the missing amount with gems?" upsell
         instead of upgrading. That must be detected and backed out of
         safely (never tapping the gem-purchase button), not mistaken for
         success.
    Verifies state via two targeted OCR checks (title bar text) rather than
    reading gold/elixir — the dialog's own panel covers the leading digits of
    BOTH resource numbers while open, which is exactly why post-click balance
    OCR kept coming back garbled ('2893', '210', empty) on genuinely
    successful upgrades.
    """
    print(f"\n[UPGRADE] Gold={gold:,}  Elixir={elixir:,}  WallPrice={WALL_PRICE:,}")

    if gold < WALL_PRICE and elixir < WALL_PRICE:
        print("[UPGRADE] Neither gold nor elixir is sufficient.")
        return "NOT_ENOUGH_RESOURCES"

    confirm_cx = WINDOW_X + CONFIRM_BTN[0] + CONFIRM_BTN[2] // 2
    confirm_cy = WINDOW_Y + CONFIRM_BTN[1] + CONFIRM_BTN[3] // 2

    # Newer UI: the dialog isn't open yet, only the wall's Upgrade buttons.
    # Confirm must never be clicked blind there — that spot is the magic
    # items button when no dialog covers it.
    if not _main_upgrade_dialog_open():
        if _open_upgrade_dialog(gold):
            if not _main_upgrade_dialog_open():
                print("[UPGRADE] Upgrade dialog did not open — aborting.")
                return "UPGRADE_CLICK_FAILED"
        elif BUILDER_PANEL_TITLE_AREA:
            # Newer-UI machine and no Upgrade buttons — never click blind.
            return "UPGRADE_CLICK_FAILED"

    dialog_closed = False
    for attempt in range(1, max_attempts + 1):
        print(f"[UPGRADE] Attempt {attempt}/{max_attempts}: clicking Confirm at ({confirm_cx}, {confirm_cy})")
        pyautogui.click(confirm_cx, confirm_cy)
        time.sleep(1.3)

        if _need_more_currency_popup_open():
            print("[UPGRADE] Insufficient-currency upsell popup detected for this wall — "
                  "backing out without spending gems")
            # Close whichever dialog is topmost, up to twice (popup, then the
            # main dialog behind it), using image search rather than a fixed
            # coordinate so it works regardless of the popup's exact size.
            for _ in range(2):
                if not _click_close_btn():
                    break
            return "NOT_ENOUGH_RESOURCES"

        if _builders_busy_popup_open():
            print("[UPGRADE] All builders busy (Goblin Builder gem offer) — "
                  "backing out without spending gems")
            # The offer's own X is larger than Close_Btn.PNG and doesn't
            # match it, so tap it at its fixed spot, then close the dialog.
            pyautogui.click(WINDOW_X + BUILDERS_BUSY_POPUP_CLOSE[0],
                            WINDOW_Y + BUILDERS_BUSY_POPUP_CLOSE[1])
            time.sleep(1.0)
            _click_close_btn()
            return "NO_FREE_BUILDER"

        if not _main_upgrade_dialog_open():
            print("[UPGRADE] Dialog closed — Confirm registered")
            dialog_closed = True
            break

        print("[UPGRADE] Dialog still open — Confirm click missed, retrying")

    if not dialog_closed:
        print("[UPGRADE] Dialog still open after all attempts — upgrade NOT applied.")
        return "UPGRADE_CLICK_FAILED"

    # Dialog is confirmed closed, so the resource bar is no longer obscured —
    # read it once more purely to label which currency got spent. This is
    # best-effort only: if OCR still can't get a clean read, the upgrade
    # already succeeded regardless (per the dialog-closed check above).
    close_builder_panel()   # panel shifts the resource bars out of their offsets
    gold_after, elixir_after = _read_resources_after_upgrade()
    print(f"[UPGRADE] Gold before={gold:,} after={gold_after:,} | "
          f"Elixir before={elixir:,} after={elixir_after:,}")

    if gold_after and gold_after <= gold - (WALL_PRICE // 2):
        return "UPGRADED_WITH_GOLD"
    if elixir_after and elixir_after <= elixir - (WALL_PRICE // 2):
        return "UPGRADED_WITH_ELIXIR"

    if gold_after and elixir_after:
        # Both balances read cleanly and neither dropped — the dialog closed
        # for some other reason (e.g. a different popup took over). Treating
        # that as success would loop forever re-"upgrading".
        print("[UPGRADE] Dialog closed but no resources were spent — treating as failed")
        return "UPGRADE_NOT_CONFIRMED"

    print("[UPGRADE] Upgrade succeeded (dialog closed) but currency used couldn't be determined")
    return "UPGRADED"


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

    # Let the window/UI settle before the first OCR read — every read_storage
    # call re-raises/re-focuses the window (see get_absolute_region), and
    # reading immediately after that focus-switch was observed to produce
    # flaky elixir reads even with retries.
    time.sleep(0.5)

    # ── Step 1: read resources ─────────────────────────────
    # Retried — a single flaky OCR read (elixir especially) coming back 0
    # here would poison success-detection for the rest of this attempt.
    print("\n[STEP 1] Reading resources...")
    # The side panel (left open by a previous upgrade) shifts the resource
    # bars left, out of GOLD_OFFSET/ELIXIR_OFFSET.
    close_builder_panel()
    gold   = read_storage_retry(GOLD_OFFSET,   label="GOLD",   debug_name="debug_gold.png")
    elixir = read_storage_retry(ELIXIR_OFFSET, label="ELIXIR", debug_name="debug_elixir.png")

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
        time.sleep(2.0)  # dialog has an open animation; clicking too early misses Confirm

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
#  APPLY PER-MACHINE PROFILE (after every default above is defined)
# ---------------------------------------------------------
globals().update(MACHINE_PROFILES.get(MACHINE_NAME, {}))


# ---------------------------------------------------------
if __name__ == "__main__":
    print(f"[INFO] Machine '{MACHINE_NAME}' — "
          f"{'custom profile' if MACHINE_NAME in MACHINE_PROFILES else 'default offsets'}")
    if not activate_game_window():
        raise SystemExit("LDPlayer not found")

    CONTINUE_ON  = {"UPGRADED_WITH_GOLD", "UPGRADED_WITH_ELIXIR", "UPGRADED"}
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
