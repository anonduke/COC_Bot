"""
Debug: Step 1 — highlights builder button on a screenshot (list closed).
       Step 2 — clicks the builder button, waits for list to open,
                 then highlights the list area on a second screenshot.
Saves:
  debug_step1_builder_btn.png  — builder button boxed (before click)
  debug_step2_list_area.png    — list area boxed (after click / list open)
"""
import time
import cv2
import numpy as np
import pyautogui
import pygetwindow as gw

# ── Regions (relative to LDPlayer top-left, x, y, w, h) ──────────────────
BUILDER_BUTTON = (581,  55,  76,  34)
LIST_AREA      = (437, 130, 202, 422)

CLICK_WAIT     = 1.5   # seconds after clicking builder button


# ──────────────────────────────────────────────────────────────────────────
def get_window():
    wins = gw.getWindowsWithTitle("LDPlayer")
    if not wins:
        print("ERROR: LDPlayer window not found.")
        return None
    w = wins[0]
    w.activate()
    w.restore()
    time.sleep(0.8)
    print(f"LDPlayer found: left={w.left}, top={w.top}, size={w.width}x{w.height}")
    return w


def screenshot_window(win):
    shot = pyautogui.screenshot(region=(win.left, win.top, win.width, win.height))
    return cv2.cvtColor(np.array(shot), cv2.COLOR_RGB2BGR)


def draw_box(img, rel, colour, label):
    x, y, w, h = rel
    cv2.rectangle(img, (x, y), (x + w, y + h), colour, 3)
    # filled label background so text is readable on any background
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
    cv2.rectangle(img, (x, y - th - 8), (x + tw + 6, y), colour, -1)
    cv2.putText(img, label, (x + 3, y - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2, cv2.LINE_AA)


def abs_pt(win, rel_x, rel_y):
    return (win.left + rel_x, win.top + rel_y)


# ──────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    win = get_window()
    if not win:
        raise SystemExit(1)

    # ── Step 1: screenshot with builder button highlighted ─────────────────
    img1 = screenshot_window(win)
    draw_box(img1, BUILDER_BUTTON, (0, 200, 255), "BUILDER BTN")
    cv2.imwrite("debug_step1_builder_btn.png", img1)
    print("Saved: debug_step1_builder_btn.png")

    # ── Step 2: click builder button, wait, screenshot with list area ──────
    bx = BUILDER_BUTTON[0] + BUILDER_BUTTON[2] // 2
    by = BUILDER_BUTTON[1] + BUILDER_BUTTON[3] // 2
    click_abs = abs_pt(win, bx, by)
    print(f"Clicking builder button at screen {click_abs} (rel center {bx},{by}) ...")

    pyautogui.click(*click_abs)
    time.sleep(CLICK_WAIT)

    img2 = screenshot_window(win)
    draw_box(img2, LIST_AREA, (0, 255, 0), "UPGRADE LIST")
    # also keep builder button visible for reference
    draw_box(img2, BUILDER_BUTTON, (0, 200, 255), "BUILDER BTN")
    cv2.imwrite("debug_step2_list_area.png", img2)
    print("Saved: debug_step2_list_area.png")
    print("\nCheck both images to confirm the boxes are correctly placed.")
