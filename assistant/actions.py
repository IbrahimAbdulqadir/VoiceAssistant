"""The actual doing. Every function here is a leaf action: open something, close
something, run something. Nothing in this module parses intent -- that's executor.py's
job -- so these are also exactly what a Phase 4 LLM tool-calling backend would call
directly as its "tools".

Each action returns a plain string result message rather than printing, so both the
CLI and (later) a voice/TTS front-end can consume the same functions.
"""

import os
import re
import shlex
import shutil
import subprocess
import webbrowser
from pathlib import Path
from typing import Callable, List, Optional, Set, Tuple

import psutil
import win32api
import win32con
import win32gui
import win32process
from rapidfuzz import fuzz

from assistant.config import config
from assistant.resolver import resolve_app_path
from assistant.logger import get_logger

log = get_logger(__name__)

ConfirmFn = Callable[[str], bool]


class ActionError(Exception):
    """Raised by an action that was understood but genuinely couldn't be carried
    out (app not found, nothing running to close, bad path, etc.) -- as opposed
    to a plain return, which always means the action succeeded (even if the
    success is just informational, like "no battery on this machine"). executor.py
    catches this to tell "understood but failed" apart from "succeeded" so the
    voice front-end (assistant/tts.py) can give an honest spoken response instead
    of always saying "done"."""

# Set True by listen.py once at startup -- a voice session has no stdin for
# answering a typed y/N prompt, so close/run-script confirmation is skipped there.
# Speaker verification (Phase 3) is the real gate on who can issue a command at all.
VOICE_MODE = False


