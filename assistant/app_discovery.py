"""Auto-discovers installed apps by scanning Start Menu .lnk shortcuts, so "open spotify"
works without hand-writing a path for every app on the machine. User aliases in
config/apps.yaml always win over what's discovered here (see resolver.resolve_app).

Results are cached in-process for the life of the run; call discover_apps(force=True)
to rescan (e.g. after installing something new) without restarting the assistant.
"""

from pathlib import Path
from typing import Dict, Optional

from assistant.logger import get_logger

log = get_logger(__name__)

START_MENU_DIRS = [
    Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs",
    Path("C:/ProgramData/Microsoft/Windows/Start Menu/Programs"),
]

# Microsoft Store apps (Spotify, WhatsApp, etc. installed via the Store rather than a
# traditional installer) don't get a .lnk in the Start Menu -- they register an "app
# execution alias" here instead, a stub .exe with the app's display name.
WINDOWS_APPS_DIR = Path.home() / "AppData/Local/Microsoft/WindowsApps"

_cache: Optional[Dict[str, str]] = None


def _resolve_shortcut(lnk_path: Path) -> Optional[str]:
    try:
        import win32com.client  # pywin32

        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(lnk_path))
        target = shortcut.Targetpath
        return target if target else None
    except Exception as e:
        log.debug("Could not resolve shortcut %s: %s", lnk_path, e)
        return None


def discover_apps(force: bool = False) -> Dict[str, str]:
    """Returns {app_name_lowercase: target_path}."""
    global _cache
    if _cache is not None and not force:
        return _cache

    apps: Dict[str, str] = {}
    for base in START_MENU_DIRS:
        if not base.exists():
            continue
        for lnk in base.rglob("*.lnk"):
            target = _resolve_shortcut(lnk)
            if not target:
                continue
            name = lnk.stem.lower()
            apps[name] = target

    if WINDOWS_APPS_DIR.exists():
        for exe in WINDOWS_APPS_DIR.glob("*.exe"):
            # Skip the generic launcher stubs Windows also drops in this folder.
            if exe.stem.lower().endswith("_installer"):
                continue
            apps.setdefault(exe.stem.lower(), str(exe))

    log.info("Discovered %d apps from Start Menu shortcuts + WindowsApps aliases", len(apps))
    _cache = apps
    return apps
