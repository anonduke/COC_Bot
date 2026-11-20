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
# LDLDBroadScreenWndIE: a 264 px ad panel LDPlayer docks to the right of the
# window (seen 2026-11-19); it pushed the window to (0, 0) and resized it.
AD_CLASSES = ("adframe", "ADFullScreenFrame", "LDLDBroadScreenWndIE")

# Ranked_Copy / wall.py were calibrated with the game picture at exactly this
# screen rectangle (x, y, w, h); their clicks and templates depend on it.
RENDER_TARGET = (85, 44, 1196, 673)
WINDOW_POS = (84, 10)
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


def _rect(hwnd):
    r = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


def fit_window(log=print):
    """Put the game picture back at RENDER_TARGET: hide LDPlayer's ad panels,
    restore the window and size it so the picture lands there. LDPlayer
    centres the picture (with or without its right toolbar), so the width is
    adjusted from the measured result. Returns True when it fits."""
    user32 = ctypes.windll.user32
    main = user32.FindWindowW("LDPlayerMainFrame", None)
    if not main:
        return False
    render = user32.FindWindowExW(main, None, "RenderWindow", None)
    if not render:
        return False
    hide_ad_windows()
    if _rect(render) == RENDER_TARGET:
        return True
    before = _rect(main)
    tx, ty, tw, th = RENDER_TARGET
    width, height = tw + 40, th + 35          # first guess: the old layout
    user32.ShowWindow(main, 9)                 # SW_RESTORE (in case maximised)
    for _ in range(4):
        user32.SetWindowPos(main, 0, WINDOW_POS[0], WINDOW_POS[1], width, height,
                            0x0004 | 0x0010)  # SWP_NOZORDER | SWP_NOACTIVATE
        time.sleep(1.5)
        x, y, w, h = _rect(render)
        if (x, y, w, h) == RENDER_TARGET:
            log(f"LDPlayer window fitted: {before} -> {_rect(main)}")
            return True
        width += (tw - w) - 2 * (x - tx)       # correct size and centring offset
        height += th - h
    log(f"could not fit the LDPlayer window (render {_rect(render)}, want {RENDER_TARGET})")
    return False


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


# Window, VM, and the VirtualBox helpers. Live 2026-11-20: after a hang, a
# stale VBoxNetDHCP / Ld9BoxSVC made the next boot come up without network
# (no VBoxNetNAT, adb never connected) until every one of these was killed.
LD_PROCESSES = ("dnplayer.exe", "Ld9BoxHeadless.exe", "VBoxNetNAT.exe", "VBoxNetDHCP.exe",
                "Ld9BoxSVC.exe")


def _stopped():
    try:
        return not adb_online() and not ld_running() and _processes_gone()
    except (EmulatorError, subprocess.SubprocessError, OSError):
        return False


def _wait_stopped(seconds):
    end = time.time() + seconds
    while time.time() < end:
        if _stopped():
            time.sleep(3)
            return True
        time.sleep(2)
    return False


def kill_processes(log=print):
    """Force-close LDPlayer's window and VM processes (for a hung emulator
    that ignores `ldconsole quit`)."""
    for name in LD_PROCESSES:
        r = subprocess.run(["taskkill", "/F", "/T", "/IM", name], capture_output=True,
                           text=True, timeout=30)
        if r.returncode == 0:
            log(f"force-closed {name}")


def stop(log=print, quit_wait=45):
    """Quit LDPlayer and wait until it has really finished shutting down
    (adb going offline happens long before the VM processes exit). If it
    doesn't (live 2026-11-20: hung graphics, `quit` ignored, VBoxNetNAT left
    behind), force-close its processes."""
    log("stopping LDPlayer")
    try:
        ldconsole("quit", "--index", INDEX)
    except EmulatorError as e:
        log(f"ldconsole quit: {e}")
    if not _wait_stopped(quit_wait):
        log(f"LDPlayer did not shut down within {quit_wait}s: force-closing it")
    # Also after a clean quit: leftover VirtualBox helpers break the next boot.
    kill_processes(log)
    if not _wait_stopped(45):
        raise EmulatorError("LDPlayer is still running after force-closing it")


def restart_adb(log=print):
    """Restart the adb server (it can hang along with the emulator)."""
    log("restarting adb")
    subprocess.run([adb.ADB, "kill-server"], capture_output=True, timeout=30)
    time.sleep(2)
    subprocess.run([adb.ADB, "start-server"], capture_output=True, timeout=60)


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
    fit_window(log)


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
