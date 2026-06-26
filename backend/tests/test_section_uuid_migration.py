"""Canonical section UUID migration tests (CONTRACT.md §1).

Verifies that figure placement and question matching prefer the
canonical UUID FK (section_uuid) over the legacy slug (section_id /
section_ref) when both are available, and that slug remains a valid
fallback for legacy rows where the UUID is null.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from app.services.figure_placement import (
    PK_POSITION_DEFAULT,
    PK_QUESTION_CHIP,
    place_unlabelled_question,
    resolve_effective_section,
)


# ─── Synthetic in-memory fixtures (mimic SQLA model shape) ──────────


@dataclass
class FakeSection:
    section_id: str
    title: str = ""
    blocks: list[dict] = field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    level: int | None = 2
    id: UUID = field(default_factory=uuid4)


@dataclass
class FakeFigure:
    section_id: str
    section_uuid: UUID | None = None
    figure_number: str | None = None
    normalized_label: str | None = None
    page_number: int | None = None
    context_hint: str = "theory"
    regen_meta: dict | None = None
    id: UUID = field(default_factory=uuid4)


@dataclass
class FakeQuestion:
    section_ref: str
    question_number: str | None = None
    raw_text: str = ""
    id: UUID = field(default_factory=uuid4)
    book_id: UUID = field(default_factory=uuid4)
    bank_id: UUID = field(default_factory=uuid4)
    section_uuid: UUID | None = None
    regen_id: UUID | None = None


# ─── Tests ─────────────────────────────────────────────────────────


def test_figure_with_section_uuid_resolves():
    """When a figure has section_uuid set and sections_by_uuid is
    provided, resolve_effective_section returns the matching Section
    even if the slug is unfamiliar."""
    sec = FakeSection(
        section_id="ch1-intro",
        blocks=[{"t": "p", "c": "Body."}],
    )
    sections_by_id = {sec.section_id: sec}
    sections_by_uuid = {sec.id: sec}

    # Pass a wrong slug but a valid UUID → UUID wins.
    eff = resolve_effective_section(
        target_sid="some-stale-or-wrong-slug",
        sections_by_id=sections_by_id,
        target_uuid=sec.id,
        sections_by_uuid=sections_by_uuid,
    )
    assert eff is sec


def test_figure_no_uuid_falls_back_to_slug():
    """No section_uuid, valid slug → returns Section via slug fallback."""
    sec = FakeSection(
        section_id="ch2-laws",
        blocks=[{"t": "p", "c": "Laws."}],
    )
    sections_by_id = {sec.section_id: sec}

    eff = resolve_effective_section(
        target_sid="ch2-laws",
        sections_by_id=sections_by_id,
        target_uuid=None,
        sections_by_uuid=None,
    )
    assert eff is sec


def test_figure_invalid_slug_and_no_uuid_returns_none():
    """Both missing → returns None."""
    sec = FakeSection(section_id="ch3-foo")
    sections_by_id = {sec.section_id: sec}

    eff = resolve_effective_section(
        target_sid="nonexistent-slug",
        sections_by_id=sections_by_id,
        target_uuid=None,
        sections_by_uuid={},
    )
    assert eff is None

    eff_none = resolve_effective_section(
        target_sid=None,
        sections_by_id=sections_by_id,
    )
    assert eff_none is None


def test_question_lookup_prefers_uuid():
    """place_unlabelled_question must NOT match a Question whose
    section_uuid points at a DIFFERENT section, even when the slug
    prefix would otherwise match."""
    theory_sec = FakeSection(
        section_id="ch9-triangles",
        blocks=[
            {"t": "p", "c": "Construct triangles given two sides."},
            {
                "t": "question_ref",
                "label": "EXAMPLE 9.11",
                "section_id": "ch9-triangles-example-9.11",
            },
            {"t": "p", "c": "End of section."},
        ],
    )
    # A different theory section — its UUID will NOT match the question.
    other_sec = FakeSection(section_id="ch9-other")

    # The question's UUID points at `other_sec`, NOT `theory_sec`,
    # even though its slug prefix would otherwise match `theory_sec`.
    q_mismatch = FakeQuestion(
        section_ref="ch9-triangles-example-9.11",
        question_number="9.11",
        section_uuid=other_sec.id,
    )
    # A correctly-pointed question (UUID matches theory_sec).
    q_good = FakeQuestion(
        section_ref="ch9-triangles-example-9.12",
        question_number="9.12",
        section_uuid=theory_sec.id,
    )

    questions_by_sid = {
        q_mismatch.section_ref: [q_mismatch],
        q_good.section_ref: [q_good],
    }

    # Asking for 9.11: the slug-only match would pick q_mismatch, but
    # the UUID check must reject it → no candidate → fallback.
    fig = FakeFigure(
        section_id="ch9-triangles-example-9.11",
        section_uuid=theory_sec.id,
        context_hint="question",
        regen_meta={"is_labelled": False, "question_no": "9.11", "anchor_text": ""},
    )
    dec = place_unlabelled_question(fig, theory_sec, questions_by_sid)
    # No UUID-matching question for 9.11 → falls through to
    # position_default in the effective section.
    assert dec.placement_kind == PK_POSITION_DEFAULT
    assert dec.question_id is None

    # Asking for 9.12: q_good's UUID matches → question_chip.
    fig2 = FakeFigure(
        section_id="ch9-triangles-example-9.12",
        section_uuid=theory_sec.id,
        context_hint="question",
        regen_meta={"is_labelled": False, "question_no": "9.12", "anchor_text": ""},
    )
    dec2 = place_unlabelled_question(fig2, theory_sec, questions_by_sid)
    assert dec2.placement_kind == PK_QUESTION_CHIP
    assert dec2.question_id == q_good.id