def _default_confirm(prompt: str) -> bool:
    """Fallback confirmation used when no UI-specific confirm function is supplied."""
    answer = input(f"{prompt} [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def _pids_for_process_names(names: List[str]) -> Set[int]:
    names_lower = {n.strip().rstrip(".,!?;: ").lower() for n in names if n}
    pids: Set[int] = set()
    for proc in psutil.process_iter(["pid", "name"]):
        pname = (proc.info.get("name") or "").lower()
        pname_stem = pname[:-4] if pname.endswith(".exe") else pname
        if pname in names_lower or pname_stem in names_lower:
            pids.add(proc.info["pid"])
    return pids


def _visible_windows_for_pids(pids: Set[int]) -> List[int]:
    hwnds: List[int] = []

    def _enum_handler(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or not win32gui.GetWindowText(hwnd):
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid in pids:
            hwnds.append(hwnd)

    win32gui.EnumWindows(_enum_handler, None)
    return hwnds


def _hwnds_for_title(substring: str) -> List[int]:
    """Fallback for apps with no process name of their own to match -- e.g. Chrome
    PWAs (chess, unigram, whatsapp web) all run as chrome.exe/chrome_proxy.exe,
    shared with the entire browser, so matching by process name either finds
    nothing or -- if matched loosely against "chrome" -- would wrongly catch
    every ordinary browser window too. Matching by window title instead finds
    just that app's own window."""
    substring_lower = substring.strip().rstrip(".,!?;: ").lower()
    hwnds: List[int] = []

    def _enum_handler(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if title and substring_lower in title.lower():
            hwnds.append(hwnd)

    win32gui.EnumWindows(_enum_handler, None)
    return hwnds


def _focus_window(hwnd: int) -> None:
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        # Windows refuses SetForegroundWindow from a background process unless the
        # calling thread currently "owns" the foreground -- briefly tapping Alt is
        # a well-known workaround for that lock.
        try:
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32gui.SetForegroundWindow(hwnd)
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        except Exception as e:
            log.debug("Could not force foreground for window: %s", e)


def _process_name_candidates(name: str) -> List[str]:
    """Everything close_app/minimize_app/open_app's dedupe check should try
    matching a running process name against for a given spoken/alias name.

    A bare alias key is often *not* the real process name -- "vscode" is an
    alias for the command "code", but the actual running process is
    "Code.exe"; matching literally on "vscode" never finds it. Resolving
    through resolve_app_path (same as open_app already does) and, if the
    target is a bare command rather than a path, following it through
    shutil.which() to find what it actually launches, recovers the real
    process stem in cases like this without hardcoding per-app exceptions."""
    candidates = {name}
    match = resolve_app_path(name)
    if not match:
        return list(candidates)
    matched_name, target = match
    candidates.add(matched_name)
    if os.path.exists(target):
        candidates.add(Path(target).stem)
    else:
        which = shutil.which(target)
        if which:
            candidates.add(Path(which).stem)
    return list(candidates)


def open_app(name: str) -> str:
    match = resolve_app_path(name)
    if not match:
        msg = f"Couldn't find an app matching '{name}'."
        log.warning(msg)
        raise ActionError(msg)

    matched_name, target = match

    proc_stem = Path(target).stem if os.path.exists(target) else matched_name
    pids = _pids_for_process_names([proc_stem, matched_name])
    hwnds = _visible_windows_for_pids(pids) if pids else []
    if not hwnds:
        # Covers apps with no process name of their own to match on, e.g. Chrome
        # PWAs (chess, unigram) that all run as chrome.exe/chrome_proxy.exe --
        # without this, "open chess" would never recognize an already-open PWA
        # window and would keep launching new ones instead of focusing it.
        hwnds = _hwnds_for_title(matched_name)
    if hwnds:
        _focus_window(hwnds[0])
        msg = f"{matched_name} is already open -- switching to it."
        log.info(msg)
        return msg
    if pids:
        msg = f"{matched_name} is already running."
        log.info(msg)
        return msg

    try:
        if os.path.exists(target):
            os.startfile(target)
        else:
            # Not a filesystem path -- treat as a command on PATH (e.g. "code").
            subprocess.Popen(target, shell=True)
        msg = f"Opened {matched_name} ({target})."
        log.info(msg)
        return msg
    except Exception as e:
        msg = f"Failed to open {matched_name}: {e}"
        log.error(msg)
        raise ActionError(msg)


def close_app(name: str, confirm: ConfirmFn = _default_confirm) -> str:
    protected = config.protected_processes
    name_candidates = {c.strip().rstrip(".,!?;: ").lower() for c in _process_name_candidates(name)}

    matches: List[psutil.Process] = []
    for proc in psutil.process_iter(["pid", "name"]):
        pname = (proc.info.get("name") or "").lower()
        pname_stem = pname[:-4] if pname.endswith(".exe") else pname
        if any(c in (pname, pname_stem) or c in pname_stem for c in name_candidates):
            matches.append(proc)

    if not matches:
        # No process matches by name at all -- covers apps that don't have a
        # process name of their own to match on, e.g. Chrome PWAs (chess,
        # unigram) which all run as chrome.exe/chrome_proxy.exe shared with the
        # entire browser. Closing the whole shared process would be wrong (it'd
        # kill every other Chrome window and tab too), so this closes just the
        # matching window(s) via WM_CLOSE instead of terminating a process.
        hwnds = _hwnds_for_title(name)
        if not hwnds:
            msg = f"No running process or window matching '{name}'."
            log.info(msg)
            raise ActionError(msg)
        if not VOICE_MODE and not confirm(f"Close {len(hwnds)} window(s) matching '{name}'?"):
            msg = "Cancelled."
            log.info("User declined to close: %s", name)
            return msg
        for hwnd in hwnds:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        msg = f"Closed {len(hwnds)} window(s) matching '{name}'."
        log.info(msg)
        return msg

    blocked = [p for p in matches if (p.info.get("name") or "").lower() in protected]
    killable = [p for p in matches if p not in blocked]

    if blocked and not killable:
        msg = f"'{name}' matches a protected system process ({blocked[0].info['name']}); refusing to close it."
        log.warning(msg)
        raise ActionError(msg)

    names = ", ".join(sorted({p.info["name"] for p in killable}))
    if not VOICE_MODE and not confirm(f"Close {len(killable)} process(es) matching '{name}' ({names})?"):
        msg = "Cancelled."
        log.info("User declined to close: %s", names)
        return msg

    closed = []
    for proc in killable:
        try:
            proc.terminate()
            closed.append(proc.info["name"])
        except Exception as e:
            log.error("Failed to terminate %s (pid %s): %s", proc.info.get("name"), proc.pid, e)

    msg = f"Closed: {', '.join(closed)}." if closed else "Nothing closed."
    log.info(msg)
    return msg


def _press_hotkey(*vk_codes: int) -> None:
    """Simulates a key chord (e.g. Win+D) via the same virtual-key events a
    physical keypress would send -- used for OS-level actions with no clean
    API (opening Notification Center, Search) or that are naturally a hardware
    key on most keyboards (volume mute)."""
    for vk in vk_codes:
        win32api.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vk_codes):
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)


def show_desktop() -> str:
    import win32com.client

    win32com.client.Dispatch("Shell.Application").ToggleDesktop()
    msg = "Toggled the desktop view."
    log.info(msg)
    return msg


def open_notifications() -> str:
    _press_hotkey(win32con.VK_LWIN, ord("N"))
    msg = "Opened notification center."
    log.info(msg)
    return msg


def open_search() -> str:
    _press_hotkey(win32con.VK_LWIN, ord("S"))
    msg = "Opened search."
    log.info(msg)
    return msg


def open_wifi_settings() -> str:
    os.startfile("ms-settings:network-wifi")
    msg = "Opened Wi-Fi settings."
    log.info(msg)
    return msg


def battery_status() -> str:
    battery = psutil.sensors_battery()
    if battery is None:
        msg = "No battery detected on this machine."
        log.info(msg)
        return msg

    state = "plugged in" if battery.power_plugged else "on battery"
    msg = f"Battery is at {battery.percent:.0f}%, {state}."
    log.info(msg)
    return msg


def toggle_mute() -> str:
    _press_hotkey(win32con.VK_VOLUME_MUTE)
    msg = "Toggled speaker mute."
    log.info(msg)
    return msg


def media_play_pause() -> str:
    _press_hotkey(win32con.VK_MEDIA_PLAY_PAUSE)
    msg = "Toggled play/pause."
    log.info(msg)
    return msg


def media_next() -> str:
    _press_hotkey(win32con.VK_MEDIA_NEXT_TRACK)
    msg = "Skipped to next track."
    log.info(msg)
    return msg


def media_previous() -> str:
    _press_hotkey(win32con.VK_MEDIA_PREV_TRACK)
    msg = "Went back to previous track."
    log.info(msg)
    return msg


def minimize_app(name: str) -> str:
    pids = _pids_for_process_names(_process_name_candidates(name))
    hwnds = _visible_windows_for_pids(pids) if pids else []
    if not hwnds:
        # Falls back to a window-title search when process-name matching finds
        # nothing running, or finds a process with no window of its own to
        # minimize -- needed for apps like Chrome PWAs (chess, unigram) that
        # share chrome.exe/chrome_proxy.exe with the whole browser, so there's
        # no process name to match on the app's own.
        hwnds = _hwnds_for_title(name)
    if not hwnds:
        msg = f"No running window matching '{name}'."
        log.info(msg)
        raise ActionError(msg)

    for hwnd in hwnds:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)

    msg = f"Minimized {name}."
    log.info(msg)
    return msg


