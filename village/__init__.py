"""Village state for the self-managing bot: static game data, the in-game data
export, and the snapshot store. See docs/automation_roadmap.md (Phase 2).

Nothing in this package drives the game except `capture`; everything else is
pure Python and covered by tests/.
"""
import os
import subprocess

# The service runs under pythonw.exe (no console). Windows then gives every
# console program it starts (adb.exe, ldconsole.exe, tasklist, tesseract ...)
# its own new console window: live 2026-11-17 adb.exe windows kept popping up
# over the desktop. Start child processes without a window unless the caller
# asked for something specific. Applies to everything imported afterwards,
# including Ranked_Copy and wall.py during farming runs.
if os.name == "nt" and not getattr(subprocess.Popen, "_village_no_window", False):
    _popen_init = subprocess.Popen.__init__

    def _no_window_init(self, *args, **kwargs):
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        _popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _no_window_init
    subprocess.Popen._village_no_window = True
