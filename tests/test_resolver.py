"""Covers resolve_app_path()'s fuzzy-match guard -- added after a real false
positive: a wake word false-trigger recorded a snippet of background conversation,
Whisper transcribed it as "Open the password", and the fuzzy matcher resolved
"the password" to the "word" app (Microsoft Word) purely because "password"
contains "word" as a substring, opening Word with nobody asking for it."""

import unittest
from unittest.mock import patch

from assistant import resolver

CANDIDATES = {
    "word": "C:\\Program Files\\Microsoft Office\\root\\Office16\\WINWORD.EXE",
    "chrome": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "vscode": "C:\\Users\\me\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe",
    "explorer": "explorer.exe",
    "telegram": "telegram.exe",
    "notepad": "notepad.exe",
}


class TestResolveAppPath(unittest.TestCase):
    def setUp(self):
        patcher = patch("assistant.resolver._all_candidates", return_value=CANDIDATES)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rejects_app_name_buried_inside_an_unrelated_word(self):
        # The exact phrase that caused the false positive.
        self.assertIsNone(resolver.resolve_app_path("open the password"))
        self.assertIsNone(resolver.resolve_app_path("the password"))

    def test_rejects_other_words_that_merely_contain_an_app_name(self):
        self.assertIsNone(resolver.resolve_app_path("wordpress"))
        self.assertIsNone(resolver.resolve_app_path("password manager"))

    def test_still_matches_typo_of_app_name(self):
        self.assertEqual(resolver.resolve_app_path("crome")[0], "chrome")

    def test_still_matches_merged_word_alias(self):
        self.assertEqual(resolver.resolve_app_path("vs code")[0], "vscode")

    def test_still_matches_extra_words_around_exact_app_name(self):
        self.assertEqual(resolver.resolve_app_path("google chrome")[0], "chrome")
        self.assertEqual(resolver.resolve_app_path("microsoft word")[0], "word")
        self.assertEqual(resolver.resolve_app_path("telegram desktop")[0], "telegram")
        self.assertEqual(resolver.resolve_app_path("file explorer")[0], "explorer")

    def test_exact_match_still_short_circuits(self):
        self.assertEqual(resolver.resolve_app_path("word")[0], "word")


if __name__ == "__main__":
    unittest.main()
