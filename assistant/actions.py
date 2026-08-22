"""The actual doing. Every function here is a leaf action: open something, close
something, run something. Nothing in this module parses intent -- that's executor.py's
job -- so these are also exactly what a Phase 4 LLM tool-calling backend would call
directly as its "tools".

Each action returns a plain string result message rather than printing, so both the
CLI and (later) a voice/TTS front-end can consume the same functions.
"""

import os
import shlex
import shutil
import subprocess
import webbrowser
from pathlib import Path
from typing import Callable, List, Optional, Set

import psutil
import win32api
import win32con
import win32gui
import win32process

from assistant.config import config
from assistant.resolver import resolve_app_path
from assistant.logger import get_logger

log = get_logger(__name__)

ConfirmFn = Callable[[str], bool]

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


def open_app(name: str) -> str:
    match = resolve_app_path(name)
    if not match:
        msg = f"Couldn't find an app matching '{name}'."
        log.warning(msg)
        return msg

    matched_name, target = match

    proc_stem = Path(target).stem if os.path.exists(target) else matched_name
    pids = _pids_for_process_names([proc_stem, matched_name])
    if pids:
        hwnds = _visible_windows_for_pids(pids)
        if hwnds:
            _focus_window(hwnds[0])
            msg = f"{matched_name} is already open -- switching to it."
        else:
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
        return msg


def close_app(name: str, confirm: ConfirmFn = _default_confirm) -> str:
    name_lower = name.strip().rstrip(".,!?;: ").lower()
    protected = config.protected_processes

    matches: List[psutil.Process] = []
    for proc in psutil.process_iter(["pid", "name"]):
        pname = (proc.info.get("name") or "").lower()
        pname_stem = pname[:-4] if pname.endswith(".exe") else pname
        if name_lower in (pname, pname_stem) or name_lower in pname_stem:
            matches.append(proc)

    if not matches:
        msg = f"No running process matching '{name}'."
        log.info(msg)
        return msg

    blocked = [p for p in matches if (p.info.get("name") or "").lower() in protected]
    killable = [p for p in matches if p not in blocked]

    if blocked and not killable:
        msg = f"'{name}' matches a protected system process ({blocked[0].info['name']}); refusing to close it."
        log.warning(msg)
        return msg

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
    pids = _pids_for_process_names([name])
    if not pids:
        msg = f"No running process matching '{name}'."
        log.info(msg)
        return msg

    hwnds = _visible_windows_for_pids(pids)
    if not hwnds:
        msg = f"'{name}' is running but has no window to minimize."
        log.info(msg)
        return msg

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
        return msg

    # --reuse-window: without it, the VS Code CLI opens a brand new window on
    # every single invocation, even for the exact same folder -- this is what
    # was causing "open vscode" to pile up extra windows instead of just
    # bringing the existing one forward.
    args = [code_exe, "--reuse-window"]
    if goto:
        args += ["--goto", goto]
    else:
        args.append(path)

    try:
        subprocess.Popen(args, shell=True)
        msg = f"Opened VS Code at {goto or path}."
        log.info(msg)
        return msg
    except Exception as e:
        msg = f"Failed to open VS Code: {e}"
        log.error(msg)
        return msg


def run_script(name: str, confirm: ConfirmFn = _default_confirm) -> str:
    """Runs a command from the config.scripts whitelist only -- arbitrary shell
    execution from transcribed speech is refused by design."""
    scripts = config.scripts
    key = name.strip().lower()
    command = scripts.get(key)

    if not command:
        msg = f"'{name}' isn't a whitelisted script. Add it to config/apps.yaml under 'scripts' first."
        log.warning(msg)
        return msg

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
        return msg
    except Exception as e:
        msg = f"Failed to run '{key}': {e}"
        log.error(msg)
        return msg


def open_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    msg = f"Opened {url}."
    log.info(msg)
    return msg


def open_folder(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        msg = f"Path does not exist: {p}"
        log.warning(msg)
        return msg
    os.startfile(str(p))
    msg = f"Opened folder {p}."
    log.info(msg)
    return msg


def list_known_apps() -> str:
    from assistant.app_discovery import discover_apps

    names = sorted(set(discover_apps().keys()) | set(config.aliases.keys()))
    return "Known apps:\n" + "\n".join(f"  - {n}" for n in names) if names else "No apps known yet."
