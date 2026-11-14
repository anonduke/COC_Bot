"""Minimal adb wrapper. Phase 4 (emulator manager) builds on this."""
import os
import subprocess
import time

LDPLAYER_DIR = r"C:\LDPlayer\LDPlayer9"
ADB = LDPLAYER_DIR + r"\adb.exe"
SERIAL = "emulator-5554"
COC_PACKAGE = "com.supercell.clashofclans"
_MISC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "misc")


class AdbError(RuntimeError):
    pass


def run(*args, timeout=30, serial=SERIAL):
    """Run `adb -s <serial> <args>` and return stdout. Raises AdbError on a
    non-zero exit or a timeout (adb can hang when the emulator is wedged)."""
    try:
        r = subprocess.run([ADB, "-s", serial, *args], capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise AdbError(f"adb {args} timed out after {timeout}s") from e
    if r.returncode != 0:
        raise AdbError(f"adb {args} failed ({r.returncode}): {r.stderr.strip()}")
    return r.stdout


def shell(cmd, timeout=30, serial=SERIAL):
    return run("shell", cmd, timeout=timeout, serial=serial).strip()


def screencap(serial=SERIAL, attempts=3):
    """Screenshot of the emulator as a BGR numpy array (900x1600x3), taken
    through adb so the LDPlayer window may be covered. Uses the raw format
    (~1 s; PNG takes ~10 s on this emulator). Occasionally adb returns an
    all-black frame; those are retried."""
    import cv2
    import numpy as np
    for _ in range(attempts):
        try:
            r = subprocess.run([ADB, "-s", serial, "exec-out", "screencap"],
                               capture_output=True, timeout=20)
        except subprocess.TimeoutExpired:
            continue
        raw = r.stdout
        if len(raw) >= 12:
            w, h, fmt = np.frombuffer(raw[:12], dtype="<u4")
            n = int(w) * int(h) * 4
            # Header is 12 or 16 bytes depending on the Android version.
            if fmt == 1 and len(raw) - n in (12, 16):  # RGBA_8888
                rgba = np.frombuffer(raw[len(raw) - n:], np.uint8).reshape(int(h), int(w), 4)
                img = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
                if img.mean() > 5:
                    return img
        time.sleep(1)
    raise AdbError("screencap failed or returned only black frames")


def tap(x, y, serial=SERIAL):
    shell(f"input tap {int(x)} {int(y)}", serial=serial)


def swipe(x1, y1, x2, y2, ms=800, times=1, serial=SERIAL):
    """Slow drag, `times` times in one adb call. Fast swipes are ignored by
    the game's lists. Each `input` call costs ~3 s on this emulator."""
    cmd = "; ".join([f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(ms)}"] * times)
    shell(cmd, timeout=times * (ms / 1000 + 5) + 20, serial=serial)


def focused_window(serial=SERIAL):
    """The focused window line from dumpsys, e.g.
    'Window{2e43be6 u0 com.supercell.clashofclans/com.supercell.titan.GameApp}'."""
    out = shell("dumpsys window | grep -m1 mCurrentFocus", serial=serial)
    return out.split("=", 1)[1].strip() if "=" in out else out


def wait_for_focus(package=COC_PACKAGE, timeout=8, serial=SERIAL):
    """True once `package` owns the focused window. Focus is briefly null
    during window transitions, so one reading is not enough."""
    end = time.time() + timeout
    while True:
        if f"{package}/" in focused_window(serial):
            return True
        if time.time() >= end:
            return False
        time.sleep(1)


_ANR_WAIT_POS = (405, 515)     # top-left of misc/anr_wait.png in the dialog
_ANR_WAIT_TAP = (123, 28)      # "Wait", relative to that


def dismiss_systemui_anr(serial=SERIAL):
    """If Android shows "System UI isn't responding", press Wait. Returns
    True if it did.

    Not "Close app" (or kill): on LDPlayer a freshly started System UI is
    slow to initialise (LDPlayer's system_server permission checks), misses
    the next per-minute clock broadcast and stalls again, so restarting it
    keeps the dialog coming back. Waiting lets it finish starting."""
    if "Application Not Responding: com.android.systemui" not in focused_window(serial):
        return False
    import cv2
    tpl = cv2.imread(os.path.join(_MISC, "anr_wait.png"), cv2.IMREAD_COLOR)
    if tpl is None:
        raise AdbError("template misc/anr_wait.png missing")
    res = cv2.matchTemplate(screencap(serial), tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, (x, y) = cv2.minMaxLoc(res)
    if score < 0.9 or abs(x - _ANR_WAIT_POS[0]) > 10 or abs(y - _ANR_WAIT_POS[1]) > 10:
        raise AdbError(f"System UI ANR focused but its dialog was not found (score {score:.2f})")
    tap(x + _ANR_WAIT_TAP[0], y + _ANR_WAIT_TAP[1], serial)
    time.sleep(1)
    return True


def game_version(serial=SERIAL):
    """Installed Clash of Clans versionName, e.g. '18.647.5'."""
    out = shell(f"dumpsys package {COC_PACKAGE} | grep -m1 versionName", serial=serial)
    if "=" not in out:
        raise AdbError(f"could not read {COC_PACKAGE} version: {out!r}")
    return out.split("=", 1)[1].strip()
