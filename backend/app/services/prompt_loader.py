"""Load versioned prompt templates from disk and cache them.

Prompts live under `backend/prompts/<version>/<name>.txt`. They are loaded
verbatim and rendered with {placeholders} using str.format_map with a
safe dict that ignores missing keys.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
DEFAULT_VERSION = "v1"


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def load_raw(name: str, version: str = DEFAULT_VERSION) -> str:
    """Load a prompt template, bypassing cache so file edits are always picked up."""
    path = PROMPTS_DIR / version / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def render(name: str, version: str = DEFAULT_VERSION, **substitutions: str) -> str:
    raw = load_raw(name, version)
    if not substitutions:
        return raw
    return raw.format_map(_SafeDict(**substitutions))
