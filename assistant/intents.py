"""A tiny regex-based intent registry. Phase 1-3 use this directly: text in, regex
match, handler called. Phase 4 (LLM tool-calling) doesn't replace this -- it sits in
front of it, deciding *which* underlying action (in actions.py) to call for open-ended
commands the regexes here don't cover, while these patterns keep handling the fast,
common, "just do the obvious thing" cases without waiting on an LLM round-trip.

Registration order matters: patterns are tried in the order they were registered, so
specific patterns (registered first in executor.py) win over generic catch-alls
(registered last).
"""

import re
from typing import Callable, List, Optional, Pattern, Tuple

_INTENTS: List[Tuple[Pattern, Callable]] = []


def intent(pattern: str):
    compiled = re.compile(pattern, re.IGNORECASE)

    def decorator(fn: Callable) -> Callable:
        _INTENTS.append((compiled, fn))
        return fn

    return decorator


def match(text: str) -> Tuple[Optional[Callable], Optional[Tuple[str, ...]]]:
    stripped = text.strip()
    for pattern, fn in _INTENTS:
        m = pattern.match(stripped)
        if m:
            return fn, m.groups()
    return None, None


def registered_patterns() -> List[str]:
    return [p.pattern for p, _ in _INTENTS]
