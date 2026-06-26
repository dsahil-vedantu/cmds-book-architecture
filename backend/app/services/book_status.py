"""Derive book.status from per-stage status fields.

Per CONTRACT.md §2 (State Contract). The old single ``book.status``
field was a lie at scale — `extract.py:578` set it to "ready"
unconditionally. Phase 5d added per-stage status fields populated
by the workers; this module derives the overall book.status from
those fields, kills the lie at the source.

Lookup table:
  schema    theory    questions    figures   →  book.status
  ─────────────────────────────────────────────────────────
  pending   *         *            *         →  queued
  running   *         *            *         →  processing  (schema in flight)
  failed    *         *            *         →  failed      (schema crash blocks everything)
  done      pending   *            *         →  queued      (extract not started)
  done      running   *            *         →  processing
  done      failed    *            *         →  failed
  done      done      pending      pending   →  queued      (questions+figures not started)
  done      done      running      *         →  processing
  done      done      done         pending   →  processing  (figures still pending — common)
  done      done      *            running   →  processing
  done      *         failed       *         →  failed
  done      *         *            failed    →  failed
  done      done|partial   done|partial   done|partial  →  partial OR ready

Rules:
  1. ANY 'failed' → "failed" (loud)
  2. ANY 'running' → "processing"
  3. Schema pending → "queued"
  4. ANY 'partial' → "partial"   (clear honest signal)
  5. ANY 'pending' (post-schema) → "queued"
  6. ALL 'done' → "ready"

Backward compatibility note:
  Existing v1 books (no per-stage fields populated) keep their old
  book.status verbatim — derive_book_status() returns the existing
  book.status if all four per-stage fields are still 'pending'.
  This means /quality (which uses derived semantics) sees the truth;
  the legacy book.status field stays "ready" for grandfathered books.
"""

from __future__ import annotations

from app.models.book import Book


def derive_book_status(book: Book) -> str:
    """Compute the overall book status from per-stage fields.

    Returns one of:  queued | processing | partial | ready | failed

    Edge case: when ALL per-stage fields are still 'pending' (legacy
    book that was extracted before Phase 5d landed), returns the
    existing book.status unchanged — don't retroactively reclassify
    pre-v2 books as 'queued' when they're already in their final state.
    """
    # 'needs_review' is a TERMINAL schema state — the schema IS built, just
    # flagged for optional human review (the orchestrator's coordinator even
    # auto-heals needs_review→done once downstream stages finish). Treat it as
    # done-equivalent here. Without this, a fully-extracted book whose schema is
    # 'needs_review' never satisfies the all-'done' check below, so it hangs at
    # "processing" forever — never flips to "ready", and the UI never shows the
    # "View extracted / Regenerate" CTAs even though every stage is complete.
    schema_status = (
        "done" if book.schema_status == "needs_review" else book.schema_status
    )
    stages = (
        schema_status,
        book.theory_status,
        book.questions_status,
        book.figures_status,
    )

    # Legacy fallback: all stages still 'pending' means the book was
    # extracted on v1 code. Trust whatever book.status was last set to.
    if all(s == "pending" for s in stages):
        return book.status

    if "running" in stages:
        return "processing"
    # If FIGURES is the only failed stage AND theory+questions are done, the
    # book is partial-but-usable (theory text + questions are extracted and
    # viewable; only the figure detection step failed). Treat as 'partial' so
    # the UI keeps the View / Regenerate CTAs and a "Retry figures" button —
    # don't wipe the rest of the work behind a generic 'failed' screen and a
    # "Retry schema" prompt. Other failure combinations stay 'failed' (loud)
    # because they break downstream rendering.
    figures_only_failed = (
        book.figures_status == "failed"
        and book.theory_status == "done"
        and book.questions_status == "done"
        and "failed" not in (schema_status, book.theory_status, book.questions_status)
    )
    if figures_only_failed:
        return "partial"
    if "failed" in stages:
        return "failed"
    if book.schema_status == "pending":
        return "queued"
    if "partial" in stages:
        return "partial"
    if "pending" in stages:
        # Schema done, but a later stage hasn't started yet.
        return "queued"
    if all(s == "done" for s in stages):
        return "ready"
    # Fallback — shouldn't reach here, but be honest if we do
    return "processing"


def update_book_status_from_stages(book: Book) -> None:
    """Set book.status from derive_book_status. Caller must commit.

    Idempotent — safe to call before commit on every stage transition.
    Phase 5e writers replace ``book.status = "ready"`` with
    ``update_book_status_from_stages(book)``.
    """
    book.status = derive_book_status(book)
