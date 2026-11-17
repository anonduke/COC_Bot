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
