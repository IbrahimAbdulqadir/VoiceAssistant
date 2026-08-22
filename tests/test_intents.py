"""Verifies text routes to the right action -- without actually opening/closing
anything, since actions.* is patched out."""

import unittest
from unittest.mock import patch

from assistant.executor import execute


class TestIntentRouting(unittest.TestCase):
    @patch("assistant.executor.actions.open_app", return_value="ok")
    def test_open_app_catchall(self, mock_open):
        execute("open chrome")
        mock_open.assert_called_once_with("chrome")

    @patch("assistant.executor.actions.open_vscode", return_value="ok")
    def test_open_vscode_with_path(self, mock_vscode):
        execute("open vscode in C:/projects/foo")
        mock_vscode.assert_called_once_with(path="C:/projects/foo")

    @patch("assistant.executor.actions.open_vscode", return_value="ok")
    def test_open_vscode_bare(self, mock_vscode):
        execute("open vscode")
        mock_vscode.assert_called_once_with()

    @patch("assistant.executor.actions.close_app", return_value="ok")
    def test_close_app(self, mock_close):
        execute("close spotify")
        mock_close.assert_called_once_with("spotify")

    @patch("assistant.executor.actions.open_folder", return_value="ok")
    def test_open_folder_beats_catchall(self, mock_folder):
        execute("open folder C:/Users/me/Documents")
        mock_folder.assert_called_once_with("C:/Users/me/Documents")

    @patch("assistant.executor.actions.open_url", return_value="ok")
    def test_open_url_beats_catchall(self, mock_url):
        execute("open website github.com")
        mock_url.assert_called_once_with("github.com")

    @patch("assistant.executor.actions.run_script", return_value="ok")
    def test_run_script(self, mock_run):
        execute("run backup")
        mock_run.assert_called_once_with("backup")

    @patch("assistant.executor.integrations.open_in_chrome", return_value="ok")
    def test_open_in_chrome_beats_catchall(self, mock_chrome):
        execute("open github.com in chrome")
        mock_chrome.assert_called_once_with("github.com")

    @patch("assistant.executor.integrations.spotify_search", return_value="ok")
    def test_play_on_spotify(self, mock_spotify):
        execute("play daft punk on spotify")
        mock_spotify.assert_called_once_with("daft punk")

    @patch("assistant.executor.integrations.open_gmail", return_value="ok")
    def test_open_mail(self, mock_mail):
        execute("open mail")
        mock_mail.assert_called_once_with()

    @patch("assistant.executor.integrations.open_gmail", return_value="ok")
    def test_search_mail(self, mock_mail):
        execute("search mail for invoice")
        mock_mail.assert_called_once_with("invoice")

    @patch("assistant.executor.llm_backend.handle", return_value=None)
    def test_unmatched_falls_through_when_llm_unconfigured(self, mock_llm):
        result = execute("do a barrel roll")
        self.assertIn("Didn't understand", result)

    @patch("assistant.executor.llm_backend.handle", return_value="did the barrel roll thing")
    def test_unmatched_routes_to_llm_backend(self, mock_llm):
        result = execute("do a barrel roll")
        mock_llm.assert_called_once_with("do a barrel roll")
        self.assertEqual(result, "did the barrel roll thing")

    def test_help(self):
        result = execute("help")
        self.assertIn("Commands:", result)


if __name__ == "__main__":
    unittest.main()
