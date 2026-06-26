"""Canonical figure serialization — the SINGLE source of truth for turning
``figure_references`` rows into the figure dicts the frontend renders.

Before this module there were two near-identical, already-drifted helpers:
  - ``sections.py:_load_embedded_figures``         (theory; approval-gated regen)
  - ``question_banks.py:_load_question_embedded_figures`` (question; no gate)

They disagreed on the regen-visibility rule and each new consumer (theory
regen reader, question regen reader) was re-implementing the same loop. This
module collapses all of that into ONE function so:

  • theory + question extract readers,
  • theory + question REGEN readers,

all produce byte-identical figure dicts. No drift possible — there is one
code path. Adding a new consumer is a function call, never a new figure loop.

REGEN VISIBILITY RULE (unified — Option A):
  A regenerated image is shown whenever ``regen_image_bytes`` exists. There
  is NO approval gate. The explicit Approve step on the Figures tab is a QA
  workflow, not a precondition for display. (This matches the user intent:
  "if the user has regenerated that image, show the regenerated one on that
  placeholder.")

VARIANT SEMANTICS (what ``image_url`` actually serves):
  variant="original"    → always the original image
  variant="auto"        → regen if it exists, else original
  variant="regenerated" → regen if it exists, else falls back to original
                          (we emit ?variant=auto in the URL when no regen
                          exists, so the <img> never 404s)

  Tab → variant mapping (decided by the caller, not here):
    Original tab        → "original"
    Regenerated tab     → "regenerated"  (regen-with-fallback)
    Comparison left     → "original"
    Comparison right    → "regenerated"  (regen-with-fallback)
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.figure import Figure
from app.models.figure_reference import FigureReference

FigureContext = Literal["theory", "question"]
FigureVariant = Literal["auto", "original", "regenerated"]


def _url_variant(requested: FigureVariant, has_regen: bool) -> str:
    """Resolve the ?variant= value to put in the image URL.

    The image endpoint (figures.py:get_figure_image) accepts
    original|regenerated|auto and returns 404 for variant=regenerated
    when no regen bytes exist. To keep the <img> from breaking we emit
    'auto' (which serves the original) instead of 'regenerated' whenever
    no regen image is present — that is the "regen-with-fallback" the
    Regenerated tab / comparison-right column want.
    """
    if requested == "original":
        return "original"
    if requested == "regenerated":
        return "regenerated" if has_regen else "auto"
    return "auto"


def _figure_dict(
    ref: FigureReference, fig: Figure, requested: FigureVariant
) -> dict[str, Any]:
    """The ONE canonical figure dict shape. Every reader emits exactly this."""
    has_regen = bool(fig.regen_image_bytes)
    url_variant = _url_variant(requested, has_regen)
    # `variant` field reports what the URL will actually serve, so the UI
    # can label/badge accordingly without re-deriving.
    effective = "regen" if url_variant in ("regenerated", "auto") and has_regen else "original"
    return {
        "ref_id": str(ref.id),
        "figure_id": str(fig.id),
        "label": fig.figure_number or ref.placeholder_text or "",
        "caption": fig.caption or "",
        # Gemini's 2-3 sentence description — the placeholder text the UI
        # shows for UNLABELLED figures (label + caption both empty).
        "description": fig.description or "",
        # What the URL will serve right now.
        "variant": effective,
        # Whether a regenerated image exists at all — drives the
        # "↻ Regenerated" badge on regen-side renderings.
        "has_regen": has_regen,
        "image_url": f"/api/figures/{fig.id}/image?variant={url_variant}",
        "placement_kind": ref.placement_kind or "appended",
        "placement_block_idx": ref.placement_block_idx,
        "placement_char_offset": ref.placement_char_offset,
        # Explicit body target (question vs solution) — frontend reads
        # this directly. NULL on legacy refs → caller treats as question.
        "body_target": ref.body_target,
    }


async def serialize_embedded_figures(
    session: AsyncSession,
    book_id: UUID,
    *,
    context: FigureContext,
    variant: FigureVariant = "auto",
) -> dict[str, list[dict[str, Any]]]:
    """Return ``{key: [figure_dict, ...]}`` for one book's figure_references.

    Keying:
      context="theory"   → key is ``section_ref``
      context="question" → key is ``question_id`` (as str)

    Filters (identical for both contexts):
      • only the requested ``context``
      • not hidden
      • not unattached (those live in a separate tray)
      • ghost figures (no bytes of EITHER variant) skipped — they would
        render as broken <img> tags

    Ordering:
      context="theory"   → by placement_block_idx (None → end)
      context="question" → by placement_char_offset (None → end)
    """
    refs = (
        await session.execute(
            select(FigureReference)
            .where(FigureReference.book_id == book_id)
            .where(FigureReference.context == context)
            .where(FigureReference.is_hidden.is_(False))
            .where(FigureReference.placement_kind != "unattached")
        )
    ).scalars().all()
    if not refs:
        return {}

    fig_ids = {r.figure_id for r in refs}
    figs = (
        await session.execute(select(Figure).where(Figure.id.in_(fig_ids)))
    ).scalars().all()
    fig_by_id = {f.id: f for f in figs}

    out: dict[str, list[dict[str, Any]]] = {}
    for r in refs:
        fig = fig_by_id.get(r.figure_id)
        if fig is None:
            continue
        # Ghost-figure guard: a reference whose figure never got image
        # bytes (extraction crashed before commit). Including it produces
        # a broken <img>.
        if not fig.regen_image_bytes and not fig.image_bytes:
            continue
        if context == "question":
            if r.question_id is None:
                continue
            key = str(r.question_id)
        else:
            key = r.section_ref
        out.setdefault(key, []).append(_figure_dict(r, fig, variant))

    sort_field = (
        "placement_char_offset" if context == "question" else "placement_block_idx"
    )
    for lst in out.values():
        lst.sort(
            key=lambda d: (
                d.get(sort_field) if d.get(sort_field) is not None else 10 ** 9
            )
        )
    return out


__all__ = ["serialize_embedded_figures", "FigureContext", "FigureVariant"]
