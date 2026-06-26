"""Tests for paragraph→block conversion + invariant split/merge."""

from __future__ import annotations

from app.schemas.block import INVARIANT_TYPES
from app.services.invariant_splitter import (
    merge_blocks_in_order,
    paragraphs_to_blocks,
    split_blocks,
)


def test_paragraphs_to_blocks_type_mapping() -> None:
    paragraphs = [
        {"type": "body", "content": "intro"},
        {"type": "heading", "content": "Sub"},
        {"type": "equation", "content": "E = mc^2"},
        {"type": "definition", "term": "X", "content": "meaning"},
        {"type": "key_point", "content": "note"},
        {"type": "figure", "content": "Fig 5.1 — diagram"},
        {"type": "example", "label": "Ex", "prob": "P", "eqs": ["a=b"]},
    ]
    blocks = paragraphs_to_blocks(paragraphs)
    types = [b["t"] for b in blocks]
    assert types == ["p", "h3", "eq", "def", "kp", "fig", "example"]


def test_consecutive_list_items_are_merged() -> None:
    paragraphs = [
        {"type": "body", "content": "before"},
        {"type": "list_item", "content": "one"},
        {"type": "list_item", "content": "two"},
        {"type": "list_item", "content": "three"},
        {"type": "body", "content": "after"},
    ]
    blocks = paragraphs_to_blocks(paragraphs)
    types = [b["t"] for b in blocks]
    assert types == ["p", "list", "p"]
    assert blocks[1]["items"] == ["one", "two", "three"]


def test_split_blocks_preserves_order() -> None:
    blocks = [
        {"t": "p", "c": "a"},
        {"t": "eq", "c": "x=1"},
        {"t": "p", "c": "b"},
        {"t": "def", "term": "X", "c": "y"},
        {"t": "kp", "c": "c"},
    ]
    inv, free = split_blocks(blocks)
    assert [b["t"] for b in inv] == ["eq", "def"]
    assert [b["t"] for b in free] == ["p", "p", "kp"]
    for b in inv:
        assert b["t"] in INVARIANT_TYPES


def test_merge_preserves_original_order_with_invariants_verbatim() -> None:
    original = [
        {"t": "p", "c": "Original 1"},
        {"t": "eq", "c": "E = mc^2"},
        {"t": "p", "c": "Original 2"},
    ]
    regen_free = [
        {"t": "p", "c": "Rewritten 1"},
        {"t": "p", "c": "Rewritten 2"},
    ]
    merged = merge_blocks_in_order(original, regen_free)
    assert merged[0]["c"] == "Rewritten 1"
    assert merged[1]["c"] == "E = mc^2"  # invariant preserved
    assert merged[2]["c"] == "Rewritten 2"


def test_merge_appends_leftover_regen_blocks() -> None:
    original = [{"t": "p", "c": "A"}, {"t": "eq", "c": "x=1"}]
    regen_free = [
        {"t": "p", "c": "A'"},
        {"t": "p", "c": "extra"},
    ]
    merged = merge_blocks_in_order(original, regen_free)
    assert [b["t"] for b in merged] == ["p", "eq", "p"]
    assert merged[2]["c"] == "extra"


def test_merge_drops_free_slots_when_regen_returns_fewer() -> None:
    original = [
        {"t": "p", "c": "A"},
        {"t": "eq", "c": "x=1"},
        {"t": "p", "c": "B"},
    ]
    regen_free = [{"t": "p", "c": "A'"}]
    merged = merge_blocks_in_order(original, regen_free)
    # Missing second free slot simply has a gap
    assert [b["t"] for b in merged] == ["p", "eq"]
