"""Canonical block → searchable text extraction.

Single source of truth for "what's searchable in any block." Used by
figure section resolver, future search features, etc.

Design principle: every string-valued field in a block contributes to
the searchable text. No per-type logic; new block types automatically
work without code changes. This avoids the recurring bug where a new
block field is added but the resolver's per-type flattener forgets to
include it.

Used by:
  - app/services/figure_section_resolver.py — matches anchor text to
    sections by searching across all theory blocks.
"""
from __future__ import annotations

from typing import Any


# Keys that are metadata, not searchable text. Skip these when collecting strings.
_NON_TEXT_KEYS = frozenset({
    "t",                  # block type marker
    "section_id",         # cross-references, not content
    "section_uuid",
    "question_id",
    "number",             # numeric identifiers like "5.2", not narrative
    "block_idx",
    "placement_block_idx",
    "ref_id",
    "figure_id",
})


def block_to_searchable_text(block: Any) -> str:
    """Extract all searchable text from a block dict (or any value).

    Recursively walks dicts + lists, collects every string value.
    Non-text keys (block type marker, FK ids, numeric identifiers) are
    skipped.

    Returns a single string with all extracted text joined by spaces.

    Examples:
        {"t": "def", "term": "Line", "c": "A line is a set..."}
          → "Line A line is a set..."

        {"t": "example", "label": "Example 5.1", "prob": "Solve 2x+3=7",
         "eqs": ["2x = 4", "x = 2"], "sol": "Subtract 3 from both sides"}
          → "Example 5.1 Solve 2x+3=7 2x = 4 x = 2 Subtract 3 from both sides"

        {"t": "table", "headers": ["x", "y"], "rows": [["1", "2"], ["3", "4"]]}
          → "x y 1 2 3 4"

        {"t": "p", "c": "Plain paragraph text."}
          → "Plain paragraph text."
    """
    if isinstance(block, str):
        return block.strip()
    if not isinstance(block, dict):
        return ""

    parts: list[str] = []
    for key, val in block.items():
        if key in _NON_TEXT_KEYS:
            continue
        parts.extend(_collect_strings(val))
    return " ".join(p for p in parts if p)


def _collect_strings(val: Any) -> list[str]:
    """Recursively collect all string values from any nested structure."""
    if val is None:
        return []
    if isinstance(val, str):
        s = val.strip()
        return [s] if s else []
    if isinstance(val, (int, float, bool)):
        return []  # numeric values are not narrative text
    if isinstance(val, list):
        out: list[str] = []
        for item in val:
            out.extend(_collect_strings(item))
        return out
    if isinstance(val, dict):
        out = []
        for k, v in val.items():
            if k in _NON_TEXT_KEYS:
                continue
            out.extend(_collect_strings(v))
        return out
    return []


def section_blocks_text(blocks: Any) -> str:
    """Flatten an entire section's blocks list into one searchable text string.

    Convenience wrapper around block_to_searchable_text for whole-section
    searches. Blocks that aren't dicts are skipped silently.
    """
    if not blocks:
        return ""
    parts: list[str] = []
    for b in blocks:
        text = block_to_searchable_text(b)
        if text:
            parts.append(text)
    return "\n".join(parts)


__all__ = ["block_to_searchable_text", "section_blocks_text"]
