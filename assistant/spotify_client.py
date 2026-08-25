"""Real Spotify Web API access via OAuth, used when SPOTIFY_CLIENT_ID/SECRET are set
in the environment (see README.md "Spotify Web API setup"). This is what makes
"play X on spotify" actually press play on a device, instead of just opening search
results in the desktop app (integrations.py falls back to that when this isn't
configured, so the assistant still works before you've registered a Spotify app).
"""

import os
import threading
from pathlib import Path
from typing import List, Optional

from assistant.logger import get_logger

log = get_logger(__name__)

SCOPES = "user-modify-playback-state user-read-playback-state user-read-currently-playing"
CACHE_PATH = Path(__file__).resolve().parent.parent / "config" / ".spotify_cache"

_client = None
_auth_manager = None

# Guards the first-time sign-in flow below -- see _authorize_in_background.
_auth_thread_lock = threading.Lock()
_auth_in_progress = False


def is_configured() -> bool:
    return bool(os.environ.get("SPOTIFY_CLIENT_ID") and os.environ.get("SPOTIFY_CLIENT_SECRET"))


def _get_auth_manager():
    global _auth_manager
    if _auth_manager is None:
        from spotipy.oauth2 import SpotifyOAuth

        _auth_manager = SpotifyOAuth(
            client_id=os.environ["SPOTIFY_CLIENT_ID"],
            client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
            redirect_uri=os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
            scope=SCOPES,
            cache_path=str(CACHE_PATH),
            open_browser=True,
        )
    return _auth_manager


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not is_configured():
        return None

    import spotipy

    _client = spotipy.Spotify(auth_manager=_get_auth_manager())
    return _client


def _authorize_in_background(auth) -> None:
    """First-time Spotify sign-in has to happen in a real browser, and spotipy's
    get_access_token() blocks the calling thread until that browser flow redirects
    back to a local callback server -- with no timeout. Doing that inline in play()
    left a voice command hanging silently forever (confirmed live: a "play X on
    spotify" command with no cached token yet left its command thread blocked on
    the local server for minutes, with no spoken feedback at all and no way to
    retry, since the local server was still holding the callback port). Running it
    here instead lets play() return an immediate spoken explanation; once this
    finishes, the token is cached to disk and every later play() call goes
    straight through without touching the browser again."""
    global _auth_in_progress
    try:
        auth.get_access_token(as_dict=False)
        log.info("Spotify sign-in completed; cached for future commands.")
    except Exception as e:
        log.warning("Spotify sign-in didn't complete: %s", e)
    finally:
        with _auth_thread_lock:
            _auth_in_progress = False


def _active_devices(sp) -> List[dict]:
    return sp.devices().get("devices", [])


def play(query: str) -> Optional[str]:
    """Searches for a track and starts playback on an active device.

    Returns a result message, or None if Spotify isn't configured -- callers should
    fall back to the URI-scheme search in that case.
    """
    if not is_configured():
        return None

    global _auth_in_progress
    auth = _get_auth_manager()
    if auth.cache_handler.get_cached_token() is None:
        # No token on disk yet -- this account has never signed in (or the cache
        # file was deleted). Hand off to the browser instead of blocking this
        # command on it; see _authorize_in_background for why.
        with _auth_thread_lock:
            already_running = _auth_in_progress
            _auth_in_progress = True
        if already_running:
            msg = "Still waiting on that Spotify sign-in -- finish it in the browser tab, then ask me again."
        else:
            threading.Thread(
                target=_authorize_in_background, args=(auth,), daemon=True, name="spotify-oauth"
            ).start()
            msg = "Spotify needs a one-time sign-in -- I opened it in Chrome. Sign in there, then ask me to play it again."
        log.info(msg)
        return msg

    try:
        sp = _get_client()
    except Exception as e:
        log.error("Spotify auth failed: %s", e)
        return f"Spotify auth failed: {e}"

    if sp is None:
        return None

    results = sp.search(q=query, type="track", limit=1)
    tracks = results.get("tracks", {}).get("items", [])
    if not tracks:
        msg = f"No Spotify track found for '{query}'."
        log.info(msg)
        return msg

    track = tracks[0]
    uri = track["uri"]
    name = track["name"]
    artist = track["artists"][0]["name"] if track["artists"] else "unknown artist"

    devices = _active_devices(sp)
    if not devices:
        msg = f"Found '{name}' by {artist}, but no active Spotify device -- open Spotify on this PC or phone first."
        log.warning(msg)
        return msg

    device_id = next((d["id"] for d in devices if d.get("is_active")), devices[0]["id"])
    sp.start_playback(device_id=device_id, uris=[uri])
    msg = f"Playing '{name}' by {artist}."
    log.info(msg)
    return msg
