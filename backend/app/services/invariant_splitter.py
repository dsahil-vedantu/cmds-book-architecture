"""Convert P4 paragraph dicts into canonical Block dicts.

P4 returns paragraphs with type values like "body", "heading", etc. The stored
Block schema uses short codes: "p", "h3", "eq", "def", "kp", "fig", "list",
"example". This module translates between the two.

Consecutive list_item paragraphs are consolidated into a single ListBlock.

Invariant split (INVARIANT_TYPES never sent to Claude during regeneration) is
provided here too — see split_blocks() / merge_blocks_in_order().
"""

from __future__ import annotations

from app.schemas.block import INVARIANT_TYPES

_TYPE_MAP = {
    "body": "p",
    "heading": "h3",
    "equation": "eq",
    "definition": "def",
    "key_point": "kp",
    "figure": "fig",
    "list_item": "list_item",  # handled specially (merged into ListBlock)
    "table": "table",
    "example": "example",
    "example_ref": "example_ref",
    "exercise_ref": "exercise_ref",
    "question_ref": "question_ref",
}

_REF_TYPES = {"example_ref", "exercise_ref", "question_ref"}


def paragraphs_to_blocks(paragraphs: list[dict]) -> list[dict]:
    """Convert P4-style paragraphs into canonical Block dicts.

    Unknown types are dropped (defensive). Consecutive list_items are
    consolidated into a single ``{"t": "list", "items": [...]}`` block.
    """
    blocks: list[dict] = []
    list_buffer: list[str] = []

    def flush_list() -> None:
        if list_buffer:
            blocks.append({"t": "list", "items": list(list_buffer)})
            list_buffer.clear()

    for p in paragraphs or []:
        ptype = (p.get("type") or p.get("t") or "").strip()
        short = _TYPE_MAP.get(ptype)
        if short is None:
            continue

        if short == "list_item":
            content = (p.get("content") or p.get("c") or "").strip()
            if content:
                list_buffer.append(content)
            continue

        flush_list()

        if short == "def":
            term = (p.get("term") or "").strip()
            c = (p.get("content") or p.get("c") or "").strip()
            if not c:
                continue
            blocks.append({"t": "def", "term": term, "c": c})
        elif short == "table":
            blocks.append(
                {
                    "t": "table",
                    "caption": (p.get("caption") or "").strip(),
                    "headers": list(p.get("headers") or []),
                    "rows": list(p.get("rows") or []),
                }
            )
        elif short == "example":
            blocks.append(
                {
                    "t": "example",
                    "label": (p.get("label") or "").strip(),
                    "prob": (p.get("prob") or "").strip(),
                    "eqs": list(p.get("eqs") or []),
                }
            )
        elif short in _REF_TYPES:
            label = (p.get("label") or "").strip()
            number = (p.get("number") or "").strip()
            if not label and not number:
                # placeholder with no identifier — drop defensively
                continue
            blocks.append(
                {
                    "t": short,
                    "label": label,
                    "number": number,
                }
            )
        elif short == "fig":
            # extractor_v2 emits {label, caption}; extractor v1 emits {content}.
            # Accept all three field names so a prompt swap is backward-compatible.
            c = (p.get("caption") or p.get("content") or p.get("c") or "").strip()
            label = (p.get("label") or "").strip()
            # accept empty content if a label is present (pure label placeholder)
            if not c and not label:
                continue
            blocks.append({"t": "fig", "c": c, "label": label})
        else:
            c = (p.get("content") or p.get("c") or "").strip()
            if c:
                blocks.append({"t": short, "c": c})

    flush_list()
    return blocks


def split_blocks(
    blocks: list[dict],
    protected_types: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Return (invariant_blocks, free_blocks) preserving order.

    ``protected_types`` is the set of block types copied verbatim (the
    "invariant" bucket). Defaults to the canonical INVARIANT_TYPES. The regen
    path passes a narrowed set (INVARIANT_TYPES minus "def") so definition
    bodies get rewritten while equations/figures/examples stay protected.
    """
    protected = INVARIANT_TYPES if protected_types is None else protected_types
    invariant = [b for b in blocks if b.get("t") in protected]
    free = [b for b in blocks if b.get("t") not in protected]
    return invariant, free


def merge_blocks_in_order(
    original_blocks: list[dict],
    regenerated_free_blocks: list[dict],
    protected_types: set[str] | None = None,
) -> list[dict]:
    """Walk original blocks; at each position copy invariants verbatim and
    pull in order from ``regenerated_free_blocks`` for free slots.

    ``protected_types`` must match the set passed to ``split_blocks`` for this
    regen run so the free/invariant classification is consistent on both
    sides. Defaults to the canonical INVARIANT_TYPES.

    Defensive fallback: if the LLM under-produced free blocks (e.g. collapsed
    multiple body paragraphs into one), the unfilled free slots now fall
    back to the ORIGINAL block at that position instead of being silently
    dropped. Without this fallback, missing free slots caused invariant
    blocks (equations, figures) to visually cluster at the end of the
    section, which reviewers reported as "equations dumped at end".

    Leftover regen blocks (LLM over-produced) are still appended at the end
    so nothing is lost; usually this combined with the prompt's block-count
    rule means the leftover list is empty in practice.
    """
    protected = INVARIANT_TYPES if protected_types is None else protected_types
    merged: list[dict] = []
    free_idx = 0
    for orig in original_blocks:
        if orig.get("t") in protected:
            merged.append(dict(orig))
        else:
            if free_idx < len(regenerated_free_blocks):
                merged.append(regenerated_free_blocks[free_idx])
                free_idx += 1
            else:
                # Defensive: regen under-produced → keep the original block
                # at this position. Reviewer sees the original prose for
                # this slot rather than the slot being silently dropped.
                merged.append(dict(orig))
    while free_idx < len(regenerated_free_blocks):
        merged.append(regenerated_free_blocks[free_idx])
        free_idx += 1
    return merged
