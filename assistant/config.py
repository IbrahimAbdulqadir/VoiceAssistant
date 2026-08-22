"""Loads config/apps.yaml: user-defined app aliases, the protected-process blocklist,
and the whitelist of runnable scripts. This is the one file a user is expected to
hand-edit, so it's kept plain YAML with comments rather than folded into Python."""

from pathlib import Path
from typing import Any, Dict

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
APPS_FILE = CONFIG_DIR / "apps.yaml"

_DEFAULT_CONFIG: Dict[str, Any] = {
    "aliases": {},
    "protected_processes": [],
    "scripts": {},
}


class Config:
    def __init__(self, path: Path = APPS_FILE):
        self.path = path
        self._data = _DEFAULT_CONFIG.copy()
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        with open(self.path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        merged = _DEFAULT_CONFIG.copy()
        merged.update(loaded)
        self._data = merged

    @property
    def aliases(self) -> Dict[str, str]:
        return {k.lower(): v for k, v in (self._data.get("aliases") or {}).items()}

    @property
    def protected_processes(self) -> set:
        return {p.lower() for p in (self._data.get("protected_processes") or [])}

    @property
    def scripts(self) -> Dict[str, str]:
        return {k.lower(): v for k, v in (self._data.get("scripts") or {}).items()}


config = Config()
