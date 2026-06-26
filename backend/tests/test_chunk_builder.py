"""Tests for chunk builder (section chunking + boundary validation)."""

from __future__ import annotations

from app.schemas.analyser import BookSchema, SchemaSection
from app.services.chunk_builder import (
    build_chunks,
    flatten_sections,
    get_token_budget,
)


def _schema() -> BookSchema:
    return BookSchema(
        document_title="Physics Chapter",
        subject="Physics",
        sections=[
            SchemaSection(
                id="5",
                level=1,
                title="Photoelectric Effect",
                type="chapter",
                subsections=[
                    SchemaSection(
                        id="5.1",
                        level=2,
                        title="Introduction",
                        type="section",
                        subsections=[],
                    ),
                    SchemaSection(
                        id="5.2",
                        level=2,
                        title="Einstein's Explanation",
                        type="section",
                        subsections=[],
                    ),
                    SchemaSection(
                        id="5.3",
                        level=2,
                        title="Exercises",
                        type="excluded",
                        subsections=[],
                    ),
                ],
            )
        ],
    )


RAW = """\
5 Photoelectric Effect

This chapter introduces the photoelectric effect and its importance.

5.1 Introduction
The photoelectric effect is the emission of electrons when light hits a metal.
It was first observed by Hertz in 1887.

5.2 Einstein's Explanation
Einstein proposed that light consists of photons with energy E = hf.
The work function for sodium is 2.3 eV.

5.3 Exercises
Problem 1. Compute the kinetic energy ...
"""


def test_flatten_sections_skips_excluded() -> None:
    schema = _schema()
    flat = flatten_sections(schema)
    ids = [s.id for s in flat]
    assert "5.3" not in ids  # excluded
    assert ids == ["5", "5.1", "5.2"]


def test_build_chunks_returns_one_chunk_per_non_excluded_section() -> None:
    chunks = build_chunks(RAW, _schema())
    ids = [c.section_id for c in chunks]
    assert ids == ["5", "5.1", "5.2"]


def test_chunk_contains_its_own_body_text() -> None:
    chunks = {c.section_id: c for c in build_chunks(RAW, _schema())}
    assert "photoelectric effect" in chunks["5.1"].text.lower()
    assert "E = hf" in chunks["5.2"].text
    assert "2.3 eV" in chunks["5.2"].text


def test_chunk_does_not_leak_next_section() -> None:
    chunks = {c.section_id: c for c in build_chunks(RAW, _schema())}
    # 5.2's text should stop before the 5.3 Exercises section
    assert "Problem 1" not in chunks["5.2"].text


def test_missing_title_sets_found_false() -> None:
    schema = BookSchema(
        sections=[
            SchemaSection(
                id="9",
                level=1,
                title="Nonexistent Section",
                type="chapter",
                subsections=[],
            )
        ]
    )
    chunks = build_chunks(RAW, schema)
    assert len(chunks) == 1
    assert chunks[0].found is False
    assert chunks[0].suspect is True


def test_get_token_budget_scales_with_word_count() -> None:
    assert get_token_budget(50) == 1500
    assert get_token_budget(200) == 2500
    assert get_token_budget(400) == 3500
    assert get_token_budget(700) == 5000
    assert get_token_budget(1000) == 6500
    assert get_token_budget(2000) == 7000
