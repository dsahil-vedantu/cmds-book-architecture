"""Content-stream resolver — single source of positional truth.

Figure position in theory is stored as a coordinate
``(placement_block_idx, placement_char_offset)`` into a section's flat block
array. Historically that coordinate was decoded in *several* places that
disagreed:

  - ``seed_draft_items_from_merge`` used ``block_idx`` only and IGNORED
    ``char_offset`` → figures that belong *between* list items were stacked
    after the whole list (Composer / Preview / exports).
  - ``TheoryView.ListWithInlineFigures`` (frontend) reconstructed the
    interleave from ``char_offset`` at render time → RegenReview looked right
    but disagreed with Composer / Preview.

This module makes the decode happen **once**, on the backend, producing an
ordered list of atomic stream nodes. Every consumer (the seeder → Composer /
Preview / exports, and ultimately RegenReview) renders that pre-resolved
stream and never re-interprets the coordinate again.

The function is PURE and deterministic so it can be golden-diffed against the
current behaviour across every book before any consumer is cut over.

Node shape (intentionally minimal; the seeder wraps these into draft items):

    {"kind": "block",  "block": <block dict>}
    {"kind": "figure", "figure": <figure dict>}

Scope of behavioural change vs. today's seeder (deliberately tight):
  - LIST block + ≥1 figure carrying a numeric ``placement_char_offset``
        → the list is split at item boundaries and figures interleave
          between the items, matching ``TheoryView.ListWithInlineFigures``.
  - EVERYTHING ELSE (non-list blocks, lists whose figures have no numeric
        offset) → byte-identical to today: the block, then its figures.

So for the overwhelming majority of (block, figures) pairs the resolver
reproduces the current stream exactly; only the sub-unit-list case changes.
"""

from __future__ import annotations

from typing import Any

# Node kinds
BLOCK = "block"
FIGURE = "figure"


def _is_list_block(block: dict[str, Any]) -> bool:
    return isinstance(block, dict) and block.get("t") == "list"


def _list_item_boundaries(items: list[str]) -> list[int]:
    """Cumulative char length of items[0..k] joined by "\\n".

    Identical math to the backend embedder's ``sub_char_offset`` computation
    (``len("\\n".join(items[:upto]))``) and the frontend ``ListWithInlineFigures``
    boundary loop, so a figure's ``char_offset`` maps to the same item in all
    three places.
    """
    boundary: list[int] = []
    acc = 0
    for k, it in enumerate(items):
        acc += (1 if k > 0 else 0) + len(it or "")
        boundary.append(acc)
    return boundary


def _item_index_for_offset(boundary: list[int], offset: int) -> int:
    """Smallest k with boundary[k] >= offset; last item if none.

    Mirrors ``boundary.findIndex(bnd => bnd >= off)`` in TheoryView (with the
    same ``< 0 → last`` fallback). The figure attaches AFTER item ``k``.
    """
    for k, bnd in enumerate(boundary):
        if bnd >= offset:
            return k
    return len(boundary) - 1 if boundary else 0


def _split_list_block(
    block: dict[str, Any],
    figs_with_offset: list[dict[str, Any]],
    figs_without_offset: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Split one list block into ordered nodes, interleaving offset figures.

    The list items keep their original text (including any "i./ii." ordinal
    prefix), so a downstream renderer can rejoin consecutive list sub-blocks
    into one continuous <ol>. Each emitted sub-block carries grouping metadata:

        _split_of   : the original block's identity marker (id() is unstable
                      across processes, so we use a caller-supplied tag set by
                      ``resolve_block_figures`` via the block's existing keys)
        _split_part : 0-based part index within the group
        _split_last : True on the final part (renderer ends the <ol> here)

    Figures WITHOUT a numeric offset attach after the whole list (matching
    TheoryView, which renders offsetless figs separately after the list).
    """
    items: list[str] = list(block.get("items") or [])
    if not items:
        # Degenerate list — fall back to block-then-figs.
        out: list[dict[str, Any]] = [{"kind": BLOCK, "block": block}]
        for f in figs_with_offset + figs_without_offset:
            out.append({"kind": FIGURE, "figure": f})
        return out

    boundary = _list_item_boundaries(items)

    # Bucket offset figures by the item they follow. Preserve input order
    # within a bucket (stable).
    figs_by_item: dict[int, list[dict[str, Any]]] = {}
    for f in figs_with_offset:
        off = f.get("placement_char_offset")
        try:
            off_i = int(off)
        except (TypeError, ValueError):
            off_i = 0
        k = _item_index_for_offset(boundary, off_i)
        figs_by_item.setdefault(k, []).append(f)

    # Build parts: a part is a run of items with no interior figure, ended by
    # the item after which one or more figures attach (or the list end).
    split_after = sorted(figs_by_item.keys())
    nodes: list[dict[str, Any]] = []
    group_tag = (
        block.get("_block_uid")
        or block.get("id")
        or f"list@{boundary[-1]}:{len(items)}"
    )
    part = 0
    start = 0
    # Boundaries to cut after: every item index that has a figure, plus the
    # final item (so trailing items form a part). Use a sorted unique set.
    cut_points = sorted(set(split_after) | {len(items) - 1})
    for ci, cut in enumerate(cut_points):
        sub_items = items[start : cut + 1]
        is_last = ci == len(cut_points) - 1
        sub_block = {
            **block,
            "items": sub_items,
            "_split_of": group_tag,
            "_split_part": part,
            "_split_last": is_last,
            # 1-based ordinal of this part's first item within the original
            # list — lets the renderer continue <ol> numbering across the
            # split (so parts read as one list, not N lists restarting at 1).
            "_split_start": start + 1,
        }
        nodes.append({"kind": BLOCK, "block": sub_block})
        for f in figs_by_item.get(cut, []):
            nodes.append({"kind": FIGURE, "figure": f})
        start = cut + 1
        part += 1

    # Offsetless figures after the whole list.
    for f in figs_without_offset:
        nodes.append({"kind": FIGURE, "figure": f})
    return nodes


def resolve_block_figures(
    block: dict[str, Any],
    figures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve one block + the figures anchored at its index into ordered nodes.

    ``figures`` must be exactly those whose ``placement_block_idx`` equals this
    block's index (the seeder already buckets by index).

    Returns ``[{"kind": "block"|"figure", ...}]`` in render order.
    """
    figs = list(figures or [])

    if not _is_list_block(block):
        # Non-list: today's behaviour exactly — block, then its figures.
        out = [{"kind": BLOCK, "block": block}]
        for f in figs:
            out.append({"kind": FIGURE, "figure": f})
        return out

    figs_with_offset = [
        f for f in figs if isinstance(f.get("placement_char_offset"), (int, float))
    ]
    figs_without_offset = [
        f
        for f in figs
        if not isinstance(f.get("placement_char_offset"), (int, float))
    ]

    if not figs_with_offset:
        # List but no interior figures → unchanged: block, then figures.
        out = [{"kind": BLOCK, "block": block}]
        for f in figs:
            out.append({"kind": FIGURE, "figure": f})
        return out

    return _split_list_block(block, figs_with_offset, figs_without_offset)
