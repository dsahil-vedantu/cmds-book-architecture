"""Tests for the QC helpers (extract_numbers / extract_equations / extract_key_terms)."""

from __future__ import annotations

from app.services.qc.helpers import (
    blocks_to_plain_text,
    extract_equations,
    extract_key_terms,
    extract_numbers,
    paragraphs_to_plain_text,
    split_sentences,
)


def test_extract_numbers_basic() -> None:
    nums = extract_numbers("The work function for sodium is 2.3 eV; Planck is 6.626.")
    assert "2.3" in nums
    assert "6.626" in nums


def test_extract_numbers_dedupes() -> None:
    nums = extract_numbers("2.3 and 2.3 and 2.3")
    assert nums.count("2.3") == 1


def test_extract_equations_simple() -> None:
    eqs = extract_equations("Einstein showed E = mc^2 and F = ma.")
    joined = " | ".join(eqs)
    assert "E = mc" in joined or "E =mc" in joined
    assert any("F = ma" in e or "F =ma" in e for e in eqs)


def test_extract_key_terms_capitalised_phrases() -> None:
    text = "The Photoelectric Effect was explained by Albert Einstein."
    terms = extract_key_terms(text)
    assert "Photoelectric Effect" in terms
    assert "Albert Einstein" in terms


def test_extract_key_terms_capped_at_20() -> None:
    phrases = " ".join(f"Thing Number{i}" for i in range(30))
    terms = extract_key_terms(phrases)
    assert len(terms) <= 20


def test_split_sentences() -> None:
    text = "First sentence. Second one! Third?"
    sents = split_sentences(text)
    assert len(sents) == 3


def test_paragraphs_to_plain_text_includes_all_fields() -> None:
    paragraphs = [
        {"type": "body", "content": "Intro"},
        {"type": "definition", "term": "X", "content": "value"},
        {"type": "example", "label": "Ex 1", "prob": "solve", "eqs": ["a=b"]},
    ]
    out = paragraphs_to_plain_text(paragraphs)
    assert "Intro" in out
    assert "X" in out and "value" in out
    assert "Ex 1" in out and "solve" in out and "a=b" in out


def test_blocks_to_plain_text_lists_and_examples() -> None:
    blocks = [
        {"t": "p", "c": "intro"},
        {"t": "eq", "c": "E=mc^2"},
        {"t": "list", "items": ["one", "two"]},
        {"t": "example", "label": "Ex", "prob": "P", "eqs": ["x=1"]},
        {"t": "def", "term": "T", "c": "D"},
    ]
    out = blocks_to_plain_text(blocks)
    for piece in ("intro", "E=mc^2", "one", "two", "Ex", "P", "x=1", "T", "D"):
        assert piece in out
