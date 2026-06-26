"""Deterministic removal of puzzle/word-game sections from a schema.

Per `prompts/v2/schema_architecture.txt §5.9`, crosswords, word
searches, sudoku, jumbles, riddles, brain teasers are Cat C inline
callouts — NEVER their own section. The validator flags them with
`PUZZLE_AS_SECTION` (error) and the corrective retry tries to remove
them. This sanitizer auto-fixes the issue before validation runs so
we don't burn retries on a deterministic problem.

Idempotent — running twice yields the same result.
"""

from __future__ import annotations

# Imports the same regex the validator uses so the two stay in sync.
from app.services.schema_validator import _PUZZLE_TITLE_RE


def _is_puzzle_title(title: str | None) -> bool:
    if not title:
        return False
    return bool(_PUZZLE_TITLE_RE.search(title.strip()))


def _filter_tree(nodes: list, removed: list[str]) -> list:
    """Return a new list with puzzle nodes removed at every depth."""
    out = []
    for n in nodes or []:
        if not isinstance(n, dict):
            out.append(n)
            continue
        title = (n.get("title") or "").strip()
        if _is_puzzle_title(title):
            removed.append(title)
            continue
        # Recurse into subsections — drop puzzle descendants too.
        subs = n.get("subsections") or []
        if subs:
            n["subsections"] = _filter_tree(subs, removed)
        out.append(n)
    return out


def remove_puzzle_sections(schema_dict: dict) -> tuple[dict, int, list[str]]:
    """Remove every puzzle-titled section/excluded entry, recursively.

    Returns `(schema_dict, n_removed, removed_titles)`. Mutates in place;
    also returned for caller convenience.
    """
    if not isinstance(schema_dict, dict):
        return schema_dict, 0, []
    removed: list[str] = []
    schema_dict["sections"] = _filter_tree(
        schema_dict.get("sections") or [], removed
    )
    schema_dict["excluded_sections"] = _filter_tree(
        schema_dict.get("excluded_sections") or [], removed
    )
    return schema_dict, len(removed), removed


__all__ = ["remove_puzzle_sections"]
