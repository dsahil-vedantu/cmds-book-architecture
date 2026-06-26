"""Tests for P5 regenerator helpers + post-regen QC."""

from __future__ import annotations

from app.services.regenerator import (
    build_regen_system_prompt,
    free_blocks_to_text,
    post_regen_qc,
)
from app.schemas.regen import RegenParams


def test_free_blocks_to_text_renders_all_free_types() -> None:
    free = [
        {"t": "p", "c": "Intro paragraph"},
        {"t": "h3", "c": "Sub-heading"},
        {"t": "kp", "c": "Important note"},
        {"t": "list", "items": ["one", "two"]},
    ]
    out = free_blocks_to_text(free)
    assert "Intro paragraph" in out
    assert "### Sub-heading" in out
    assert "[KEY POINT: Important note]" in out
    assert "1. one" in out and "2. two" in out


def test_post_regen_qc_detects_drift() -> None:
    original = [{"t": "p", "c": "The work function for sodium is 2.3 eV."}]
    regenerated = [{"t": "p", "c": "The work function for sodium is approximately 2 eV."}]
    qc = post_regen_qc(original, regenerated)
    assert not qc.pass_
    assert "2.3" in qc.drifted_values


def test_post_regen_qc_passes_when_all_values_preserved() -> None:
    original = [{"t": "p", "c": "Planck 6.626 and charge 1.6 preserved."}]
    regenerated = [{"t": "p", "c": "With Planck 6.626 and charge 1.6 kept intact."}]
    qc = post_regen_qc(original, regenerated)
    assert qc.pass_
    assert qc.drifted_values == []


def test_regen_system_prompt_substitutes_params() -> None:
    params = RegenParams(intensity="heavy", tone="conversational", structure="identical")
    rendered = build_regen_system_prompt(params)
    assert "70-90%" in rendered  # heavy intensity descriptor
    assert "conversational" in rendered.lower() or "friendly" in rendered.lower()
    assert "IDENTICAL" in rendered
