"""Real Spotify Web API access via OAuth, used when SPOTIFY_CLIENT_ID/SECRET are set
in the environment (see README.md "Spotify Web API setup"). This is what makes
"play X on spotify" actually press play on a device, instead of just opening search
results in the desktop app (integrations.py falls back to that when this isn't
configured, so the assistant still works before you've registered a Spotify app).
"""

import os
from pathlib import Path
from typing import List, Optional

from assistant.logger import get_logger

log = get_logger(__name__)

SCOPES = "user-modify-playback-state user-read-playback-state user-read-currently-playing"
CACHE_PATH = Path(__file__).resolve().parent.parent / "config" / ".spotify_cache"

_client = None


def is_configured() -> bool:
    return bool(os.environ.get("SPOTIFY_CLIENT_ID") and os.environ.get("SPOTIFY_CLIENT_SECRET"))


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not is_configured():
        return None

    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    auth = SpotifyOAuth(
        client_id=os.environ["SPOTIFY_CLIENT_ID"],
        client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
        redirect_uri=os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
        scope=SCOPES,
        cache_path=str(CACHE_PATH),
        open_browser=True,
    )
    _client = spotipy.Spotify(auth_manager=auth)
    return _client


def _active_devices(sp) -> List[dict]:
    return sp.devices().get("devices", [])


def play(query: str) -> Optional[str]:
    """Searches for a track and starts playback on an active device.

    Returns a result message, or None if Spotify isn't configured -- callers should
    fall back to the URI-scheme search in that case.
    """
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