def open_vscode(path: str = ".", goto: Optional[str] = None) -> str:
    code_exe = shutil.which("code")
    if not code_exe:
        msg = "VS Code CLI ('code') not found on PATH. Install it or enable 'Shell Command: Install code command' from VS Code's command palette."
        log.warning(msg)
        raise ActionError(msg)

    target = path
    if goto is None and path != ".":
        # A spoken/typed "open <file> in vscode" is almost never a real literal
        # path -- it's just a name. Previously this was passed straight through
        # to the VS Code CLI, which silently opened (or created) a bogus path
        # relative to cwd instead of the file the user actually meant. Falling
        # back to a filesystem search, same as play_video does for video
        # titles, resolves a bare name to wherever the file actually lives.
        p = Path(os.path.expandvars(path)).expanduser()
        if not p.exists():
            found = _find_file(path)
            if found is not None:
                target = str(found)

    # --reuse-window: without it, the VS Code CLI opens a brand new window on
    # every single invocation, even for the exact same folder -- this is what
    # was causing "open vscode" to pile up extra windows instead of just
    # bringing the existing one forward.
    args = [code_exe, "--reuse-window"]
    if goto:
        args += ["--goto", goto]
    else:
        args.append(target)

    try:
        subprocess.Popen(args, shell=True)
        msg = f"Opened VS Code at {goto or target}."
        log.info(msg)
        return msg
    except Exception as e:
        msg = f"Failed to open VS Code: {e}"
        log.error(msg)
        raise ActionError(msg)


