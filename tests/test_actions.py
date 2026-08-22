"""Safety-rail tests: protected processes must never be closeable, unlisted scripts
must never be runnable, and close_app must never proceed without confirmation."""

import unittest
from unittest.mock import patch

from assistant import actions


class FakeProcess:
    def __init__(self, pid, name):
        self.pid = pid
        self.info = {"pid": pid, "name": name}
        self.terminated = False

    def terminate(self):
        self.terminated = True


class TestCloseApp(unittest.TestCase):
    @patch("psutil.process_iter")
    def test_protected_process_refused(self, mock_iter):
        mock_iter.return_value = [FakeProcess(1, "explorer.exe")]
        result = actions.close_app("explorer", confirm=lambda p: True)
        self.assertIn("protected", result.lower())

    @patch("psutil.process_iter")
    def test_confirmation_declined_does_not_kill(self, mock_iter):
        proc = FakeProcess(2, "notepad.exe")
        mock_iter.return_value = [proc]
        result = actions.close_app("notepad", confirm=lambda p: False)
        self.assertEqual(result, "Cancelled.")
        self.assertFalse(proc.terminated)

    @patch("psutil.process_iter")
    def test_confirmation_accepted_kills(self, mock_iter):
        proc = FakeProcess(3, "notepad.exe")
        mock_iter.return_value = [proc]
        result = actions.close_app("notepad", confirm=lambda p: True)
        self.assertTrue(proc.terminated)
        self.assertIn("Closed", result)

    @patch("psutil.process_iter")
    def test_no_match(self, mock_iter):
        mock_iter.return_value = [FakeProcess(4, "somethingelse.exe")]
        result = actions.close_app("nonexistentapp", confirm=lambda p: True)
        self.assertIn("No running process", result)


class TestRunScript(unittest.TestCase):
    def test_unwhitelisted_script_refused(self):
        result = actions.run_script("not-in-whitelist", confirm=lambda p: True)
        self.assertIn("whitelisted", result.lower())


if __name__ == "__main__":
    unittest.main()
