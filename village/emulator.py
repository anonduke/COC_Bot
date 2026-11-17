"""Emulator manager (Phase 4): get LDPlayer + Clash to a usable home screen
from any state, using only adb, ldconsole and Win32.

ensure_ready() steps, each checked before the next:
  1. LDPlayer running and Android booted (ldconsole launch, wait for
     sys.boot_completed); a System UI "isn't responding" dialog is answered
     with Wait (restarting System UI makes it come back, see Phase 0/2);
  2. LDPlayer's own ad windows hidden (Win32, classes adframe /
     ADFullScreenFrame);
  3. Clash in front (monkey launch, wait for focus);
  4. home screen (village.home.ensure_home: popups, RELOAD GAME, restart);
  5. picture not frozen (two frames differ: the village always animates).
     A frozen picture gets one emulator restart.
Every failure ends with a reason, never a hang: every wait has a timeout.
"""
import ctypes
import os
import subprocess
import time
from ctypes import wintypes

import numpy as np

from . import adb
from .home import ensure_home

LDCONSOLE = os.path.join(adb.LDPLAYER_DIR, "ldconsole.exe")
INDEX = "0"
AD_CLASSES = ("adframe", "ADFullScreenFrame")
BOOT_TIMEOUT = 180


class EmulatorError(RuntimeError):
    pass


def ldconsole(*args, timeout=60):
    try:
        r = subprocess.run([LDCONSOLE, *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise EmulatorError(f"ldconsole {args} timed out") from e
    return r.stdout


def adb_online(serial=adb.SERIAL):
    try:
        out = subprocess.run([adb.ADB, "devices"], capture_output=True, text=True, timeout=15).stdout
    except subprocess.TimeoutExpired:
        return False
    return any(line.split("\t") == [serial, "device"] for line in out.splitlines())


def booted(serial=adb.SERIAL):
    if not adb_online(serial):
        return False
    try:
        return adb.shell("getprop sys.boot_completed", timeout=10, serial=serial) == "1"
    except adb.AdbError:
        return False


def hide_ad_windows():
    """Hide LDPlayer's ad windows; returns how many were hidden."""
    user32 = ctypes.windll.user32
    hidden = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, buf, 64)
        if buf.value in AD_CLASSES and user32.IsWindowVisible(h):
            user32.ShowWindow(h, 0)
            hidden.append(buf.value)
        return True

    user32.EnumWindows(proto(cb), 0)
    return len(hidden)


def ld_running():
    return ldconsole("isrunning", "--index", INDEX, timeout=20).strip() == "running"


def _processes_gone(names=("Ld9BoxHeadless.exe", "VBoxNetNAT.exe")):
    out = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=30).stdout.lower()
    return not any(n.lower() in out for n in names)


def start(log=print, serial=adb.SERIAL, _retry=True):
    log("starting LDPlayer")
    ldconsole("launch", "--index", INDEX)
    end = time.time() + BOOT_TIMEOUT
    while time.time() < end:
        if booted(serial):
            log("Android booted")
            time.sleep(8)          # let System UI settle before the first checks
            return
        time.sleep(3)
    # Live 2026-11-16: relaunched too soon after a quit, LDPlayer booted
    # Android but its network helper (VBoxNetNAT) never started, so adb
    # could not connect. A clean stop + launch fixed it.
    if _retry and ld_running():
        log("Android up but adb can't reach it: restarting LDPlayer once")
        stop(log)
        return start(log, serial, _retry=False)
    raise EmulatorError(f"Android did not finish booting within {BOOT_TIMEOUT}s")


def stop(log=print):
    """Quit LDPlayer and wait until it has really finished shutting down
    (adb going offline happens long before the VM processes exit)."""
    log("stopping LDPlayer")
    ldconsole("quit", "--index", INDEX)
    end = time.time() + 90
    while time.time() < end:
        if not adb_online() and not ld_running() and _processes_gone():
            time.sleep(3)
            return
        time.sleep(2)
    raise EmulatorError("LDPlayer did not shut down within 90s")


def launch_clash(serial=adb.SERIAL):
    adb.shell(f"monkey -p {adb.COC_PACKAGE} -c android.intent.category.LAUNCHER 1", serial=serial)
    end = time.time() + 40
    while time.time() < end:
        adb.dismiss_systemui_anr(serial)
        if adb.wait_for_focus(adb.COC_PACKAGE, 3, serial):
            return True
    return False


def picture_frozen(serial=adb.SERIAL, gap=4.0):
    a = adb.screencap(serial).astype(np.int16)
    time.sleep(gap)
    b = adb.screencap(serial).astype(np.int16)
    return int((np.abs(a - b).sum(axis=2) > 30).sum()) == 0


def _ready_once(log, serial):
    """One attempt; raises EmulatorError / adb.AdbError on any failure."""
    if not booted(serial):
        start(log, serial)
    for _ in range(3):
        if adb.dismiss_systemui_anr(serial):
            log("answered a System UI dialog with Wait")
            time.sleep(2)
    if hide_ad_windows():
        log("hid LDPlayer ad windows")
    fresh = False
    if f"{adb.COC_PACKAGE}/" not in adb.focused_window(serial):
        log("launching Clash")
        if not launch_clash(serial):
            raise EmulatorError(f"Clash did not come to the front: {adb.focused_window(serial)}")
        fresh = True
    # A cold game load takes over a minute on this PC (live 2026-11-16).
    if not ensure_home(serial, log, first_timeout=180 if fresh else 60):
        raise EmulatorError("could not reach the home screen")
    if picture_frozen(serial):
        raise EmulatorError("the game picture is frozen")
    hide_ad_windows()


def ensure_ready(log=print, serial=adb.SERIAL):
    """Bring the game to its home screen. Any failure (hung graphics: no
    focus, black screenshots, launch timing out; a frozen picture; home not
    reachable) gets ONE full emulator restart, then the error is raised.
    Live 2026-11-17: after 16 h the emulator's graphics hung (no focused
    window, black frames) and every session failed until it was restarted."""
    try:
        _ready_once(log, serial)
        return True
    except (EmulatorError, adb.AdbError) as e:
        log(f"emulator not usable ({e}): restarting it")
    stop(log)
    start(log, serial)
    try:
        _ready_once(log, serial)
    except (EmulatorError, adb.AdbError) as e:
        raise EmulatorError(f"still not usable after an emulator restart: {e}") from e
    return True
