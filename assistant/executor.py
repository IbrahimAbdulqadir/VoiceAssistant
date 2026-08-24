"""Registers every supported intent pattern and exposes execute(text) as the single
entry point. This is the thing Phase 2 (wake word + Whisper) feeds transcribed text
into, unchanged -- it doesn't know or care whether the text was typed or spoken.
"""

import re
from enum import Enum
from typing import Optional, Tuple

from assistant import actions, integrations, llm_backend
from assistant.intents import intent, match
from assistant.logger import get_logger

log = get_logger(__name__)


class ExecStatus(Enum):
    """What happened to a command, for callers (the voice front-end in particular)
    that need to react differently to "understood and done" vs. "had no idea what
    that meant" vs. "understood, but couldn't actually do it" -- a plain string
    result can't tell those apart, which used to mean a spoken/voice session had no
    way to give honest audio feedback on a failure."""

    OK = "ok"
    NO_MATCH = "no_match"
    FAILED = "failed"


HELP_TEXT = """Commands:
  open <app>                  - launch an app (e.g. "open chrome")
  close <app>                 - close a running app (asks for confirmation)
  minimize <app>               - minimize a running app's window
  create folder <name> in <location> - create a folder (e.g. "create a folder called spiderman in downloads")
  create file <name> in <location> - create a file (e.g. "create a file called notes in downloads")
  play <video> on vlc [from <location>] - find and play a video file in VLC
                                 (matches by title, e.g. "play gotham season 1
                                 episode 1 on vlc" -- no need to read the filename)
  show desktop                 - toggle show-desktop (minimize/restore all windows)
  open notifications           - open the notification center
  open search                  - open Windows search
  show wifi                    - open Wi-Fi settings
  show battery                 - report current battery percentage
  mute/unmute speaker           - toggle system mute
  open <x> in chrome           - open a URL, or search, specifically in Chrome
  play <x> on spotify           - open Spotify search results for a song/artist
  open mail / open email       - open Gmail in the browser
  search mail for <x>          - open Gmail scoped to a search
  open vscode [in <path>]     - open VS Code, optionally at a folder/file (searches
                                 your whole home folder if the exact path isn't given)
  open folder <path>          - open a folder in File Explorer (also searches by name)
  open file <name>             - open a file with its default app (searches for it)
  find file <name>             - report where a file lives without opening it
  open url <url>               - open a URL in the default browser
  run <script name>           - run a whitelisted script from config/apps.yaml
  list apps                   - list known/discoverable app names
  help                        - show this message
  exit / quit                 - stop the assistant
"""

# --- specific patterns first (order = priority) ---


@intent(r"^(?:open|search|go to)\s+(.+?)\s+in\s+chrome$")
def _open_in_chrome(query: str):
    return integrations.open_in_chrome(query)


@intent(r"^play\s+(.+?)\s+(?:on|in)\s+spotify$")
def _play_spotify(query: str):
    return integrations.spotify_search(query)


@intent(r"^open\s+(?:mail|email|gmail)$")
def _open_mail():
    return integrations.open_gmail()


@intent(r"^search\s+(?:mail|email|gmail)\s+for\s+(.+)$")
def _search_mail(query: str):
    return integrations.open_gmail(query)


@intent(r"^open vscode(?:\s+(?:in|at))?\s+(.+)$")
def _open_vscode_at(path: str):
    return actions.open_vscode(path=path)


@intent(r"^open vscode$")
def _open_vscode():
    return actions.open_vscode()


@intent(r"^(?:open|goto|go to)\s+file\s+(.+?):(\d+)$")
def _open_file_line(path: str, line: str):
    return actions.open_vscode(goto=f"{path}:{line}")


@intent(r"^open folder\s+(.+)$")
def _open_folder(path: str):
    return actions.open_folder(path)


# "open downloads" / "open the desktop folder" etc. -- built from
# actions._NAMED_LOCATIONS (the single source of truth for named-location
# spelling) rather than duplicating that list here, and registered ahead of
# the generic "open <x>" catch-all so these resolve straight to the real
# folder via open_folder instead of being treated as an app name to launch
# (which previously only worked for "downloads"/"documents" at all, and only
# because config/apps.yaml happened to alias them to an explorer.exe shell
# command -- every other named location, e.g. "open pictures", had no such
# alias and would misfire against whatever app fuzzy-matched closest).
_LOCATION_NAMES = sorted(actions._NAMED_LOCATIONS.keys(), key=len, reverse=True)
_LOCATION_ALTERNATION = "|".join(re.escape(n) for n in _LOCATION_NAMES)


@intent(rf"^open\s+(?:the\s+)?({_LOCATION_ALTERNATION})(?:\s+folder)?$")
def _open_named_location(name: str):
    return actions.open_folder(name)


@intent(r"^(?:find|locate|search for)\s+(?:the\s+)?file\s+(.+)$")
def _find_file(name: str):
    return actions.find_file(name)


@intent(r"^open\s+file\s+(.+)$")
def _open_file(name: str):
    return actions.open_file(name)


