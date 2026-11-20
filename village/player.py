"""Player profile from the home screen (top-left): name, clan, XP level.

Read during a capture from the home screenshot already taken there, so it
costs no extra game actions. OCR of the stylised name font is close but not
exact (case, spacing), so the dashboard prefers the `player_name` setting.
"""
import re

NAME_BOX = (150, 66, 420, 104)
CLAN_BOX = (150, 100, 400, 128)
XP_BOX = (14, 32, 76, 70)


def _ocr(img, box, config):
    from .upgrader import _tesseract, _white_text
    x0, y0, x1, y1 = box
    return _tesseract().image_to_string(_white_text(img[y0:y1, x0:x1], 3), config=config).strip()


def clean_name(text):
    """First line, printable, without stray OCR punctuation at the ends."""
    line = (text or "").splitlines()[0] if text else ""
    line = re.sub(r"[^\w .'\-]", "", line).strip(" .-'")
    return re.sub(r"\s+", " ", line)[:24]


def clean_clan(text):
    """The clan name, cut where OCR noise (symbols) starts."""
    m = re.match(r"[\w .'\-]+", (text or "").strip())
    return re.sub(r"\s+", " ", m.group(0)).strip(" .-'")[:24] if m else ""


def _has_text(img, box, min_share=0.04):
    """True if the box shows bright name text (not a dimmed or empty screen)."""
    from .upgrader import _white_text
    x0, y0, x1, y1 = box
    return (_white_text(img[y0:y1, x0:x1], 1) == 0).mean() >= min_share


def read_player(img):
    """{"name", "clan", "xp"} from a home-screen image, or None when the
    name isn't readable (popup, wrong screen)."""
    try:
        if not _has_text(img, NAME_BOX):
            return None
        name = clean_name(_ocr(img, NAME_BOX, "--psm 7"))
        clan = clean_clan(_ocr(img, CLAN_BOX, "--psm 7"))
        xp_text = _ocr(img, XP_BOX, "--psm 7 -c tessedit_char_whitelist=0123456789")
    except Exception:  # noqa: BLE001 - the profile is cosmetic; never fail a capture
        return None
    if len(name) < 2:
        return None
    xp = int(xp_text) if xp_text.isdigit() and 1 <= int(xp_text) <= 999 else None
    return {"name": name, "clan": clan or None, "xp": xp}
