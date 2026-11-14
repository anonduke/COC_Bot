"""Keep Windows' display on while the bot works.

With the display off the Intel GPU stops presenting the emulator's frames
and Android's graphics eventually hang (Phase 0 findings). The request lasts
as long as the calling thread, or until released.
"""
import ctypes
from contextlib import contextmanager

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


@contextmanager
def keep_display_on():
    kernel32 = ctypes.windll.kernel32
    kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
    try:
        yield
    finally:
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)
