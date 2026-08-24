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
        "description": "Open VS Code, optionally at a specific folder path or file:line location.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Folder path to open; defaults to current directory"},
                "goto": {"type": "string", "description": "Optional file:line to jump to, e.g. 'main.py:10'"},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "open_folder",
        "description": "Open a folder in Windows File Explorer.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
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
    "create_folder": lambda location, name: actions.create_folder(location, name),
    "open_url": lambda url: actions.open_url(url),
    "run_script": lambda name: actions.run_script(name),
    "list_known_apps": lambda: actions.list_known_apps(),
    "open_in_chrome": lambda query_or_url: integrations.open_in_chrome(query_or_url),
    "spotify_play": lambda query: integrations.spotify_search(query),
    "gmail_search": lambda query=None: integrations.open_gmail(query),
}

SYSTEM_PROMPT = (
    "You are the tool-calling backend for a personal Windows voice assistant. "
    "The user's command didn't match any of the assistant's fast built-in patterns, "
    "so it was handed to you directly. Call the single most appropriate tool. If "
    "nothing fits, respond with plain text explaining you couldn't find a matching "
    "action -- do not guess at destructive actions (closing an app, running a script) "
    "if the target is ambiguous."
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
