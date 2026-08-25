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
        # WRatio's partial-ratio component scores any substring match as
        # near-perfect, even one that lands in the middle of an unrelated word --
        # "the password" scores 90 against the app "word" purely because
        # "password" happens to contain "word", which is exactly what let a
        # misheard fragment of background conversation (nobody said the wake
        # word) open Microsoft Word. Requiring the match to also clear the bar on
        # either plain character-ratio (keeps typo/merged-word matches like
        # "vs code" -> "vscode") or whole-token-set ratio (keeps "google chrome"
        # -> "chrome", "microsoft word" -> "word") rejects matches that only
        # exist because of an accidental substring, without losing the fuzzy
        # matches that are actually meant to work.
        if fuzz.ratio(name, matched_name) >= FUZZY_THRESHOLD or fuzz.token_set_ratio(name, matched_name) >= FUZZY_THRESHOLD:
            log.debug("Fuzzy matched '%s' -> '%s' (score %.0f)", name, matched_name, match[1])
            return matched_name, candidates[matched_name]

    return None