def run_script(name: str, confirm: ConfirmFn = _default_confirm) -> str:
    """Runs a command from the config.scripts whitelist only -- arbitrary shell
    execution from transcribed speech is refused by design."""
    scripts = config.scripts
    key = name.strip().lower()
    command = scripts.get(key)

    if not command:
        msg = f"'{name}' isn't a whitelisted script. Add it to config/apps.yaml under 'scripts' first."
        log.warning(msg)
        raise ActionError(msg)

    if not VOICE_MODE and not confirm(f"Run script '{key}': {command}?"):
        return "Cancelled."

    try:
        result = subprocess.run(
            command if os.name == "nt" else shlex.split(command),
            shell=(os.name == "nt"),
            capture_output=True,
            text=True,
            timeout=120,
        )
        log.info("Ran script '%s' (exit %s)", key, result.returncode)
        output = (result.stdout or "").strip()
        return f"Ran '{key}' (exit {result.returncode}).\n{output}" if output else f"Ran '{key}' (exit {result.returncode})."
    except subprocess.TimeoutExpired:
        msg = f"'{key}' timed out after 120s."
        log.error(msg)
        raise ActionError(msg)
    except Exception as e:
        msg = f"Failed to run '{key}': {e}"
        log.error(msg)
        raise ActionError(msg)


def open_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    msg = f"Opened {url}."
    log.info(msg)
    return msg


# Directories that are either huge, machine-internal, or virtually never where a
# user-requested file/folder actually lives -- walking into these would make a
# whole-file-system search take forever (or trip permission errors) for no real
# payoff, so they're pruned before os.walk ever descends into them. Matched
# case-insensitively against a bare directory name, not a full path.
_SEARCH_EXCLUDE_DIRS = {
    "appdata", "node_modules", "__pycache__", "venv", ".venv",
    "$recycle.bin", "system volume information", "windows", "programdata",
    "program files", "program files (x86)", "site-packages",
}
FILE_MATCH_THRESHOLD = 70  # fuzzy filename-match score (0-100) below which we give up


def _iter_search_roots(location: Optional[str]) -> List[Path]:
    """Where to look when the caller didn't hand over an exact, already-existing
    path. A named/explicit location narrows the search to just that folder; with
    none given, this searches the user's whole home directory -- not just one
    hardcoded app's download folder -- since a voice command asking to open some
    file rarely says where it lives."""
    if location:
        base = _resolve_location(location)
        if base is None:
            candidate = Path(os.path.expandvars(location)).expanduser()
            base = candidate if candidate.is_dir() else None
        return [base] if base is not None else []
    return [Path.home()]


