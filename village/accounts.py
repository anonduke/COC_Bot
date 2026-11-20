"""Switching between the game accounts linked to the Supercell ID.

In the game: profile (top left) -> Settings tab -> Supercell ID bar shows the
current account's tag -> the blue switch button opens a side panel listing the
linked accounts as cards ("<Supercell ID name>", "<village name>",
"Town Hall: N"). Tapping a card restarts the game on that account (no
confirmation dialog, about 6 s, measured 2026-11-20). Below the cards there is
"OTHER IDS / Log in to Supercell ID", which must never be tapped.

Accounts are identified by their player tag. The card only shows names, so
the Supercell ID name is learnt per tag (discover()) and cards are matched by
that exact name. Whatever this module does, the caller verifies the result
with an export whose tag must match: a wrong switch is caught there and never
mixes data between accounts.
"""
import os
import re
import time

import cv2
import numpy as np

from . import adb
from .capture import _find, _template
from .home import ensure_home

PROFILE_TAP = (80, 100)          # XP / Town Hall badge, home screen
SETTINGS_TAB = (1265, 74)
SWITCH_BUTTON = (1054, 171)
PANEL_CLOSE = (1544, 53)
PROFILE_CLOSE = (1398, 70)
SCID_BAR_POS = (290, 150)        # misc/scid_bar.png in the profile Settings tab
PANEL_POS = (1250, 325)          # misc/scid_switch_panel.png ("SWITCH ID")
TAG_BOX = (725, 174, 905, 202)   # current account's tag under its name
LIST_BOX = (1025, 430, 1600, 900)
LIST_DRAG = (1300, 850, 1300, 450)
TAG_CHARS = "#0289PYLQGRJCUV"


class SwitchError(RuntimeError):
    pass


def _at(img, name, pos, tol=6):
    score, (x, y) = _find(img, _template(name))
    return score >= 0.8 and abs(x - pos[0]) <= tol and abs(y - pos[1]) <= tol


def _wait(check, timeout, serial, step=0.7):
    end = time.time() + timeout
    while time.time() < end:
        img = adb.screencap(serial)
        if check(img):
            return img
        time.sleep(step)
    return None


