"""
Diagnostic: tries many OCR preprocessing methods on debug_gold.png / debug_elixir.png
Run this without the game open. It prints what each method reads and saves all processed images.
"""
import cv2
import numpy as np
import pytesseract

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

SCALE = 4   # upscale factor


def load_image(path):
    img = cv2.imread(path)
    if img is None:
        raise FileNotFoundError(f"Not found: {path}")
    return img


def parse_number(raw):
    digits = "".join(ch for ch in raw if ch.isdigit() or ch == " ").strip()
    digits = " ".join(digits.split())
    merged = "".join(digits.split())
    if len(merged) >= 6:
        if len(merged) > 8:
            merged = merged[-8:]
        return int(merged)
    return 0


def run_ocr(img_gray_scaled, psm=7):
    raws = []
    for p in [psm, 6, 13]:
        raw = pytesseract.image_to_string(
            img_gray_scaled,
            config=f"--oem 3 --psm {p} -c tessedit_char_whitelist=0123456789 "
        )
        val = parse_number(raw)
        raws.append((p, raw.strip(), val))
    # return best non-zero
    for _, raw, val in sorted(raws, key=lambda x: -x[2]):
        if val > 0:
            return val, raw
    return 0, raws[0][1]


def try_all(name, img_bgr):
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    up   = cv2.resize(gray, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_CUBIC)

    results = []

    # ── Method 1: fixed threshold 150 ────────────────────────────────────────
    _, m1 = cv2.threshold(up, 150, 255, cv2.THRESH_BINARY)
    m1i = cv2.bitwise_not(m1)
    cv2.imwrite(f"ocr_{name}_m1_thresh150_inv.png", m1i)
    val, raw = run_ocr(m1i)
    results.append(("thresh150+invert", raw, val))

    # ── Method 2: fixed threshold 175 ────────────────────────────────────────
    _, m2 = cv2.threshold(up, 175, 255, cv2.THRESH_BINARY)
    m2i = cv2.bitwise_not(m2)
    cv2.imwrite(f"ocr_{name}_m2_thresh175_inv.png", m2i)
    val, raw = run_ocr(m2i)
    results.append(("thresh175+invert", raw, val))

    # ── Method 3: fixed threshold 200 ────────────────────────────────────────
    _, m3 = cv2.threshold(up, 200, 255, cv2.THRESH_BINARY)
    m3i = cv2.bitwise_not(m3)
    cv2.imwrite(f"ocr_{name}_m3_thresh200_inv.png", m3i)
    val, raw = run_ocr(m3i)
    results.append(("thresh200+invert", raw, val))

    # ── Method 4: thresh175 + morphological close (fill digit holes) ─────────
    _, m4 = cv2.threshold(up, 175, 255, cv2.THRESH_BINARY)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    m4 = cv2.morphologyEx(m4, cv2.MORPH_CLOSE, k)
    m4i = cv2.bitwise_not(m4)
    cv2.imwrite(f"ocr_{name}_m4_thresh175_close_inv.png", m4i)
    val, raw = run_ocr(m4i)
    results.append(("thresh175+close+invert", raw, val))

    # ── Method 5: thresh175 + open (remove noise) + close (fill holes) ───────
    _, m5 = cv2.threshold(up, 175, 255, cv2.THRESH_BINARY)
    k2 = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    m5 = cv2.morphologyEx(m5, cv2.MORPH_OPEN, k2)
    m5 = cv2.morphologyEx(m5, cv2.MORPH_CLOSE, k)
    m5i = cv2.bitwise_not(m5)
    cv2.imwrite(f"ocr_{name}_m5_thresh175_open_close_inv.png", m5i)
    val, raw = run_ocr(m5i)
    results.append(("thresh175+open+close+invert", raw, val))

    # ── Method 6: OTSU ───────────────────────────────────────────────────────
    blurred = cv2.GaussianBlur(up, (3, 3), 0)
    _, m6 = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    m6i = cv2.bitwise_not(m6)
    cv2.imwrite(f"ocr_{name}_m6_otsu_inv.png", m6i)
    val, raw = run_ocr(m6i)
    results.append(("otsu+invert", raw, val))

    # ── Method 7: adaptive threshold ─────────────────────────────────────────
    blurred2 = cv2.GaussianBlur(up, (3, 3), 0)
    m7 = cv2.adaptiveThreshold(blurred2, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                cv2.THRESH_BINARY, 15, -5)
    cv2.imwrite(f"ocr_{name}_m7_adaptive.png", m7)
    val, raw = run_ocr(m7)
    results.append(("adaptive(mean,-5)", raw, val))

    # ── Method 8: extract only right 60% of image (skip resource bar) ────────
    h, w = up.shape
    crop = up[:, int(w * 0.40):]
    _, m8 = cv2.threshold(crop, 175, 255, cv2.THRESH_BINARY)
    m8i = cv2.bitwise_not(m8)
    cv2.imwrite(f"ocr_{name}_m8_crop60_thresh175_inv.png", m8i)
    val, raw = run_ocr(m8i)
    results.append(("crop_right60%+thresh175", raw, val))

    # ── Method 9: right 60% + close ──────────────────────────────────────────
    m9 = cv2.morphologyEx(m8, cv2.MORPH_CLOSE, k)
    m9i = cv2.bitwise_not(m9)
    cv2.imwrite(f"ocr_{name}_m9_crop60_close_inv.png", m9i)
    val, raw = run_ocr(m9i)
    results.append(("crop_right60%+thresh175+close", raw, val))

    # ── Method 10: use colour image — extract red channel (bright=white) ─────
    r_ch = img_bgr[:, :, 2]    # red channel — white text has high R
    r_up = cv2.resize(r_ch, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_CUBIC)
    _, m10 = cv2.threshold(r_up, 200, 255, cv2.THRESH_BINARY)
    m10i = cv2.bitwise_not(m10)
    cv2.imwrite(f"ocr_{name}_m10_redchan_inv.png", m10i)
    val, raw = run_ocr(m10i)
    results.append(("red_channel+thresh200", raw, val))

    # ── print results ─────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  {name.upper()}")
    print(f"{'='*60}")
    best_val = 0
    best_method = ""
    for method, raw_text, parsed in results:
        marker = "  "
        if parsed >= 1_000_000:
            marker = "OK"
            if parsed > best_val:
                best_val = parsed
                best_method = method
        print(f"  {marker}{method:<40} raw='{raw_text}'  ->  {parsed:,}")
    print(f"\n  >>> BEST: {best_method}  =  {best_val:,}\n")
    return best_val, best_method


if __name__ == "__main__":
    for label, path in [("gold", "debug_gold.png"), ("elixir", "debug_elixir.png")]:
        try:
            img = load_image(path)
            try_all(label, img)
        except FileNotFoundError as e:
            print(e)