def _walk_paths(root: Path, want_dirs: bool):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d.lower() not in _SEARCH_EXCLUDE_DIRS and not d.startswith(".")
        ]
        names = dirnames if want_dirs else filenames
        for n in names:
            yield Path(dirpath) / n


def _find_file(name: str, location: Optional[str] = None) -> Optional[Path]:
    """Generic filename search: the same fuzzy-match approach _find_video_file
    uses for video titles, but for any file and recursing into subfolders
    (video search only ever looks at one folder's top level) under whichever
    root(s) _iter_search_roots picks. This is what lets "open <file> in vscode"
    or "find <file>" resolve a bare name instead of requiring an exact path."""
    raw_name = name.strip().rstrip(".,!?;: ")
    roots = _iter_search_roots(location)

    has_ext = "." in raw_name
    target_lower = raw_name.lower()
    stem_lower = Path(raw_name).stem.lower() if has_ext else target_lower

    best: Optional[Path] = None
    best_score = 0
    for root in roots:
        if not root.exists():
            continue
        for f in _walk_paths(root, want_dirs=False):
            fname_lower = f.name.lower()
            if fname_lower == target_lower or (not has_ext and f.stem.lower() == target_lower):
                return f  # exact match -- stop looking immediately
            score = fuzz.token_sort_ratio(stem_lower, f.stem.lower())
            if score > best_score:
                best, best_score = f, score

    return best if best_score >= FILE_MATCH_THRESHOLD else None


def _find_folder(name: str, location: Optional[str] = None) -> Optional[Path]:
    """Same idea as _find_file but for directories -- lets "open folder <name>"
    resolve a bare folder name anywhere under the search root(s) instead of
    requiring the caller to already know its full path."""
    raw_name = name.strip().rstrip(".,!?;: ")
    roots = _iter_search_roots(location)
    target_lower = raw_name.lower()

    best: Optional[Path] = None
    best_score = 0
    for root in roots:
        if not root.exists():
            continue
        for d in _walk_paths(root, want_dirs=True):
            if d.name.lower() == target_lower:
                return d  # exact match -- stop looking immediately
            score = fuzz.token_sort_ratio(target_lower, d.name.lower())
            if score > best_score:
                best, best_score = d, score

    return best if best_score >= FILE_MATCH_THRESHOLD else None


def open_folder(path: str) -> str:
    # _resolve_location covers named locations ("downloads", "desktop",
    # "pictures", "telegram desktop", ...) *and* an already-existing literal
    # path (env-var placeholders like %USERNAME% included) -- previously this
    # only handled the literal-path case, so "open downloads" on its own
    # wasn't a real path and fell straight to a filesystem search (or the LLM
    # fallback guessing something equally wrong) instead of just resolving it.
    p = _resolve_location(path)
    if p is None:
        p = _find_folder(path)
    if p is None:
        msg = f"Path does not exist: {path}"
        log.warning(msg)
        raise ActionError(msg)
    os.startfile(str(p))
    msg = f"Opened folder {p}."
    log.info(msg)
    return msg


def find_file(name: str, location: Optional[str] = None) -> str:
    """Voice-facing "find file X" / "where is X" action -- just reports where a
    file lives without opening it, useful when the user isn't sure it exists or
    doesn't remember which folder they saved it in."""
    match = _find_file(name, location)
    if match is None:
        where = f" in {location}" if location else " anywhere under your home folder"
        msg = f"Couldn't find a file matching '{name}'{where}."
        log.warning(msg)
        raise ActionError(msg)
    msg = f"Found {match.name} at {match}."
    log.info(msg)
    return msg


def open_file(name: str, location: Optional[str] = None) -> str:
    """Opens a file with its default associated app, searching for it (same as
    open_vscode's fallback) when the given name isn't already an exact path."""
    p = Path(os.path.expandvars(name)).expanduser()
    if not p.is_file():
        found = _find_file(name, location)
        if found is None:
            where = f" in {location}" if location else " anywhere under your home folder"
            msg = f"Couldn't find a file matching '{name}'{where}."
            log.warning(msg)
            raise ActionError(msg)
        p = found
    os.startfile(str(p))
    msg = f"Opened {p.name}."
    log.info(msg)
    return msg


