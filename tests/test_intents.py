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

    @patch("assistant.executor.actions.close_app")
    @patch("assistant.executor.actions.close_all_apps", return_value="ok")
    def test_close_all_beats_close_app_catchall(self, mock_close_all, mock_close_app):
        for phrase in ["close all", "close everything", "close all apps", "close all windows"]:
            mock_close_all.reset_mock()
            execute(phrase)
            mock_close_all.assert_called_once_with()
            mock_close_app.assert_not_called()

    @patch("assistant.executor.actions.minimize_app")
    @patch("assistant.executor.actions.minimize_all_windows", return_value="ok")
    def test_minimize_all_beats_minimize_app_catchall(self, mock_minimize_all, mock_minimize_app):
        for phrase in ["minimize all", "minimize everything", "minimise all windows"]:
            mock_minimize_all.reset_mock()
            execute(phrase)
            mock_minimize_all.assert_called_once_with()
            mock_minimize_app.assert_not_called()

    @patch("assistant.executor.actions.open_folder", return_value="ok")
    def test_open_folder_beats_catchall(self, mock_folder):
        execute("open folder C:/Users/me/Documents")
        mock_folder.assert_called_once_with("C:/Users/me/Documents")

    @patch("assistant.executor.actions.open_app", return_value="ok")
    @patch("assistant.executor.actions.open_folder", return_value="ok")
    def test_open_in_file_explorer_forces_folder_over_app(self, mock_folder, mock_open):
        # Explicit override for the app-vs-folder ambiguity -- saying "in file
        # explorer" should always win, regardless of whether "telegram" would
        # otherwise resolve to the app (doesn't even need discover_apps
        # mocked here: this must never reach that check at all).
        execute("open telegram in file explorer")
        mock_folder.assert_called_once_with("telegram")
        mock_open.assert_not_called()

    @patch("assistant.executor.actions.open_app", return_value="ok")
    @patch("assistant.executor.actions.open_folder", return_value="ok")
    def test_open_in_explorer_short_form(self, mock_folder, mock_open):
        execute("open jumong in explorer")
        mock_folder.assert_called_once_with("jumong")
        mock_open.assert_not_called()

    @patch("assistant.executor.discover_apps", return_value={})
    @patch("assistant.executor.actions.open_folder", return_value="ok")
    def test_open_named_location_beats_catchall(self, mock_folder, mock_discover):
        execute("open downloads")
        mock_folder.assert_called_once_with("downloads")

    @patch("assistant.executor.discover_apps", return_value={})
    @patch("assistant.executor.actions.open_folder", return_value="ok")
    def test_open_named_location_with_folder_word(self, mock_folder, mock_discover):
        execute("open the desktop folder")
        mock_folder.assert_called_once_with("desktop")

    @patch("assistant.executor.discover_apps", return_value={})
    @patch("assistant.executor.actions.open_folder", return_value="ok")
    def test_open_multiword_named_location(self, mock_folder, mock_discover):
        execute("open telegram desktop")
        mock_folder.assert_called_once_with("telegram desktop")

    @patch("assistant.executor.actions.open_app", return_value="ok")
    def test_open_app_still_wins_for_non_location_names(self, mock_open):
        execute("open spotify")
        mock_open.assert_called_once_with("spotify")

    @patch("assistant.executor.discover_apps", return_value={"telegram": "C:/Telegram.exe"})
    @patch("assistant.executor.actions.open_app", return_value="ok")
    @patch("assistant.executor.actions.open_folder", return_value="ok")
    def test_named_location_that_is_also_an_app_opens_the_app(self, mock_folder, mock_open, mock_discover):
        # "telegram" is both a _NAMED_LOCATIONS alias (Telegram Desktop's
        # download folder) and a real installed app -- bare "open telegram"
        # should launch the app, not open the download folder.
        execute("open telegram")
        mock_open.assert_called_once_with("telegram")
        mock_folder.assert_not_called()

    @patch("assistant.executor.actions.open_app", return_value="ok")
    @patch("assistant.executor.actions.open_folder", return_value="ok")
    def test_named_location_alias_only_in_apps_yaml_still_opens_folder(self, mock_folder, mock_open):
        # "downloads"/"documents" are only resolvable as apps via the legacy
        # config/apps.yaml `explorer.exe shell:...` alias, not a real
        # discovered application -- that alias must not hijack this back to
        # open_app (deliberately runs against the real discover_apps() here,
        # not a mocked-empty one, since the whole point is that a real
        # apps.yaml alias for "downloads" exists and still must not match).
        execute("open downloads")
        mock_folder.assert_called_once_with("downloads")
        mock_open.assert_not_called()

    @patch("assistant.executor.actions.open_url", return_value="ok")
    def test_open_url_beats_catchall(self, mock_url):
        execute("open website github.com")
        mock_url.assert_called_once_with("github.com")

    @patch("assistant.executor.actions.run_script", return_value="ok")
    def test_run_script(self, mock_run):
        execute("run backup")
        mock_run.assert_called_once_with("backup")

    @patch("assistant.executor.actions.shutdown_system", return_value="ok")
    def test_shutdown_bare(self, mock_shutdown):
        execute("shut down")
        mock_shutdown.assert_called_once_with()

    @patch("assistant.executor.actions.shutdown_system", return_value="ok")
    def test_shutdown_with_target_phrasing(self, mock_shutdown):
        for phrase in ["shutdown the computer", "shut down my laptop", "shut down system", "power off"]:
            mock_shutdown.reset_mock()
            execute(phrase)
            mock_shutdown.assert_called_once_with()

    @patch("assistant.executor.actions.restart_system", return_value="ok")
    def test_restart(self, mock_restart):
        execute("restart the pc")
        mock_restart.assert_called_once_with()

    @patch("assistant.executor.actions.hibernate_system", return_value="ok")
    def test_hibernate(self, mock_hibernate):
        execute("hibernate")
        mock_hibernate.assert_called_once_with()

    @patch("assistant.executor.actions.cancel_shutdown", return_value="ok")
    def test_cancel_shutdown(self, mock_cancel):
        execute("cancel shutdown")
        mock_cancel.assert_called_once_with()

    @patch("assistant.executor.actions.cancel_shutdown", return_value="ok")
    def test_abort_restart(self, mock_cancel):
        execute("abort restart")
        mock_cancel.assert_called_once_with()

    @patch("assistant.executor.actions.lock_screen", return_value="ok")
    def test_lock_screen(self, mock_lock):
        execute("lock my computer")
        mock_lock.assert_called_once_with()

    @patch("assistant.executor.actions.screen_off", return_value="ok")
    def test_screen_off(self, mock_screen_off):
        for phrase in ["turn off the screen", "screen off", "turn off display", "off screen", "off the screen"]:
            mock_screen_off.reset_mock()
            execute(phrase)
            mock_screen_off.assert_called_once_with()

    @patch("assistant.executor.actions.shutdown_system")
    @patch("assistant.executor.llm_backend.handle", return_value=None)
    def test_shutdown_mention_in_a_sentence_does_not_trigger_it(self, mock_llm, mock_shutdown):
        # Power actions must only ever be reachable through the tight regexes
        # above, not matched loosely -- a sentence that merely contains "shut
        # down" (not a bare/exact command) must fall straight through to the
        # LLM fallback instead of resolving to a real power action.
        execute("I was thinking we should shut down that old project soon")
        mock_llm.assert_called_once()
        mock_shutdown.assert_not_called()

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