def read_tag(img):
    """The current account's tag from the Supercell ID bar (light cyan text)."""
    from .upgrader import _tesseract
    x0, y0, x1, y1 = TAG_BOX
    up = cv2.resize(img[y0:y1, x0:x1], None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    mask = np.where(up[:, :, 1] > 140, 0, 255).astype(np.uint8)
    text = _tesseract().image_to_string(
        mask, config=f"--psm 7 -c tessedit_char_whitelist={TAG_CHARS}").strip()
    return text if re.fullmatch(r"#[0289PYLQGRJCUV]{5,14}", text) else None


def same_tag(a, b):
    """Equal, or one OCR slip apart (same length, one character differs)."""
    if not a or not b or len(a) != len(b):
        return False
    return sum(x != y for x, y in zip(a, b)) <= 1


def norm(name):
    return re.sub(r"\s+", "", (name or "")).lower()


def read_cards(img):
    """Account cards visible in the switch panel, top to bottom:
    [{"scid", "village", "town_hall", "y"}] (y = the name line, to tap)."""
    from .upgrader import _tesseract
    t = _tesseract()
    x0, y0, x1, y1 = LIST_BOX
    g = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    d = t.image_to_data(g, config="--psm 4", output_type=t.Output.DICT)
    lines = {}
    for i, word in enumerate(d["text"]):
        if word.strip() and float(d["conf"][i]) > 30:
            key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
            lines.setdefault(key, []).append((d["left"][i] // 2 + x0, d["top"][i] // 2 + y0, word))
    rows = sorted((ws[0][1], ws[0][0], " ".join(w for *_, w in ws)) for ws in lines.values())
    cards = []
    for y, x, text in rows:
        if re.search(r"OTHER IDS|Log in", text, re.I):
            break                      # everything below is not a linked account
        m = re.match(r"Town Hall:?\s*(\d+)", text)
        if not m:
            continue
        # The two lines above it, left-aligned with the card text.
        above = [r for r in rows if y - 75 <= r[0] < y - 5 and 1130 <= r[1] <= 1200]
        name = [r for r in above if r[0] < y - 40]
        village = [r for r in above if r[0] >= y - 40]
        if name:
            cards.append({"scid": name[-1][2], "village": village[-1][2] if village else "",
                          "town_hall": int(m.group(1)), "y": name[-1][0] + 8})
    return cards


def open_profile_settings(serial=adb.SERIAL, log=print):
    """From anywhere: home screen, profile, Settings tab. Returns the screenshot
    with the Supercell ID bar."""
    if not ensure_home(serial, log):
        raise SwitchError("could not reach the home screen")
    adb.tap(*PROFILE_TAP, serial)
    time.sleep(1.5)
    adb.tap(*SETTINGS_TAB, serial)
    img = _wait(lambda i: _at(i, "scid_bar.png", SCID_BAR_POS), 8, serial)
    if img is None:
        adb.shell("input keyevent 4", serial=serial)
        raise SwitchError("the profile Settings tab did not open")
    return img


def current_tag(serial=adb.SERIAL, log=print):
    """The tag the game is on now (profile -> Settings), then back home."""
    img = open_profile_settings(serial, log)
    tag = read_tag(img)
    adb.tap(*PROFILE_CLOSE, serial)
    time.sleep(1)
    return tag


def _open_panel(serial):
    adb.tap(*SWITCH_BUTTON, serial)
    img = _wait(lambda i: _at(i, "scid_switch_panel.png", PANEL_POS), 10, serial)
    if img is None:
        raise SwitchError("the Supercell ID switch panel did not open")
    time.sleep(0.8)                    # cards slide in
    return adb.screencap(serial)


def _close_all(serial):
    for xy in (PANEL_CLOSE, PROFILE_CLOSE):
        adb.tap(*xy, serial)
        time.sleep(1.2)


def list_cards(serial=adb.SERIAL, log=print, max_drags=4):
    """All linked accounts in the panel (scrolling as needed), plus the
    current tag. Leaves the game on the home screen, on the same account."""
    img = open_profile_settings(serial, log)
    tag = read_tag(img)
    img = _open_panel(serial)
    seen, cards = set(), []
    for i in range(max_drags + 1):
        new = [c for c in read_cards(img) if norm(c["scid"]) not in seen]
        for c in new:
            seen.add(norm(c["scid"]))
            cards.append({k: c[k] for k in ("scid", "village", "town_hall")})
        if i == max_drags or not new and i:
            break
        adb.swipe(*LIST_DRAG, 800, serial=serial)
        time.sleep(1)
        img = adb.screencap(serial)
    _close_all(serial)
    ensure_home(serial, log)
    return tag, cards


def _tap_card(scid, serial, log, step, max_drags=4):
    """With the profile Settings tab open: open the switch panel, tap the card
    named `scid` and wait for the game to restart on it."""
    step("Opening the Supercell ID account list")
    img = _open_panel(serial)
    for i in range(max_drags + 1):
        card = next((c for c in read_cards(img) if norm(c["scid"]) == norm(scid)), None)
        if card:
            step(f"Switching to {card['scid']}")
            adb.tap(1300, card["y"], serial)
            break
        if i == max_drags:
            _close_all(serial)
            raise SwitchError(f"no account named {scid!r} in the Supercell ID list")
        adb.swipe(*LIST_DRAG, 800, serial=serial)
        time.sleep(1)
        img = adb.screencap(serial)
    step("The game is restarting on the other account")
    time.sleep(6)
    if not ensure_home(serial, log, first_timeout=90):
        raise SwitchError("the game did not come back to the home screen after switching")


def switch_to(tag, scid, serial=adb.SERIAL, log=print, step=None):
    """Make the game run account `tag` (Supercell ID name `scid`). Returns
    "already" or "switched"; the game is on its home screen afterwards.
    The caller must still verify the tag with an export."""
    step = step or (lambda text: log(f"accounts: {text}"))
    step("Opening the profile")
    img = open_profile_settings(serial, log)
    now = read_tag(img)
    if same_tag(now, tag):
        log(f"accounts: already on {tag}")
        adb.tap(*PROFILE_CLOSE, serial)
        time.sleep(1)
        return "already"
    if not scid:
        adb.tap(*PROFILE_CLOSE, serial)
        raise SwitchError(f"the Supercell ID name of {tag} is not known yet: run Find accounts")
    log(f"accounts: on {now or 'unknown'}, switching to {scid} ({tag})")
    _tap_card(scid, serial, log, step)
    return "switched"


# --- with the store: verified captures and the dashboard's jobs -----------------

def capture_for(store, tag, log=print, step=None):
    """Capture account `tag`, switching the game to it first if the export
    shows another account. Never returns another account's data."""
    from .capture import TagMismatch, capture_export, capture_lock
    with capture_lock(wait=30):        # held across capture, switch and capture
        try:
            return capture_export(expected_tag=tag, log=log)
        except TagMismatch as e:
            acct = store.account(tag) or {}
            if not acct.get("scid"):
                raise SwitchError(f"the game is on {e.found}, not {tag}, and the Supercell ID "
                                  "name of that account is unknown: run Find accounts") from None
            log(f"accounts: the game is on {e.found}; switching to {tag}")
            switch_to(tag, acct["scid"], log=log, step=step)
            return capture_export(expected_tag=tag, log=log)


def ensure_account(store, tag, log=print):
    """Before farming: make sure the game is on `tag` (switch if needed)."""
    from .capture import capture_lock
    acct = store.account(tag) or {}
    with capture_lock(wait=30):
        return switch_to(tag, acct.get("scid"), log=log)


def save_capture(store, cap, gd_version, position=None, scid=None):
    """Store a capture and refresh the account's row from it."""
    from .catalog import TOWN_HALL
    sid = store.save_snapshot(cap.snapshot, cap.resources, game_version=gd_version,
                              captured_at=cap.home_at, counter=cap.counter, player=cap.player)
    fields = {"town_hall": cap.snapshot.level_of(TOWN_HALL)}
    if cap.player and cap.player.get("name"):
        fields["village"] = cap.player["name"]
    if scid:
        fields["scid"] = scid
    if position is not None:
        fields["position"] = position
    store.upsert_account(cap.snapshot.tag, **fields)
    return sid


JOB_KEY = "account_job"


class Job:
    """Progress of a switch / discover job for the dashboard (kv account_job)."""

    def __init__(self, store, kind, target=None, log=print):
        self.store, self.log = store, log
        self.state = {"kind": kind, "target": target, "state": "running", "step": "Starting",
                      "steps": [], "started_at": int(time.time()), "ended_at": None,
                      "message": "", "pid": os.getpid()}
        self._save()

    def _save(self):
        self.store.kv_set(JOB_KEY, self.state)

    def step(self, text):
        self.log(f"accounts: {text}")
        self.state["step"] = text
        self.state["steps"].append([int(time.time()), text])
        self._save()

    def end(self, state, message):
        self.state.update(state=state, message=message, ended_at=int(time.time()),
                          step="Done" if state == "done" else "Failed")
        self._save()


def _gd_version(log):
    from . import gamedata
    try:
        return gamedata.ensure_static_data().version
    except Exception as e:  # noqa: BLE001 - names and costs can come later
        log(f"accounts: game data not available ({e})")
        return None


def run_switch(store, tag, log=print):
    """Dashboard job: switch the game to `tag`, read its village, and only
    then make it the active account."""
    from . import emulator
    from .capture import capture_lock
    acct = store.account(tag)
    job = Job(store, "switch", tag, log)
    try:
        if acct is None:
            raise SwitchError(f"unknown account {tag}")
        with capture_lock(wait=30):
            job.step("Getting the game ready")
            emulator.ensure_ready(log)
            switch_to(tag, acct.get("scid"), log=log, step=job.step)
            job.step("Reading the village (data export)")
            cap = capture_for(store, tag, log, job.step)
            save_capture(store, cap, _gd_version(log))
        store.set_active(tag)
        name = (acct.get("display_name") or (cap.player or {}).get("name")
                or acct.get("village") or tag)
        job.end("done", f"Now on {name} ({tag})")
        return 0
    except Exception as e:  # noqa: BLE001 - reported to the dashboard
        job.end("failed", f"{type(e).__name__}: {e}")
        store.notify("error", "accounts", f"Switching to {tag} failed", str(e), tag=None)
        return 1


def run_restart(store, log=print):
    """Dashboard job: close LDPlayer completely (force-closing it if it hangs),
    restart adb, start LDPlayer and the game, and read the active account's
    village to prove everything works. Uses the same progress record as the
    account jobs, so the page shows it the same way."""
    from . import emulator
    from .capture import capture_export, capture_lock
    job = Job(store, "restart", store.active_tag(), log)
    try:
        with capture_lock(wait=30):
            job.step("Closing LDPlayer")
            emulator.stop(log)
            job.step("Restarting adb")
            emulator.restart_adb(log)
            job.step("Starting LDPlayer (Android boots in 1-3 min)")
            emulator.start(log)
            job.step("Opening Clash of Clans")
            emulator.ensure_ready(log)
            tag = store.active_tag()
            job.step("Reading the village (data export)")
            cap = capture_for(store, tag, log, job.step) if tag else capture_export(log=log)
            save_capture(store, cap, _gd_version(log))
        if tag:     # failures before the restart shouldn't delay the next session
            store.kv_set(store.akey("fail_streak", tag), {"n": 0, "at": int(time.time())})
        job.end("done", "LDPlayer and the game restarted; village read")
        return 0
    except Exception as e:  # noqa: BLE001 - reported to the dashboard
        job.end("failed", f"{type(e).__name__}: {e}")
        store.notify("error", "emulator", "Restarting LDPlayer failed", str(e), tag=None)
        return 1


def run_discover(store, log=print):
    """Dashboard job: read the Supercell ID account list, learn the tag of
    every account (switching to the unknown ones and reading their village),
    then go back to the active account."""
    from . import emulator
    from .capture import capture_export, capture_lock
    job = Job(store, "discover", None, log)
    try:
        with capture_lock(wait=30):
            job.step("Getting the game ready")
            emulator.ensure_ready(log)
            version = _gd_version(log)
            job.step("Reading the account list")
            now_tag, cards = list_cards(log=log)
            if not cards:
                raise SwitchError("no accounts found in the Supercell ID list")
            known = {norm(a["scid"]): a for a in store.accounts() if a["scid"]}
            found = []
            for pos, c in enumerate(cards):
                a = known.get(norm(c["scid"]))
                if a:
                    store.upsert_account(a["tag"], position=pos, town_hall=c["town_hall"])
                    found.append(a["tag"])
                    continue
                job.step(f"New account {c['scid']}: switching to it")
                open_profile_settings(log=log)
                _tap_card(c["scid"], adb.SERIAL, log, job.step)
                job.step(f"Reading the village of {c['scid']}")
                cap = capture_export(log=log)
                save_capture(store, cap, version, position=pos, scid=c["scid"])
                found.append(cap.snapshot.tag)
            if not store.active_tag() and found:
                store.set_active(now_tag if now_tag in found else found[0])
            active = store.active_tag()
            back = store.account(active)
            if back and back.get("scid"):
                job.step("Going back to the active account")
                switch_to(active, back["scid"], log=log, step=job.step)
                job.step("Reading the village (data export)")
                save_capture(store, capture_for(store, active, log, job.step), version)
        job.end("done", f"{len(cards)} account(s): " + ", ".join(c["scid"] for c in cards))
        return 0
    except Exception as e:  # noqa: BLE001
        job.end("failed", f"{type(e).__name__}: {e}")
        store.notify("error", "accounts", "Finding accounts failed", str(e), tag=None)
        return 1
