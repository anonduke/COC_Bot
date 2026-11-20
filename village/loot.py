"""Enemy loot reading on the scouting screen, cake-event aware.

Ranked_Copy._loot_digit_mask keeps cream-coloured digits (low saturation).
During the cake event ("Loot Available 2x", a cake icon and "2x" after each
value, live 2026-11-19) the loot digits are drawn in YELLOW (saturation ~153
instead of ~51), so only the cake icon survived the mask and the gold read
came out as e.g. 46 or 48: every base was skipped, even with 1-2.5 M gold.

digit_mask() builds the cream mask and a yellow mask and keeps whichever
yields the better row of digit-shaped blobs; the cake icon (cream frosting,
orange cup) is not a digit-shaped blob in a row of digits and is dropped.
Same return value as Ranked_Copy._loot_digit_mask, so it can replace it.
"""
import cv2
import numpy as np

UPSCALE = 4


def _blobs(mask, img_h):
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    keep = [i for i in range(1, n)
            if 35 <= stats[i][3] <= 80                    # digit height at 4x
            and stats[i][2] <= stats[i][3] * 3.5          # up to ~3 touching digits
            and stats[i][4] >= 150
            and stats[i][1] > 2 and stats[i][1] + stats[i][3] < img_h - 2]
    if not keep:
        return labels, stats, []
    heights = np.array([stats[i][3] for i in keep])
    mid_h = np.median(heights)
    row_mid = np.median([stats[i][1] + stats[i][3] / 2 for i in keep])
    # One row of same-height glyphs: drops icons and stray bright patches.
    keep = [i for i in keep if abs(stats[i][1] + stats[i][3] / 2 - row_mid) <= 12
            and abs(stats[i][3] - mid_h) <= 0.2 * mid_h]
    return labels, stats, sorted(keep, key=lambda i: stats[i][0])


def _digits_from(labels, stats, keep):
    imgs = []
    for i in keep:
        x, y, w, bh = stats[i][:4]
        blob = (labels[y:y + bh, x:x + w] == i).astype(np.float32)
        parts = max(1, round(w / (0.82 * bh))) if w > bh * 1.3 else 1
        for p in range(parts):
            piece = blob[:, p * w // parts:(p + 1) * w // parts]
            imgs.append(cv2.resize(piece, (20, 30), interpolation=cv2.INTER_AREA))
    return imgs


def digit_mask(crop_bgr):
    """(black-on-white mask image, [30x20 digit images]) of the loot number."""
    up = cv2.resize(crop_bgr, None, fx=UPSCALE, fy=UPSCALE, interpolation=cv2.INTER_CUBIC)
    h, s, v = cv2.split(cv2.cvtColor(up, cv2.COLOR_BGR2HSV))
    not_blue = ~((h >= 80) & (h <= 140) & (s > 20))
    cream = ((v > 170) & (s < 90) & not_blue).astype(np.uint8) * 255
    yellow = ((v > 170) & (h >= 20) & (h <= 38) & (s >= 90) & (s <= 230)).astype(np.uint8) * 255
    best = None
    for mask in (cream, yellow):
        labels, stats, keep = _blobs(mask, mask.shape[0])
        digits = _digits_from(labels, stats, keep)
        score = len(digits)
        if best is None or score > best[0]:
            best = (score, labels, keep, digits)
    _, labels, keep, digits = best
    out = np.zeros(labels.shape, np.uint8)
    for i in keep:
        out[labels == i] = 255
    return cv2.copyMakeBorder(cv2.bitwise_not(out), 20, 20, 20, 20,
                              cv2.BORDER_CONSTANT, value=255), digits
