"""Covers the first-time-sign-in path in spotify_client.play() -- the case that
was silently hanging a voice command forever (no cached token, spotipy's
get_access_token() blocking indefinitely on the local OAuth callback server with
no feedback and no way to retry)."""

import unittest
from unittest.mock import MagicMock, patch

from assistant import spotify_client


class TestSpotifyFirstTimeSignIn(unittest.TestCase):
    def setUp(self):
        # Module-level state that play() reads/mutates -- reset around every
        # test so they don't leak into each other.
        spotify_client._client = None
        spotify_client._auth_manager = None
        spotify_client._auth_in_progress = False

    def tearDown(self):
        spotify_client._client = None
        spotify_client._auth_manager = None
        spotify_client._auth_in_progress = False

    def _fake_auth(self, cached_token=None):
        auth = MagicMock()
        auth.cache_handler.get_cached_token.return_value = cached_token
        return auth

    @patch("assistant.spotify_client.is_configured", return_value=True)
    @patch("assistant.spotify_client.threading.Thread")
    def test_no_cached_token_returns_immediately_and_backgrounds_auth(self, mock_thread, _configured):
        with patch.object(spotify_client, "_get_auth_manager", return_value=self._fake_auth(None)):
            result = spotify_client.play("some song")

        self.assertIn("one-time sign-in", result)
        mock_thread.assert_called_once()
        mock_thread.return_value.start.assert_called_once()
        self.assertTrue(spotify_client._auth_in_progress)

    @patch("assistant.spotify_client.is_configured", return_value=True)
    @patch("assistant.spotify_client.threading.Thread")
    def test_second_call_while_signing_in_does_not_start_another_thread(self, mock_thread, _configured):
        spotify_client._auth_in_progress = True
        with patch.object(spotify_client, "_get_auth_manager", return_value=self._fake_auth(None)):
            result = spotify_client.play("some song")

        self.assertIn("Still waiting", result)
        mock_thread.assert_not_called()

    @patch("assistant.spotify_client.is_configured", return_value=True)
    def test_cached_token_present_skips_browser_and_searches(self, _configured):
        sp = MagicMock()
        sp.search.return_value = {"tracks": {"items": []}}
        with patch.object(spotify_client, "_get_auth_manager", return_value=self._fake_auth({"access_token": "x"})), \
             patch.object(spotify_client, "_get_client", return_value=sp):
            result = spotify_client.play("some song")

        sp.search.assert_called_once()
        self.assertIn("No Spotify track found", result)

    def test_not_configured_returns_none(self):
        with patch("assistant.spotify_client.is_configured", return_value=False):
            self.assertIsNone(spotify_client.play("some song"))


if __name__ == "__main__":
    unittest.main()