# Named locations "create a folder in <here>" should resolve without the user
# having to spell out a full path -- these are real filesystem paths (unlike the
# `shell:`-namespace aliases in config/apps.yaml, which only work for *launching*
# Explorer at a virtual folder, not for creating a real file inside one).
_NAMED_LOCATIONS = {
    "downloads": lambda: Path.home() / "Downloads",
    "download": lambda: Path.home() / "Downloads",
    "documents": lambda: Path.home() / "Documents",
    "desktop": lambda: Path.home() / "Desktop",
    "pictures": lambda: Path.home() / "Pictures",
    "music": lambda: Path.home() / "Music",
    "videos": lambda: Path.home() / "Videos",
    "home": lambda: Path.home(),
    # Telegram Desktop's default download location -- this is where this
    # user's actual TV/movie downloads live, so it needs to be a first-class
    # named location, not something you have to spell out a full path for.
    "telegram desktop": lambda: Path.home() / "Downloads" / "Telegram Desktop",
    "telegram": lambda: Path.home() / "Downloads" / "Telegram Desktop",
}


def _resolve_location(location: str) -> Optional[Path]:
    key = location.strip().rstrip(".,!?;: ").lower()
    if key in _NAMED_LOCATIONS:
        return _NAMED_LOCATIONS[key]()
    candidate = Path(os.path.expandvars(location)).expanduser()
    if candidate.is_dir():
        return candidate
    return None


def create_folder(location: str, name: str) -> str:
    base = _resolve_location(location)
    if base is None:
        msg = f"Couldn't find a location matching '{location}'."
        log.warning(msg)
        raise ActionError(msg)
    if not base.exists():
        msg = f"'{base}' doesn't exist."
        log.warning(msg)
        raise ActionError(msg)

    folder_name = name.strip().rstrip(".,!?;: ")
    target = base / folder_name
    if target.exists():
        msg = f"'{folder_name}' already exists in {base}."
        log.info(msg)
        return msg

    try:
        target.mkdir(parents=True)
    except Exception as e:
        msg = f"Failed to create folder '{folder_name}' in {base}: {e}"
        log.error(msg)
        raise ActionError(msg)
    msg = f"Created folder '{folder_name}' in {base}."
    log.info(msg)
    return msg


def create_file(location: str, name: str) -> str:
    base = _resolve_location(location)
    if base is None:
        msg = f"Couldn't find a location matching '{location}'."
        log.warning(msg)
        raise ActionError(msg)
    if not base.exists():
        msg = f"'{base}' doesn't exist."
        log.warning(msg)
        raise ActionError(msg)

    file_name = name.strip().rstrip(".,!?;: ")
    # A spoken name almost never includes an extension ("create a file named
    # notes") -- default to .txt so the file actually opens in something
    # sensible, but respect one if the user did say/type it (e.g. "notes.py").
    if "." not in file_name:
        file_name += ".txt"

    target = base / file_name
    if target.exists():
        msg = f"'{file_name}' already exists in {base}."
        log.info(msg)
        return msg

    try:
        target.touch()
    except Exception as e:
        msg = f"Failed to create file '{file_name}' in {base}: {e}"
        log.error(msg)
        raise ActionError(msg)
    msg = f"Created file '{file_name}' in {base}."
    log.info(msg)
    return msg


VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg")
# Search order when no location is given -- voice commands almost never
# include a full path, just "play <name> on vlc", so check the places a
# video is actually likely to be.
_DEFAULT_VIDEO_SEARCH_LOCATIONS = ["videos", "telegram desktop", "downloads", "desktop", "documents", "home"]


