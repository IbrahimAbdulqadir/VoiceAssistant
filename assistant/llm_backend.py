"""Phase 4: LLM tool-calling fallback. When the regex router in executor.py doesn't
recognize a command, this hands the raw text to an LLM (OpenAI, via the Responses
API) with the same actions/integrations functions exposed as tools, so open-ended or
oddly-phrased commands ("go into my project folder and open vscode there") still
resolve to a concrete action instead of a flat "Didn't understand".

This is deliberately the *fallback* path, not the primary one -- the regex router is
free (no API call, no latency) and handles the common cases; this only runs when
that router draws a blank, which is also why it's cheap in practice: most commands
never reach it.
"""

import json
import os
from typing import Optional

from assistant import actions, integrations
from assistant.logger import get_logger

log = get_logger(__name__)

DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-nano")

TOOLS = [
    {
        "type": "function",
        "name": "open_app",
        "description": "Launch an installed application by name (e.g. 'chrome', 'spotify', 'notepad').",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "App name as spoken/typed"}},
            "required": ["name"],
        },
    },
    {
        "type": "function",
        "name": "close_app",
        "description": "Close a running application by name. Asks for confirmation before terminating and refuses protected system processes.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "type": "function",
        "name": "open_vscode",
        "description": (
            "Open VS Code, optionally at a specific folder/file. 'path' does not need "
            "to be an exact filesystem path -- if it isn't one, this searches the "
            "user's whole home folder recursively for a matching file/folder name, so "
            "just pass the name as given (e.g. path='actions.py'), never a guessed path."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Folder/file name or path to open; defaults to current directory"},
                "goto": {"type": "string", "description": "Optional file:line to jump to, e.g. 'main.py:10'"},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "open_folder",
        "description": (
            "Open a folder in Windows File Explorer. If 'path' isn't an exact existing "
            "path, this searches the user's whole home folder recursively by name."
        ),
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "type": "function",
        "name": "find_file",
        "description": (
            "Search the user's whole home folder (or a given location) recursively "
            "for a file by name and report where it is, without opening it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "File name (with or without extension) to search for"},
                "location": {"type": "string", "description": "Optional named location or full path to scope the search to"},
            },
            "required": ["name"],
        },
    },
    {
        "type": "function",
        "name": "open_file",
        "description": (
            "Open a file with its default associated app. Searches the user's whole "
            "home folder (or a given location) recursively when 'name' isn't already "
            "an exact path."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "File name (with or without extension) to open"},
                "location": {"type": "string", "description": "Optional named location or full path to scope the search to"},
            },
            "required": ["name"],
        },
    },
    {
        "type": "function",
        "name": "create_folder",
        "description": "Create a new folder inside a named location (e.g. 'downloads', 'documents', 'desktop') or a full path.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "Where to create it, e.g. 'downloads' or a full path"},
                "name": {"type": "string", "description": "Name of the new folder"},
            },
            "required": ["location", "name"],
        },
    },
    {
        "type": "function",
        "name": "create_file",
        "description": "Create a new empty file inside a named location (e.g. 'downloads', 'documents', 'desktop') or a full path. Defaults to a .txt extension if none is given.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "Where to create it, e.g. 'downloads' or a full path"},
                "name": {"type": "string", "description": "Name of the new file"},
            },
            "required": ["location", "name"],
        },
    },
    {
        "type": "function",
        "name": "play_video",
        "description": (
            "Find a video file by title and play it in VLC. Searches Videos, Downloads, "
            "Desktop, Documents, and home if no location is given. Matches against the "
            "actual scene-release filename (e.g. 'Gotham.S01E01.1080p.WEB.x264-GROUP.mkv') "
            "by fuzzy title, so pass just the plain title spoken, e.g. name='gotham season 1 "
            "episode 1' or name='the batman' -- never try to reconstruct the exact filename."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Spoken title, optionally with 'season X episode Y'"},
                "location": {"type": "string", "description": "Optional named location or full path to search"},
            },
            "required": ["name"],
        },
    },
    {
        "type": "function",
        "name": "open_url",
        "description": "Open a URL in the default web browser.",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "type": "function",
        "name": "run_script",
        "description": "Run a whitelisted script/command defined in config/apps.yaml by its name. Refuses anything not explicitly whitelisted there.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "type": "function",
        "name": "list_known_apps",
        "description": "List every app name the assistant currently knows how to open.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "open_in_chrome",
        "description": "Open a URL, or a Google search, specifically in Chrome rather than the OS default browser.",
        "parameters": {
            "type": "object",
            "properties": {"query_or_url": {"type": "string"}},
            "required": ["query_or_url"],
        },
    },
    {
        "type": "function",
        "name": "spotify_play",
        "description": "Search for and start playing a song/artist on Spotify.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "gmail_search",
        "description": "Open Gmail, optionally scoped to a search query. Omit query to just open the inbox.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Optional search terms"}},
            "required": [],
        },
    },
]

