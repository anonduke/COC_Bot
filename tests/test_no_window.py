import os
import subprocess
import unittest

import village  # noqa: F401  (installs the no-window default)


@unittest.skipUnless(os.name == "nt", "Windows only")
class NoWindowTest(unittest.TestCase):
    def test_child_processes_get_no_console_window(self):
        seen = {}
        orig = subprocess.Popen._execute_child

        import inspect
        pos = list(inspect.signature(orig).parameters).index("creationflags")

        def spy(self, *rest):
            seen["flags"] = rest[pos - 1]
            return orig(self, *rest)

        subprocess.Popen._execute_child = spy
        try:
            subprocess.run(["cmd", "/c", "exit 0"], capture_output=True)
        finally:
            subprocess.Popen._execute_child = orig
        self.assertTrue(seen["flags"] & subprocess.CREATE_NO_WINDOW)

    def test_explicit_flags_are_kept(self):
        p = subprocess.Popen(["cmd", "/c", "exit 0"], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        p.wait()
        self.assertEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main()