# Scene-release filenames are never what you'd actually say out loud (e.g.
# "Gotham.S01E01.1080p.WEB.x264-GROUP.mkv") -- these strip that junk down to a
# plain title so voice can match on "gotham" / "the batman" instead of making
# the user read the whole displayed filename back.
_SXXEYY_RE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,2})")
# "<title> season <N> episode <M>" (also matches the glued short form "s1e1"
# since every literal in the alternation still only needs the digits adjacent
# to it, with \s* allowing zero or more spaces around each piece).
_SEASON_EPISODE_WORDS_RE = re.compile(
    r"^(.*?)\s*(?:season|s)\s*(\d{1,2})\s*(?:episode|ep|e)\s*(\d{1,2})\s*$",
    re.IGNORECASE,
)
# A *whole token* that marks the start of scene-release metadata (resolution,
# source, codec, audio) -- matched per-token rather than substring-subbed, and
# used as a truncation point (see _clean_title) rather than something to strip
# out piece by piece. Scene naming conventions always put the release group
# and any other uploader tag *after* every one of these, so truncating at the
# first one dumps arbitrary group names ("Cinemagic_HD", "SeriesLand4U",
# "GalaxyTV", "HETeam"...) for free, without having to enumerate every group
# that exists -- trying to strip them individually is a losing battle.
_QUALITY_MARKER_RE = re.compile(
    r"^(?:\d{3,4}p|4k|webrip|web-?dl|web|bluray|brrip|bdrip|dvdrip|hdrip|hdtv|"
    r"x264|x265|h264|h265|hevc|aac\d*|ac3|dts|remux|proper|repack|extended|"
    r"uncut|multi|dual|\d{1,2}bit|\d{1,2}ch)$",
    re.IGNORECASE,
)
_YEAR_TOKEN_RE = re.compile(r"^\(?(19|20)\d{2}\)?$")
_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
    "nineteen": "19", "twenty": "20",
}
VIDEO_MATCH_THRESHOLD = 70  # fuzzy title-match score (0-100) below which we give up


def _words_to_digits(text: str) -> str:
    """Whisper doesn't reliably write small spoken numbers as digits ("season one
    episode one") -- normalize common ones so the season/episode regex, which
    only looks for digits, still catches them."""
    return re.sub(r"\b[a-zA-Z]+\b", lambda m: _NUMBER_WORDS.get(m.group(0).lower(), m.group(0)), text)


def _clean_title(raw: str) -> str:
    """Extracts just the show/movie title from a scene-release-style filename
    stem: keeps only the tokens before the first season/episode tag, year, or
    resolution/source/codec marker, since everything from there on is metadata
    (and, past that, an arbitrary release-group/uploader tag -- see
    _QUALITY_MARKER_RE for why truncating is more robust than stripping)."""
    s = re.sub(r"[._]", " ", raw)
    s = re.sub(r"[\[\](){}]", " ", s)
    title_tokens: List[str] = []
    for tok in s.split():
        if _SXXEYY_RE.fullmatch(tok) or _YEAR_TOKEN_RE.match(tok) or _QUALITY_MARKER_RE.match(tok):
            break
        title_tokens.append(tok)
    return " ".join(title_tokens).strip()


def _parse_spoken_episode(name: str) -> Optional[Tuple[str, int, int]]:
    """Returns (title, season, episode) for "<title> season X episode Y" (or the
    glued "s1e1" short form), else None if the spoken name doesn't look like a
    TV episode request at all (a plain movie title)."""
    m = _SEASON_EPISODE_WORDS_RE.match(_words_to_digits(name).strip())
    if not m:
        return None
    title = m.group(1).strip()
    return title, int(m.group(2)), int(m.group(3))


