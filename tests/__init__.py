"""Tests never share the game lock with a bot that is running on this PC."""
import os
import tempfile

from village import capture

capture.LOCK_PATH = os.path.join(tempfile.mkdtemp(prefix="village-tests-"), "capture.lock")
