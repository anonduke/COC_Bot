"""Bring a window to the foreground from a background process.

Windows ignores SetForegroundWindow from a process that hasn't received
input recently (the "foreground lock"); live, every dashboard-started farming
attack failed with "LDPlayer could not be brought to the front" while VS Code
stayed in front. A synthetic ALT key press counts as input and lifts the lock
for the following SetForegroundWindow call.
"""
import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]

VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
SW_RESTORE = 9
SW_SHOW = 5


def foreground_hwnd():
    return user32.GetForegroundWindow()


def window_title(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(wintypes.HWND(hwnd), buf, 256)
    return buf.value


def bring_to_front(hwnd, tries=3):
    """True once `hwnd` is the foreground window."""
    hwnd = wintypes.HWND(hwnd)
    for _ in range(tries):
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        else:
            user32.ShowWindow(hwnd, SW_SHOW)
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        time.sleep(0.3)
        if user32.GetForegroundWindow() == hwnd.value:
            return True
    return False


def find_ldplayer():
    """hwnd of the LDPlayer main window, or None."""
    return user32.FindWindowW("LDPlayerMainFrame", None) or None


# The game window must fit on screen at its calibrated place and size, and
# pyautogui's coordinates must be real pixels (100 % display scaling).
MIN_SCREEN = (1282, 718)      # window at (84, 10), 1198 x 708


def desktop_problem():
    """Why mouse-driven attacks can't run right now, or None if they can.
    Checks: the desktop is drawn (not locked, Remote Desktop not minimized
    or disconnected), the screen is big enough, display scaling is 100 %.
    Remote Desktop sets the resolution from the connecting device, so it
    can change between connections."""
    import pyautogui
    try:
        pyautogui.screenshot(region=(0, 0, 8, 8))
    except Exception:  # noqa: BLE001
        return ("the Windows desktop is not visible (Remote Desktop minimized or "
                "disconnected, or the PC is locked)")
    w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    if w < MIN_SCREEN[0] or h < MIN_SCREEN[1]:
        return (f"the screen is {w}x{h}; the game window needs at least "
                f"{MIN_SCREEN[0]}x{MIN_SCREEN[1]} (Remote Desktop: connect at 1366x768 or larger)")
    dc = user32.GetDC(0)
    try:
        dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)     # LOGPIXELSX
    finally:
        user32.ReleaseDC(0, dc)
    if dpi != 96:
        return (f"display scaling is {round(dpi / 96 * 100)} %; the attack code needs 100 % "
                "(Settings > Display > Scale)")
    return None


def desktop_usable():
    return desktop_problem() is None
