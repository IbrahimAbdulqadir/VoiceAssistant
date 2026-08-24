"""Safety-rail tests: protected processes must never be closeable, unlisted scripts
must never be runnable, and close_app must never proceed without confirmation."""

import tempfile
import unittest
from pathlib import Path
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
        with self.assertRaises(actions.ActionError) as ctx:
            actions.close_app("explorer", confirm=lambda p: True)
        self.assertIn("protected", str(ctx.exception).lower())

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
        with self.assertRaises(actions.ActionError) as ctx:
            actions.close_app("nonexistentapp", confirm=lambda p: True)
        self.assertIn("No running process", str(ctx.exception))


class TestRunScript(unittest.TestCase):
    def test_unwhitelisted_script_refused(self):
        with self.assertRaises(actions.ActionError) as ctx:
            actions.run_script("not-in-whitelist", confirm=lambda p: True)
        self.assertIn("whitelisted", str(ctx.exception).lower())


class TestCreateFile(unittest.TestCase):
    def test_defaults_to_txt_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = actions.create_file(tmp, "notes")
            self.assertTrue((Path(tmp) / "notes.txt").exists())
            self.assertIn("notes.txt", result)

    def test_keeps_given_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions.create_file(tmp, "script.py")
            self.assertTrue((Path(tmp) / "script.py").exists())

    def test_unknown_location_refused(self):
        with self.assertRaises(actions.ActionError):
            actions.create_file("not-a-real-location-xyz", "notes")


class TestPlayVideo(unittest.TestCase):
    @patch("assistant.actions.resolve_app_path")
    def test_no_matching_video_refused(self, mock_resolve):
        mock_resolve.return_value = ("vlc", "C:\\fake\\vlc.exe")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(actions.ActionError) as ctx:
                actions.play_video("nonexistentvideo", location=tmp)
            self.assertIn("Couldn't find a video", str(ctx.exception))

    @patch("assistant.actions.subprocess.Popen")
    @patch("assistant.actions.resolve_app_path")
    def test_finds_and_plays_matching_video(self, mock_resolve, mock_popen):
        mock_resolve.return_value = ("vlc", "C:\\fake\\vlc.exe")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "spiderman.mp4").touch()
            result = actions.play_video("spiderman", location=tmp)
            self.assertIn("spiderman.mp4", result)
            mock_popen.assert_called_once()

    def test_vlc_not_found_refused(self):
        with patch("assistant.actions.resolve_app_path", return_value=None):
            with self.assertRaises(actions.ActionError) as ctx:
                actions.play_video("spiderman")
            self.assertIn("VLC", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
