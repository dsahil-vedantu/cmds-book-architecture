"""Sanity checks for the Block union — ensures the canonical content model
deserialises correctly via the discriminator."""

from __future__ import annotations

from pydantic import TypeAdapter

from app.schemas.block import (
    FREE_TYPES,
    INVARIANT_TYPES,
    Block,
    DefinitionBlock,
    EquationBlock,
    ParagraphBlock,
)

BlockAdapter = TypeAdapter(Block)


def test_paragraph_roundtrip() -> None:
    b = BlockAdapter.validate_python({"t": "p", "c": "Hello"})
    assert isinstance(b, ParagraphBlock)
    assert b.c == "Hello"


def test_equation_is_invariant() -> None:
    b = BlockAdapter.validate_python({"t": "eq", "c": "E = mc^2"})
    assert isinstance(b, EquationBlock)
    assert b.t in INVARIANT_TYPES


def test_definition_fields() -> None:
    b = BlockAdapter.validate_python(
        {"t": "def", "term": "Work Function", "c": "Minimum energy..."}
    )
    assert isinstance(b, DefinitionBlock)
    assert b.term == "Work Function"


def test_invariant_and_free_types_are_disjoint() -> None:
    assert INVARIANT_TYPES.isdisjoint(FREE_TYPES)
    assert INVARIANT_TYPES == {"eq", "def", "fig", "example"}
    assert FREE_TYPES == {"p", "h3", "kp", "list"}
