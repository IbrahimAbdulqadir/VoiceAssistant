"""Registers every supported intent pattern and exposes execute(text) as the single
entry point. This is the thing Phase 2 (wake word + Whisper) feeds transcribed text
into, unchanged -- it doesn't know or care whether the text was typed or spoken.
"""

from typing import Optional

from assistant import actions, integrations, llm_backend
from assistant.intents import intent, match
from assistant.logger import get_logger

log = get_logger(__name__)

HELP_TEXT = """Commands:
  open <app>                  - launch an app (e.g. "open chrome")
  close <app>                 - close a running app (asks for confirmation)
  minimize <app>               - minimize a running app's window
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
  open vscode [in <path>]     - open VS Code, optionally at a folder/file
  open folder <path>          - open a folder in File Explorer
  open url <url>              - open a URL in the default browser
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


def execute(text: str) -> str:
    text = text.strip()
    if not text:
        return "Say something."

    fn, groups = match(text)
    if fn is None:
        llm_result = llm_backend.handle(text)
        if llm_result is not None:
            return llm_result
        msg = f"Didn't understand: '{text}'. Type 'help' for a list of commands."
        log.info(msg)
        return msg

    log.info("Executing intent for: '%s'", text)
    try:
        return fn(*groups)
    except Exception as e:
        msg = f"Error executing '{text}': {e}"
        log.error(msg)
        return msg