def _find_video_file(name: str, location: Optional[str] = None) -> Optional[Path]:
    raw_name = name.strip().rstrip(".,!?;: ")

    if location:
        base = _resolve_location(location)
        search_dirs = [base] if base is not None else []
    else:
        search_dirs = [d for k in _DEFAULT_VIDEO_SEARCH_LOCATIONS if (d := _NAMED_LOCATIONS[k]()).exists()]

    candidates: List[Path] = [
        f
        for directory in search_dirs if directory.exists()
        for f in directory.iterdir()
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
    ]

    episode = _parse_spoken_episode(raw_name)
    if episode is not None:
        title, season, ep = episode
        # (?!\d) instead of \b: real scene-release filenames commonly use "_"
        # as a separator ("...S01E03_720p..."), and "_" counts as a \w
        # character in regex just like a digit does, so \b never actually
        # matches between them -- the episode tag would silently fail to
        # match on any underscore-separated filename. A trailing digit is the
        # only thing that actually needs ruling out (so "e1" doesn't also
        # match inside "e10"); it doesn't care what character, if any, follows.
        tag_pattern = re.compile(rf"s0*{season}e0*{ep}(?!\d)", re.IGNORECASE)
        tagged_matches = [f for f in candidates if tag_pattern.search(f.stem)]
        if len(tagged_matches) == 1:
            return tagged_matches[0]
        if len(tagged_matches) > 1:
            # Multiple files share this exact season/episode tag -- likely a
            # library with lots of shows sharing a folder (e.g. Telegram
            # Desktop's default download folder), not several copies of the
            # requested one. The tag match itself already confirms this is the
            # right episode, so pick whichever title fuzzy-matches best rather
            # than applying VIDEO_MATCH_THRESHOLD below -- there's no genuine
            # "not found" outcome left once the tag has narrowed it this far,
            # and a real title (e.g. "The Diplomat") can still legitimately
            # score under that threshold against its own messier release-group
            # noise ("Cinemagic HD") relative to how cleanly the other 9
            # candidates in the tagged set score against *their* own titles.
            target_lower = (title or raw_name).lower()
            return max(
                tagged_matches,
                key=lambda f: fuzz.token_sort_ratio(target_lower, _clean_title(f.stem).lower()),
            )
        # No file carries this exact tag at all -- maybe it isn't scene-style
        # filenamed; fall through to plain title matching below.
        target = title or raw_name
    else:
        target = raw_name

    target_lower = target.lower()
    best: Optional[Path] = None
    best_score = 0
    for f in candidates:
        stem_lower = f.stem.lower()
        if stem_lower == target_lower or (target_lower and target_lower in stem_lower):
            return f  # exact/substring match -- stop looking immediately
        score = fuzz.token_sort_ratio(target_lower, _clean_title(f.stem).lower())
        if score > best_score:
            best, best_score = f, score

    return best if best_score >= VIDEO_MATCH_THRESHOLD else None


def play_video(name: str, location: Optional[str] = None) -> str:
    match = resolve_app_path("vlc")
    if not match:
        msg = "VLC isn't installed (or isn't discoverable) on this machine."
        log.warning(msg)
        raise ActionError(msg)
    _, vlc_target = match

    video = _find_video_file(name, location)
    if video is None:
        where = f" in {location}" if location else " in Videos, Downloads, Desktop, Documents, or your home folder"
        msg = f"Couldn't find a video matching '{name}'{where}."
        log.warning(msg)
        raise ActionError(msg)

    try:
        subprocess.Popen([vlc_target, str(video)])
        msg = f"Playing {video.name} in VLC."
        log.info(msg)
        return msg
    except Exception as e:
        msg = f"Failed to open {video.name} in VLC: {e}"
        log.error(msg)
        raise ActionError(msg)

    msg = f"Created folder '{folder_name}' in {base}."
    log.info(msg)
    return msg


def list_known_apps() -> str:
    from assistant.app_discovery import discover_apps

    names = sorted(set(discover_apps().keys()) | set(config.aliases.keys()))
    return "Known apps:\n" + "\n".join(f"  - {n}" for n in names) if names else "No apps known yet."
