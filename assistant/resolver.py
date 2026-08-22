"""Turns a spoken/typed app name into something actionable: a path to launch or a
process name to match against. Checks user aliases first (config/apps.yaml), then
auto-discovered Start Menu shortcuts, then falls back to fuzzy matching across both --
this is what keeps "open crome" or "open google chrome" working even when the exact
alias is "chrome", since Whisper transcriptions in Phase 2+ won't always be exact.
"""

from typing import Dict, Optional, Tuple

from rapidfuzz import process, fuzz

from assistant.config import config
from assistant.app_discovery import discover_apps
from assistant.logger import get_logger

log = get_logger(__name__)

FUZZY_THRESHOLD = 72


def _all_candidates() -> Dict[str, str]:
    """Merged name->target map, aliases take priority over discovered apps.

    An alias's value can be either a real path/command ("code") or the *name* of
    an already-discovered app ("google chrome") -- the latter lets config/apps.yaml
    define a friendly short name ("chrome") without hardcoding a machine-specific
    install path, since the real path is resolved from discovery at lookup time.
    """
    discovered = discover_apps()
    merged = dict(discovered)
    for alias, target in config.aliases.items():
        target_key = target.strip().lower()
        merged[alias] = discovered.get(target_key, target)
    return merged


def resolve_app_path(spoken_name: str) -> Optional[Tuple[str, str]]:
    """Returns (matched_name, target) or None if nothing close enough was found."""
    name = spoken_name.strip().lower()
    candidates = _all_candidates()

    if name in candidates:
        return name, candidates[name]

    match = process.extractOne(name, candidates.keys(), scorer=fuzz.WRatio)
    if match and match[1] >= FUZZY_THRESHOLD:
        matched_name = match[0]
        log.debug("Fuzzy matched '%s' -> '%s' (score %.0f)", name, matched_name, match[1])
        return matched_name, candidates[matched_name]

    return None
