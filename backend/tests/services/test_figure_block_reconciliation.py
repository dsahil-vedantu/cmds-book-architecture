"""Pre-flight tests for figure_block_reconciliation.

Uses an in-memory SQLite DB so we exercise the real SQLAlchemy code
path (including ``flag_modified`` on JSON columns) — the same way the
extract worker invokes it.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.db import Base
from app.models.book import Book  # noqa: F401  (registers mappers)
from app.models.figure import Figure
from app.models.section import Section
from app.services.figure_block_reconciliation import reconcile_fig_blocks


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _mk_book(session) -> UUID:
    book = Book(id=uuid4(), title="t", status="ready")
    session.add(book)
    session.flush()
    return book.id


def _mk_section(
    session, book_id: UUID, sid: str, blocks: list[dict] | None = None
) -> Section:
    sec = Section(
        id=uuid4(),
        book_id=book_id,
        section_id=sid,
        title=sid,
        blocks=blocks or [],
        status="passed",
    )
    session.add(sec)
    session.flush()
    return sec


def _mk_figure(
    session,
    book_id: UUID,
    section: Section,
    *,
    figure_number: str | None = None,
    caption: str | None = None,
    anchor_text: str | None = None,
    is_labelled: bool | None = None,
) -> Figure:
    meta: dict = {}
    if anchor_text is not None:
        meta["anchor_text"] = anchor_text
    if is_labelled is not None:
        meta["is_labelled"] = is_labelled
    fig = Figure(
        id=uuid4(),
        book_id=book_id,
        section_id=section.section_id,
        section_uuid=section.id,
        figure_number=figure_number,
        caption=caption,
        regen_meta=meta or None,
    )
    session.add(fig)
    session.flush()
    return fig


# ─── Test 1: labelled, no fig block → insert ──────────────────────


def test_labelled_no_fig_block_inserts(session):
    book_id = _mk_book(session)
    sec = _mk_section(
        session, book_id, "introduction",
        blocks=[{"t": "p", "c": "Some prose."}],
    )
    _mk_figure(
        session, book_id, sec,
        figure_number="Figure 9.3", caption="A circuit diagram",
    )

    report = reconcile_fig_blocks(session, book_id)
    assert report.labelled_inserted == 1
    assert report.labelled_populated == 0
    session.refresh(sec)
    fig_blocks = [b for b in sec.blocks if b.get("t") == "fig"]
    assert len(fig_blocks) == 1
    fb = fig_blocks[0]
    assert fb["label"] == "Figure 9.3"
    assert fb["c"] == "Figure 9.3"
    assert fb["caption"] == "A circuit diagram"


# ─── Test 2: labelled, empty fig block → populate ─────────────────


def test_labelled_empty_fig_block_populates(session):
    book_id = _mk_book(session)
    sec = _mk_section(
        session, book_id, "intro",
        blocks=[
            {"t": "p", "c": "prose"},
            {"t": "fig", "label": "Figure 9.3"},  # empty: no caption, no c
        ],
    )
    _mk_figure(
        session, book_id, sec,
        figure_number="Figure 9.3", caption="Populated caption",
    )

    report = reconcile_fig_blocks(session, book_id)
    assert report.labelled_populated == 1
    assert report.labelled_inserted == 0
    session.refresh(sec)
    fig_blocks = [b for b in sec.blocks if b.get("t") == "fig"]
    assert len(fig_blocks) == 1
    assert fig_blocks[0]["caption"] == "Populated caption"
    assert fig_blocks[0]["c"] == "Figure 9.3"


# ─── Test 3: labelled, populated fig block → skip ─────────────────


def test_labelled_populated_fig_block_skips(session):
    book_id = _mk_book(session)
    sec = _mk_section(
        session, book_id, "intro",
        blocks=[
            {
                "t": "fig",
                "label": "Figure 9.3",
                "c": "Figure 9.3",
                "caption": "Already there",
            },
        ],
    )
    _mk_figure(
        session, book_id, sec,
        figure_number="Figure 9.3", caption="Already there",
    )

    report = reconcile_fig_blocks(session, book_id)
    assert report.labelled_skipped == 1
    assert report.labelled_inserted == 0
    assert report.labelled_populated == 0
    session.refresh(sec)
    fig_blocks = [b for b in sec.blocks if b.get("t") == "fig"]
    assert len(fig_blocks) == 1


# ─── Test 4: unlabelled with anchor → insert AFTER anchor block ────


def test_unlabelled_with_anchor_inserts_after(session):
    book_id = _mk_book(session)
    sec = _mk_section(
        session, book_id, "intro",
        blocks=[
            {"t": "p", "c": "Filler line one."},
            {"t": "p", "c": "As shown in the diagram below the apparatus."},
            {"t": "p", "c": "Filler line three."},
        ],
    )
    _mk_figure(
        session, book_id, sec,
        figure_number=None,
        caption="apparatus photo",
        anchor_text="shown in the diagram below the apparatus",
        is_labelled=False,
    )

    report = reconcile_fig_blocks(session, book_id)
    assert report.unlabelled_inserted == 1
    session.refresh(sec)
    # The new fig should be at index 2 (immediately after the anchor at 1).
    assert sec.blocks[2]["t"] == "fig"
    assert sec.blocks[2]["caption"] == "apparatus photo"
    # The filler "line three" should now be at index 3.
    assert sec.blocks[3]["c"] == "Filler line three."


# ─── Test 5: unlabelled with no anchor → append at end ────────────


def test_unlabelled_no_anchor_appends_at_end(session):
    book_id = _mk_book(session)
    sec = _mk_section(
        session, book_id, "intro",
        blocks=[
            {"t": "p", "c": "Some prose."},
            {"t": "p", "c": "More prose."},
        ],
    )
    _mk_figure(
        session, book_id, sec,
        figure_number=None,
        caption="orphan figure",
        anchor_text=None,
        is_labelled=False,
    )

    report = reconcile_fig_blocks(session, book_id)
    assert report.unlabelled_inserted == 1
    session.refresh(sec)
    assert sec.blocks[-1]["t"] == "fig"
    assert sec.blocks[-1]["caption"] == "orphan figure"


# ─── Test 6: idempotent — run twice, same result ──────────────────


def test_idempotent_double_run(session):
    book_id = _mk_book(session)
    sec = _mk_section(
        session, book_id, "intro",
        blocks=[{"t": "p", "c": "prose"}],
    )
    _mk_figure(
        session, book_id, sec,
        figure_number="Figure 1.1", caption="first",
    )
    _mk_figure(
        session, book_id, sec,
        figure_number=None,
        caption="unlabelled here",
        anchor_text=None,
        is_labelled=False,
    )

    r1 = reconcile_fig_blocks(session, book_id)
    session.commit()
    session.refresh(sec)
    blocks_after_first = list(sec.blocks)
    fig_count_1 = sum(1 for b in sec.blocks if b.get("t") == "fig")

    r2 = reconcile_fig_blocks(session, book_id)
    session.commit()
    session.refresh(sec)
    fig_count_2 = sum(1 for b in sec.blocks if b.get("t") == "fig")

    assert fig_count_1 == 2
    assert fig_count_2 == 2  # no duplicate insertions on second run
    assert sec.blocks == blocks_after_first
    assert r2.labelled_inserted == 0
    assert r2.unlabelled_inserted == 0
    assert r1.labelled_inserted + r1.unlabelled_inserted == 2


# ─── Test 7: no section found → skip silently ─────────────────────


def test_no_section_found_skips_silently(session):
    book_id = _mk_book(session)
    # Figure refers to a section that doesn't exist.
    fig = Figure(
        id=uuid4(),
        book_id=book_id,
        section_id="does-not-exist",
        section_uuid=None,
        figure_number="Figure 9.99",
        caption="orphan",
    )
    session.add(fig)
    session.flush()

    report = reconcile_fig_blocks(session, book_id)
    assert report.no_section == 1
    assert report.labelled_inserted == 0
    assert report.unlabelled_inserted == 0


# ─── Test 8: Modern Physics replica — 6 labelled missing → fixed ──


def test_modern_physics_replica_six_labelled_fixed(session):
    book_id = _mk_book(session)
    sec = _mk_section(
        session, book_id, "9.4-photoelectric-effect",
        blocks=[
            {"t": "p", "c": "Photoelectric effect intro."},
            {"t": "p", "c": "Einstein's explanation."},
        ],
    )
    labels = [
        "Figure 9.1", "Figure 9.2", "Figure 9.3",
        "Figure 9.4", "Figure 9.5", "Figure 9.6",
    ]
    for lbl in labels:
        _mk_figure(
            session, book_id, sec,
            figure_number=lbl, caption=f"caption for {lbl}",
        )

    report = reconcile_fig_blocks(session, book_id)
    assert report.figures_seen == 6
    assert report.labelled_inserted == 6
    assert report.labelled_populated == 0
    assert report.labelled_skipped == 0
    session.refresh(sec)
    fig_blocks = [b for b in sec.blocks if b.get("t") == "fig"]
    assert len(fig_blocks) == 6
    found_labels = {b["label"] for b in fig_blocks}
    assert found_labels == set(labels)
    # And each carries its caption.
    for b in fig_blocks:
        assert b.get("caption") == f"caption for {b['label']}"