_DISPATCH = {
    "open_app": lambda name: actions.open_app(name),
    "close_app": lambda name: actions.close_app(name),
    "open_vscode": lambda path=".", goto=None: actions.open_vscode(path=path, goto=goto),
    "open_folder": lambda path: actions.open_folder(path),
    "find_file": lambda name, location=None: actions.find_file(name, location),
    "open_file": lambda name, location=None: actions.open_file(name, location),
    "create_folder": lambda location, name: actions.create_folder(location, name),
    "create_file": lambda location, name: actions.create_file(location, name),
    "play_video": lambda name, location=None: actions.play_video(name, location),
    "open_url": lambda url: actions.open_url(url),
    "run_script": lambda name: actions.run_script(name),
    "list_known_apps": lambda: actions.list_known_apps(),
    "open_in_chrome": lambda query_or_url: integrations.open_in_chrome(query_or_url),
    "spotify_play": lambda query: integrations.spotify_search(query),
    "gmail_search": lambda query=None: integrations.open_gmail(query),
}

SYSTEM_PROMPT = (
    "You are the tool-calling backend for a personal Windows voice assistant. The "
    "text you're given is a wake-word-triggered voice transcription -- it was NOT "
    "typed by the user with intent, and the wake word can trigger on nearby speech "
    "that was never meant for the assistant at all (a conversation in the room, "
    "background audio, someone thinking out loud). Whisper transcription errors are "
    "also common. Because of this, only call a tool when the text plausibly reads as "
    "a direct command to you specifically -- an imperative addressed to the "
    "assistant, phrased like 'open X' / 'close Y' / 'play Z'. If it instead reads "
    "like a sentence from a conversation, a narrated thought, or a garbled fragment "
    "that merely happens to contain a word matching a tool (e.g. an app name "
    "mentioned in passing, not asked to be opened), do NOT call any tool -- respond "
    "with plain text noting it didn't look like a command, even if some literal "
    "words in it resemble one. When you do call a tool, do not guess at destructive "
    "actions (closing an app, running a script) if the target is ambiguous."
)

_client = None


def is_configured() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI()
    return _client


def handle(text: str) -> Optional[str]:
    """Runs an LLM-chosen tool for free-form text and returns the result message, or
    None if the backend isn't configured (caller should fall back to its own
    "didn't understand" message)."""
    if not is_configured():
        return None

    try:
        client = _get_client()
        input_list = [{"role": "user", "content": text}]

        response = client.responses.create(
            model=DEFAULT_MODEL,
            instructions=SYSTEM_PROMPT,
            tools=TOOLS,
            input=input_list,
        )
        input_list += response.output

        called_any = False
        for item in response.output:
            if item.type != "function_call":
                continue
            called_any = True
            fn = _DISPATCH.get(item.name)
            if fn is None:
                result = f"Unknown tool: {item.name}"
                log.warning(result)
            else:
                try:
                    args = json.loads(item.arguments) if item.arguments else {}
                    log.info("LLM backend calling %s(%s)", item.name, args)
                    result = fn(**args)
                except Exception as e:
                    result = f"Error running {item.name}: {e}"
                    log.error(result)

            input_list.append(
                {
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": str(result),
                }
            )

        if not called_any:
            return response.output_text or "Nothing matched that command."

        final = client.responses.create(
            model=DEFAULT_MODEL,
            instructions="Report back what happened in one short sentence, based on the tool result(s) above.",
            tools=TOOLS,
            input=input_list,
        )
        return final.output_text
    except Exception as e:
        msg = f"LLM backend error: {e}"
        log.error(msg)
        return msg
