"""Deep, app-specific actions for apps that support more than a plain "launch me":
telling Chrome specifically (not whatever the default browser is) to open a URL or
search, telling Spotify to jump to a search, and opening/searching Gmail. These are
one level past actions.py's generic open/close -- each knows a trick specific to one
app (a URI scheme, a CLI flag, a URL format) rather than just starting a process.

Playback control on Spotify (actually pressing play, not just opening search results)
and reading/composing mail without a browser both need OAuth against a real API
(Spotify Web API, Gmail API) -- a good Phase 4 addition once there's an LLM backend
to hold credentials and make those calls. What's here is the no-auth-required slice
of each: takes you straight to the right place, one click from done.
"""

import os
import shutil
import subprocess
import urllib.parse
import webbrowser
from typing import Optional

from assistant.app_discovery import discover_apps
from assistant.logger import get_logger

log = get_logger(__name__)

CHROME_FALLBACK_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def _find_chrome() -> Optional[str]:
    apps = discover_apps()
    for key in ("google chrome", "chrome"):
        if key in apps and os.path.exists(apps[key]):
            return apps[key]
    for p in CHROME_FALLBACK_PATHS:
        if os.path.exists(p):
            return p
    return shutil.which("chrome")


def _looks_like_url(text: str) -> bool:
    text = text.strip()
    return "." in text and " " not in text and not text.lower().startswith("search ")


def open_in_chrome(query_or_url: str) -> str:
    """'open <x> in chrome' -- opens a URL directly, or a Google search if it doesn't
    look like one. Launches Chrome specifically via its binary rather than
    webbrowser.open(), which would just hand the URL to whatever the OS default is."""
    target = query_or_url.strip()
    if _looks_like_url(target):
        url = target if target.startswith(("http://", "https://")) else f"https://{target}"
    else:
        url = f"https://www.google.com/search?q={urllib.parse.quote(target)}"

    chrome = _find_chrome()
    if not chrome:
        webbrowser.open(url)
        msg = f"Chrome wasn't found; opened {url} in the default browser instead."
        log.warning(msg)
        return msg

    subprocess.Popen([chrome, url])
    msg = f"Opened {url} in Chrome."
    log.info(msg)
    return msg


def spotify_search(query: str) -> str:
    """'play <x> on spotify' -- if SPOTIFY_CLIENT_ID/SECRET are configured (see
    README.md "Spotify Web API setup"), actually starts playback via the Spotify Web
    API. Otherwise falls back to opening the desktop app straight to search results
    via its registered spotify: URI scheme -- no auth needed, but you have to press
    play yourself."""
    from assistant import spotify_client

    result = spotify_client.play(query)
    if result is not None:
        return result

    uri = f"spotify:search:{urllib.parse.quote(query)}"
    try:
        os.startfile(uri)
        msg = (
            f"Opened Spotify search for '{query}' -- press play on the top result. "
            "(Set up Spotify Web API credentials to skip this step -- see README.md.)"
        )
        log.info(msg)
        return msg
    except OSError as e:
        msg = f"Couldn't open Spotify (is the desktop app installed?): {e}"
        log.warning(msg)
        return msg


GMAIL_BASE = "https://mail.google.com/mail/u/0/"


def open_gmail(query: Optional[str] = None) -> str:
    """'open mail' / 'search mail for <x>' -- if config/gmail_credentials.json is set
    up (see README.md "Gmail API setup"), a search query returns actual matching
    message summaries via the Gmail API. Otherwise (or for a bare "open mail") it
    falls back to opening Gmail in the browser, riding your existing logged-in
    session there instead of handling credentials itself."""
    if query:
        from assistant import gmail_client

        results = gmail_client.search(query)
        if results is not None:
            if not results:
                msg = f"No mail matching '{query}'."
            else:
                msg = f"{len(results)} result(s) for '{query}':\n" + "\n".join(
                    f"  - {r}" for r in results
                )
            log.info(msg)
            return msg

        url = GMAIL_BASE + "#search/" + urllib.parse.quote(query)
        msg = f"Opened Gmail search for '{query}'. (Set up Gmail API credentials to see results here instead -- see README.md.)"
    else:
        url = GMAIL_BASE + "#inbox"
        msg = "Opened Gmail inbox."

    webbrowser.open(url)
    log.info(msg)
    return msg
