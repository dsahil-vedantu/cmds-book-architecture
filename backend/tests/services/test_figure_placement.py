"""Pre-flight tests for the new figure_placement + figure_embedder.

The .new files can't be imported by name through the normal import
system; we load them via importlib.util.spec_from_file_location.
After the swap (.new → real .py) these tests can be rewritten as
plain ``import`` lines.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[2]
SERVICES = BACKEND_ROOT / "app" / "services"


def _load_module_from_path(name: str, path: Path):
    # spec_from_file_location can't infer a loader from `.py.new`; supply
    # SourceFileLoader explicitly so it reads the file as Python source.
    from importlib.machinery import SourceFileLoader

    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_file_location(
        name, str(path), loader=loader
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def placement_mod():
    """Load app/services/figure_placement.py (post-swap)."""
    import app.services.figure_placement as mod
    return mod


@pytest.fixture(scope="module")
def embedder_mod():
    """Load app/services/figure_embedder.py (post-swap)."""
    import app.services.figure_embedder as mod
    return mod


# ─── Synthetic in-memory fixtures (mimic SQLA model shape) ────────


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


@dataclass
class FakeFigureRef:
    figure_id: UUID
    book_id: UUID
    section_ref: str
    section_uuid: UUID | None = None
    context: str = "theory"
    question_id: UUID | None = None
    placeholder_text: str | None = None
    link_method: str = "auto"
    placement_kind: str | None = None
    placement_block_idx: int | None = None
    placement_char_offset: int | None = None
    is_hidden: bool = False
    id: UUID = field(default_factory=uuid4)


# ─── Tests ────────────────────────────────────────────────────────


def test_labelled_figure_finds_label_in_section(placement_mod):
    """Synthetic labelled figure → label match → block_idx pointed at fig block."""
    sec = FakeSection(
        section_id="ch1-intro",
        page_start=1,
        page_end=3,
        blocks=[
            {"t": "p", "c": "Intro paragraph"},
            {"t": "fig", "label": "Figure 4.7", "c": "An example"},
            {"t": "p", "c": "More body"},
        ],
    )
    fig = FakeFigure(
        section_id="ch1-intro",
        figure_number="Figure 4.7",
        normalized_label="4.7",
        page_number=2,
    )
    dec = placement_mod.place_labelled(fig, sec, [sec])
    assert dec.placement_kind == placement_mod.PK_LABEL
    assert dec.block_idx == 1
    assert dec.section_id == "ch1-intro"
    assert dec.question_id is None


def test_unlabelled_theory_anchor_match(placement_mod):
    """Anchor substring → correct block_idx, placement_kind == anchor."""
    sec = FakeSection(
        section_id="ch1-laws",
        blocks=[
            {"t": "p", "c": "Some intro."},
            {"t": "p", "c": "The angle of incidence equals the angle of reflection in this experiment."},
            {"t": "p", "c": "Closing remarks."},
        ],
    )
    fig = FakeFigure(
        section_id="ch1-laws",
        context_hint="theory",
        regen_meta={
            "is_labelled": False,
            "anchor_text": "angle of incidence equals the angle of reflection",
            "anchor_position": "below",
        },
    )
    dec = placement_mod.place_unlabelled_theory(fig, sec)
    assert dec.placement_kind == placement_mod.PK_ANCHOR
    assert dec.block_idx == 1


def test_unlabelled_question_compound_key(placement_mod):
    """(section, question_no) compound key resolves the right Question."""
    theory_sec = FakeSection(
        section_id="ch9-triangles",
        blocks=[
            {"t": "p", "c": "Construct triangles given two sides."},
            {"t": "question_ref", "label": "EXAMPLE 9.11",
             "section_id": "ch9-triangles-example-9.11"},
            {"t": "p", "c": "End of section."},
        ],
    )
    q_match = FakeQuestion(
        section_ref="ch9-triangles-example-9.11", question_number="9.11"
    )
    q_other = FakeQuestion(
        section_ref="ch9-triangles-example-9.10", question_number="9.10"
    )
    questions_by_sid = {
        "ch9-triangles-example-9.11": [q_match],
        "ch9-triangles-example-9.10": [q_other],
    }
    fig = FakeFigure(
        section_id="ch9-triangles-example-9.11",
        context_hint="question",
        regen_meta={
            "is_labelled": False,
            "question_no": "9.11",
            "anchor_text": "",
        },
    )
    dec = placement_mod.place_unlabelled_question(
        fig, theory_sec, questions_by_sid
    )
    assert dec.placement_kind == placement_mod.PK_QUESTION_CHIP
    assert dec.question_id == q_match.id
    assert dec.block_idx == 1


def test_unlabelled_no_neighbour_inheritance(placement_mod, embedder_mod):
    """PROD BUG TEST: unlabelled figure in section X with a labelled
    neighbour figure pinned to section Y must land in X, NOT Y."""
    sec_x = FakeSection(
        section_id="ch5-section-x",
        page_start=10,
        page_end=12,
        blocks=[{"t": "p", "c": "Section X body — long enough body to host."}],
    )
    sec_y = FakeSection(
        section_id="ch5-section-y",
        page_start=13,
        page_end=15,
        blocks=[
            {"t": "fig", "label": "Figure 5.2", "c": "Y's labelled figure"},
            {"t": "p", "c": "Section Y body."},
        ],
    )
    sections_by_id = {sec_x.section_id: sec_x, sec_y.section_id: sec_y}

    fig_unlabelled = FakeFigure(
        section_id="ch5-section-x",
        context_hint="theory",
        page_number=11,
        regen_meta={
            "is_labelled": False,
            "anchor_text": "",   # forces position_default
            "anchor_position": "below",
        },
    )
    fig_labelled_neighbour = FakeFigure(
        section_id="ch5-section-y",
        figure_number="Figure 5.2",
        normalized_label="5.2",
        page_number=14,
    )

    decisions, _ = embedder_mod.compute_decisions(
        [fig_unlabelled, fig_labelled_neighbour],
        sections_by_id,
        [],
    )
    dec_un = decisions[fig_unlabelled.id]
    dec_lb = decisions[fig_labelled_neighbour.id]
    assert dec_un.section_id == "ch5-section-x", (
        f"unlabelled drifted to {dec_un.section_id} (neighbour-inheritance bug)"
    )
    assert dec_lb.section_id == "ch5-section-y"
    # And: unlabelled with no anchor → position_default at section end
    assert dec_un.placement_kind == placement_mod.PK_POSITION_DEFAULT
    assert dec_un.block_idx is None


def test_diff_upsert_preserves_manual_ref(embedder_mod):
    """An existing manual-link FigureReference is preserved verbatim
    even when the auto decision would say something different."""
    book_id = uuid4()
    sec_x = FakeSection(
        section_id="ch5-section-x",
        blocks=[{"t": "p", "c": "Body."}],
    )
    fig = FakeFigure(
        section_id="ch5-section-x",
        context_hint="theory",
        regen_meta={"is_labelled": False, "anchor_text": ""},
    )
    decisions = {
        fig.id: embedder_mod.PlacementDecision(
            section_id="ch5-section-x",
            section_uuid=sec_x.id,
            question_id=None,
            block_idx=None,
            placement_kind=embedder_mod.PK_POSITION_DEFAULT,
        )
    }
    # Pre-existing manual ref points at a totally different section.
    manual_ref = FakeFigureRef(
        figure_id=fig.id,
        book_id=book_id,
        section_ref="ch5-section-y",
        link_method="manual",
        placement_kind=embedder_mod.PK_ANCHOR,
        placement_block_idx=0,
    )
    to_insert, to_update, to_delete, stats = embedder_mod._diff_upsert(
        [fig], decisions, [manual_ref], book_id
    )
    assert to_insert == []
    assert to_update == []
    assert to_delete == []
    assert stats["preserved_manual"] == 1
    # Manual row unchanged.
    assert manual_ref.section_ref == "ch5-section-y"
    assert manual_ref.link_method == "manual"


def test_diff_upsert_unchanged_ref_untouched(embedder_mod):
    """If an existing auto-ref exactly matches the decision,
    diff/upsert returns no inserts / updates / deletes for it."""
    book_id = uuid4()
    sec_x = FakeSection(section_id="ch5-section-x", blocks=[{"t": "p", "c": "x"}])
    fig = FakeFigure(
        section_id="ch5-section-x",
        context_hint="theory",
        regen_meta={"is_labelled": False, "anchor_text": ""},
    )
    dec = embedder_mod.PlacementDecision(
        section_id="ch5-section-x",
        section_uuid=sec_x.id,
        question_id=None,
        block_idx=None,
        placement_kind=embedder_mod.PK_POSITION_DEFAULT,
    )
    existing_ref = FakeFigureRef(
        figure_id=fig.id,
        book_id=book_id,
        section_ref=dec.section_id,
        section_uuid=dec.section_uuid,
        context="theory",
        question_id=None,
        placement_kind=dec.placement_kind,
        placement_block_idx=dec.block_idx,
        link_method="auto",
    )
    to_insert, to_update, to_delete, stats = embedder_mod._diff_upsert(
        [fig], {fig.id: dec}, [existing_ref], book_id
    )
    assert to_insert == []
    assert to_update == []
    assert to_delete == []
    assert stats["unchanged"] == 1
