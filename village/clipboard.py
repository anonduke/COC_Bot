"""Windows clipboard text via the Win32 API (tkinter is unreliable here: while
it owns the clipboard it returns its own copy instead of what LDPlayer put
there)."""
import ctypes
import time
from ctypes import wintypes

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_user32.SetClipboardData.restype = wintypes.HANDLE
_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = wintypes.LPVOID
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]


class _Open:
    """Open the clipboard, retrying briefly: another process (LDPlayer's sync,
    the user) may hold it for a moment."""

    def __enter__(self):
        for _ in range(50):
            if _user32.OpenClipboard(None):
                return self
            time.sleep(0.02)
        raise OSError("clipboard is busy")

    def __exit__(self, *exc):
        _user32.CloseClipboard()


def get_text():
    """Clipboard text, or None if it holds no text."""
    with _Open():
        h = _user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = _kernel32.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.wstring_at(p)
        finally:
            _kernel32.GlobalUnlock(h)


def set_text(text):
    """Replace the clipboard with `text`; None empties it."""
    with _Open():
        _user32.EmptyClipboard()
        if text is None:
            return
        data = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(data)
        h = _kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not h:
            raise OSError("GlobalAlloc failed")
        p = _kernel32.GlobalLock(h)
        if not p:
            _kernel32.GlobalFree(h)
            raise OSError("GlobalLock failed")
        ctypes.memmove(p, data, size)
        _kernel32.GlobalUnlock(h)
        if not _user32.SetClipboardData(CF_UNICODETEXT, h):
            _kernel32.GlobalFree(h)  # ownership only passes on success
            raise OSError("SetClipboardData failed")
