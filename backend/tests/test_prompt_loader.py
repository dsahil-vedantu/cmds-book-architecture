"""Sanity checks for the prompt loader — keeps us from accidentally breaking
the 6 prompt files (each one is battle-tested and must load as expected)."""

from __future__ import annotations

import pytest

from app.services.prompt_loader import load_raw, render


@pytest.mark.parametrize(
    "name",
    [
        "analyser",
        "schema",
        "raw_extract",
        "extractor",
        "extractor_attempt2",
        "extractor_attempt3",
        "regenerator",
        "qc_auditor",
    ],
)
def test_prompts_exist_and_nonempty(name: str) -> None:
    text = load_raw(name)
    assert text.strip(), f"Prompt {name} is empty"


def test_extractor_attempt2_has_placeholder() -> None:
    text = load_raw("extractor_attempt2")
    assert "{formatted_qc_issues}" in text


def test_render_substitutes_known_keys_and_leaves_unknown_intact() -> None:
    rendered = render("extractor_attempt2", formatted_qc_issues="Numbers missing: 2.3")
    assert "Numbers missing: 2.3" in rendered
    assert "{formatted_qc_issues}" not in rendered
