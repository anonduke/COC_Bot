"""Keep Windows' display on while the bot works.

With the display off the Intel GPU stops presenting the emulator's frames
and Android's graphics eventually hang (Phase 0 findings). The request is
per thread and nests: a session holds it while a farming run inside it does
too, and only the outermost exit releases it.
"""
import ctypes
import threading
from contextlib import contextmanager

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
_depth = threading.local()


@contextmanager
def keep_display_on():
    kernel32 = ctypes.windll.kernel32
    depth = getattr(_depth, "n", 0)
    if depth == 0:
        kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
    _depth.n = depth + 1
    try:
        yield
    finally:
        _depth.n -= 1
        if _depth.n == 0:
            kernel32.SetThreadExecutionState(ES_CONTINUOUS)
