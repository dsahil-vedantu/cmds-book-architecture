"""Tests for the robust JSON parser used on Claude responses."""

from __future__ import annotations

import pytest

from app.utils.json_parse import extract_json, parse_json


def test_parses_plain_object() -> None:
    assert parse_json('{"a": 1}') == {"a": 1}


def test_strips_markdown_fences() -> None:
    text = '```json\n{"pdf_type": "digital", "estimated_pages": 10}\n```'
    assert parse_json(text)["pdf_type"] == "digital"


def test_strips_leading_prose() -> None:
    text = 'Here is the answer:\n{"a": 1, "b": [2, 3]}'
    assert parse_json(text) == {"a": 1, "b": [2, 3]}


def test_handles_trailing_commas() -> None:
    text = '{"a": 1, "b": 2,}'
    assert parse_json(text) == {"a": 1, "b": 2}


def test_array_roots() -> None:
    assert parse_json("[1, 2, 3]") == [1, 2, 3]


def test_extract_json_balanced_braces_in_strings() -> None:
    text = '{"msg": "value has } inside"}'
    assert extract_json(text) == '{"msg": "value has } inside"}'


def test_raises_when_no_json() -> None:
    with pytest.raises(ValueError):
        parse_json("just prose with no brackets")