# Two orderings of the same command, both natural: "create a folder in downloads,
# name it spiderman" (location first) and "create a folder called spiderman in
# downloads" (name first). The location-first pattern is registered ahead of the
# name-first one since the name-first pattern's ".+? in .+" would otherwise also
# match the location-first phrasing (it has an " in " in it too), just with the
# name/location groups swapped wrong.
@intent(r"^create (?:a |a new )?folder in (.+?),?\s*(?:name it|named|call it|called)\s+(.+)$")
def _create_folder_location_first(location: str, name: str):
    return actions.create_folder(location, name)


@intent(r"^create (?:a |a new )?folder(?: called| named)? (.+?) in (.+)$")
def _create_folder_name_first(name: str, location: str):
    return actions.create_folder(location, name)


# Same two-ordering pattern as create folder above.
@intent(r"^create (?:a |a new )?file in (.+?),?\s*(?:name it|named|call it|called)\s+(.+)$")
def _create_file_location_first(location: str, name: str):
    return actions.create_file(location, name)


@intent(r"^create (?:a |a new )?file(?: called| named)? (.+?) in (.+)$")
def _create_file_name_first(name: str, location: str):
    return actions.create_file(location, name)


# "play spiderman on vlc", "open spiderman in vlc from downloads" -- location is
# optional; when omitted, actions.play_video searches the common video folders
# (Videos, Downloads, Desktop, Documents, home) itself.
@intent(r"^(?:play|open)\s+(.+?)\s+(?:on|in)\s+vlc(?:\s+(?:in|from)\s+(.+))?$")
def _play_video(name: str, location: Optional[str] = None):
    return actions.play_video(name, location)


@intent(r"^open\s+(?:website|url)\s+(.+)$")
def _open_url(url: str):
    return actions.open_url(url)


@intent(r"^(?:run|execute)\s+(.+)$")
def _run_script(name: str):
    return actions.run_script(name)


@intent(r"^(?:close|kill)\s+(.+)$")
def _close_app(name: str):
    return actions.close_app(name)


@intent(r"^(?:minimize|minimise)\s+(.+)$")
def _minimize_app(name: str):
    return actions.minimize_app(name)


@intent(r"^(?:take me to|go to|show)\s+(?:the\s+)?desktop$")
def _show_desktop():
    return actions.show_desktop()


@intent(r"^(?:open|show)\s+(?:the\s+)?notifications?$")
def _open_notifications():
    return actions.open_notifications()


@intent(r"^(?:open|show)\s+(?:the\s+)?search$")
def _open_search():
    return actions.open_search()


@intent(r"^(?:open|show)\s+(?:the\s+)?wifi$")
def _open_wifi():
    return actions.open_wifi_settings()


@intent(r"^(?:show|what's)\s+(?:my\s+)?battery(?:\s+percent(?:age)?)?$")
def _battery_status():
    return actions.battery_status()


@intent(r"^(?:mute|unmute)\s+(?:the\s+)?speakers?$")
def _toggle_mute():
    return actions.toggle_mute()


@intent(r"^(?:play|pause|resume)$")
def _media_play_pause():
    return actions.media_play_pause()


@intent(r"^(?:next|skip)(?:\s+track)?$")
def _media_next():
    return actions.media_next()


@intent(r"^(?:previous|back|last)(?:\s+track)?$")
def _media_previous():
    return actions.media_previous()


@intent(r"^list apps$")
def _list_apps():
    return actions.list_known_apps()


@intent(r"^help$")
def _help():
    return HELP_TEXT


# --- generic catch-all last ---


@intent(r"^open\s+(.+)$")
def _open_app(name: str):
    return actions.open_app(name)


def execute_with_status(text: str) -> Tuple[ExecStatus, str]:
    """Same routing as execute() but also reports what kind of outcome it was --
    OK / NO_MATCH / FAILED -- rather than collapsing that into one string. The
    voice front-end (assistant/tts.py, via listen.py) needs this distinction to
    give honest spoken feedback instead of staying silent (or worse, always
    sounding like it succeeded) when a command wasn't understood or couldn't
    actually be carried out."""
    text = text.strip()
    if not text:
        return ExecStatus.NO_MATCH, "Say something."

    fn, groups = match(text)
    if fn is None:
        llm_result = llm_backend.handle(text)
        if llm_result is not None:
            return ExecStatus.OK, llm_result
        msg = f"Didn't understand: '{text}'. Type 'help' for a list of commands."
        log.info(msg)
        return ExecStatus.NO_MATCH, msg

    log.info("Executing intent for: '%s'", text)
    try:
        return ExecStatus.OK, fn(*groups)
    except actions.ActionError as e:
        log.warning(str(e))
        return ExecStatus.FAILED, str(e)
    except Exception as e:
        msg = f"Error executing '{text}': {e}"
        log.error(msg)
        return ExecStatus.FAILED, msg


def execute(text: str) -> str:
    _, message = execute_with_status(text)
    return message
