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
    @patch("assistant.actions._hwnds_for_title", return_value=[])
    @patch("psutil.process_iter")
    def test_protected_process_refused_with_no_window_either(self, mock_iter, mock_hwnds):
        mock_iter.return_value = [FakeProcess(1, "explorer.exe")]
        with self.assertRaises(actions.ActionError) as ctx:
            actions.close_app("explorer", confirm=lambda p: True)
        self.assertIn("protected", str(ctx.exception).lower())

    @patch("assistant.actions.win32gui.PostMessage")
    @patch("assistant.actions._hwnds_for_title", return_value=[12345])
    @patch("psutil.process_iter")
    def test_protected_process_closes_matching_window_instead(self, mock_iter, mock_hwnds, mock_post):
        # explorer.exe is the one shared shell process AND the host for every
        # open Explorer *window* -- "close file explorer" should close the
        # window that's actually open, not just refuse because the shared
        # process itself is (correctly) protected from being killed.
        mock_iter.return_value = [FakeProcess(1, "explorer.exe")]
        result = actions.close_app("file explorer", confirm=lambda p: True)
        self.assertIn("Closed", result)
        mock_post.assert_called_once()

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

    @patch("assistant.actions._hwnds_for_title", return_value=[])
    @patch("psutil.process_iter")
    def test_no_match(self, mock_iter, mock_hwnds):
        mock_iter.return_value = [FakeProcess(4, "somethingelse.exe")]
        with self.assertRaises(actions.ActionError) as ctx:
            actions.close_app("nonexistentapp", confirm=lambda p: True)
        self.assertIn("No running process", str(ctx.exception))


class TestOpenAppSearchFallback(unittest.TestCase):
    """'open jumong' etc. -- a name that isn't a known/installed app should fall
    back to a folder/file search instead of just refusing, since a spoken "open
    <x>" can't be told apart from "open <folder/file x>" up front."""

    @patch("assistant.actions.os.startfile")
    @patch("assistant.actions.resolve_app_path", return_value=None)
    def test_falls_back_to_folder_search(self, mock_resolve, mock_startfile):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "jumong"
            nested.mkdir()
            with patch("assistant.actions._iter_search_roots", return_value=[Path(tmp)]):
                result = actions.open_app("jumong")
            self.assertIn("jumong", result)
            mock_startfile.assert_called_once()

    @patch("assistant.actions.os.startfile")
    @patch("assistant.actions.resolve_app_path", return_value=None)
    def test_falls_back_to_file_search_when_no_folder_matches(self, mock_resolve, mock_startfile):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "jumong.mp4").touch()
            with patch("assistant.actions._iter_search_roots", return_value=[Path(tmp)]):
                result = actions.open_app("jumong")
            self.assertIn("jumong.mp4", result)
            mock_startfile.assert_called_once()

    @patch("assistant.actions.resolve_app_path", return_value=None)
    def test_refused_when_nothing_matches_at_all(self, mock_resolve):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("assistant.actions._iter_search_roots", return_value=[Path(tmp)]):
                with self.assertRaises(actions.ActionError) as ctx:
                    actions.open_app("definitely-not-anything-xyz")
            self.assertIn("app, folder, or file", str(ctx.exception))


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


class TestFindFile(unittest.TestCase):
    def test_finds_file_nested_in_subfolder(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "projects" / "voiceassistant"
            nested.mkdir(parents=True)
            (nested / "actions.py").touch()
            result = actions._find_file("actions.py", location=tmp)
            self.assertEqual(result.name, "actions.py")

    def test_matches_by_name_ignoring_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "notes.txt").touch()
            result = actions._find_file("notes", location=tmp)
            self.assertEqual(result.name, "notes.txt")

    def test_prunes_excluded_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            excluded = Path(tmp) / "node_modules"
            excluded.mkdir()
            (excluded / "target.js").touch()
            result = actions._find_file("target.js", location=tmp)
            self.assertIsNone(result)

    def test_no_match_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = actions._find_file("nonexistentfile123", location=tmp)
            self.assertIsNone(result)


class TestFindFolder(unittest.TestCase):
    def test_finds_nested_folder_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "a" / "b" / "target_folder"
            nested.mkdir(parents=True)
            result = actions._find_folder("target_folder", location=tmp)
            self.assertEqual(result.name, "target_folder")


class TestOpenFolderSearchFallback(unittest.TestCase):
    @patch("assistant.actions.os.startfile")
    def test_falls_back_to_search_for_nonexistent_literal_path(self, mock_startfile):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "deep" / "myfolder"
            nested.mkdir(parents=True)
            with patch("assistant.actions._iter_search_roots", return_value=[Path(tmp)]):
                result = actions.open_folder("myfolder")
            self.assertIn("myfolder", result)
            mock_startfile.assert_called_once()

    @patch("assistant.actions.os.startfile")
    def test_resolves_named_location_directly(self, mock_startfile):
        result = actions.open_folder("downloads")
        expected = str(Path.home() / "Downloads")
        self.assertIn(expected, result)
        mock_startfile.assert_called_once_with(expected)

    def test_unresolvable_name_refused(self):
        with self.assertRaises(actions.ActionError):
            actions.open_folder("definitely-not-a-real-location-xyz-123")


class TestOpenVscodeSearchFallback(unittest.TestCase):
    @patch("assistant.actions.subprocess.Popen")
    @patch("assistant.actions.shutil.which", return_value="C:\\fake\\code.exe")
    def test_falls_back_to_search_for_nonexistent_literal_path(self, mock_which, mock_popen):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sub" / "myfile.py"
            target.parent.mkdir(parents=True)
            target.touch()
            with patch("assistant.actions._find_file", return_value=target):
                result = actions.open_vscode(path="myfile.py")
            self.assertIn(str(target), result)
            args = mock_popen.call_args[0][0]
            self.assertIn(str(target), args)


class TestVideoTitleExtraction(unittest.TestCase):
    def test_cleans_scene_release_junk(self):
        self.assertEqual(actions._clean_title("Gotham.S01E01.1080p.WEB.x264-GROUP"), "Gotham")
        self.assertEqual(
            actions._clean_title("The.Batman.2022.1080p.BluRay.x264-SPARKS"), "The Batman"
        )
        self.assertEqual(
            actions._clean_title("Interstellar (2014) [1080p] BluRay x265 HEVC"), "Interstellar"
        )

    def test_parses_spoken_season_episode(self):
        self.assertEqual(actions._parse_spoken_episode("gotham season 1 episode 1"), ("gotham", 1, 1))
        self.assertEqual(
            actions._parse_spoken_episode("gotham season one episode one"), ("gotham", 1, 1)
        )
        self.assertEqual(actions._parse_spoken_episode("breaking bad s5e14"), ("breaking bad", 5, 14))

    def test_plain_title_is_not_an_episode_request(self):
        self.assertIsNone(actions._parse_spoken_episode("the batman"))

    def test_find_video_file_matches_by_season_episode_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Gotham.S01E01.1080p.WEB.x264-GROUP.mkv").touch()
            (Path(tmp) / "Gotham.S01E02.1080p.WEB.x264-GROUP.mkv").touch()
            result = actions._find_video_file("gotham season 1 episode 2", location=tmp)
            self.assertEqual(result.name, "Gotham.S01E02.1080p.WEB.x264-GROUP.mkv")

    def test_find_video_file_matches_movie_title_fuzzily(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "The.Batman.2022.1080p.BluRay.x264-SPARKS.mkv").touch()
            result = actions._find_video_file("the batman", location=tmp)
            self.assertEqual(result.name, "The.Batman.2022.1080p.BluRay.x264-SPARKS.mkv")


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
