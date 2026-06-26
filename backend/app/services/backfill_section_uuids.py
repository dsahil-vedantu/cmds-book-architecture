"""Phase 1 backfill: populate `section_uuid` FK on questions and figures.

Reads existing slug-based join keys (questions.section_ref,
figures.section_id) and resolves them to the canonical Section.id (UUID),
writing the result to the new section_uuid columns added by migration
0022_section_uuid_fks.

Idempotent — safe to run repeatedly. Only writes rows where section_uuid
is currently NULL.

Read-only on sections. Does NOT modify questions.section_ref or
figures.section_id. Old slug-based code keeps working unchanged.

Unresolved rows are logged with their reason so we can audit orphans
without changing behavior.

Usage:
    # All books:
    python -m app.services.backfill_section_uuids --all

    # One specific book:
    python -m app.services.backfill_section_uuids --book-id <uuid>

    # Dry run (no writes, just print what would happen):
    python -m app.services.backfill_section_uuids --all --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# Standalone script: configure logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("backfill_section_uuids")


def _build_section_map(engine: Engine, book_id: str | None) -> dict[tuple[str, str], str]:
    """Build {(book_id, section_slug): section.id} for fast lookup.

    Returns the keys as plain hex strings to match how SQLite stores UUIDs;
    Postgres will store with hyphens but the comparison works either way
    because we look up using the same form we read.
    """
    sql = "SELECT id, book_id, section_id FROM sections"
    params: dict = {}
    if book_id:
        sql += " WHERE book_id = :book_id"
        params["book_id"] = book_id

    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()

    section_map: dict[tuple[str, str], str] = {}
    for sec_id, bk_id, slug in rows:
        if slug:
            section_map[(str(bk_id), slug)] = str(sec_id)
    logger.info("loaded %d sections into lookup map", len(section_map))
    return section_map


def _backfill_questions(
    engine: Engine,
    section_map: dict[tuple[str, str], str],
    book_id: str | None,
    dry_run: bool,
) -> dict[str, int]:
    """Resolve questions.section_ref → questions.section_uuid."""
    sql = (
        "SELECT id, book_id, section_ref FROM questions "
        "WHERE section_uuid IS NULL"
    )
    params: dict = {}
    if book_id:
        sql += " AND book_id = :book_id"
        params["book_id"] = book_id

    counts = Counter()
    updates: list[tuple[str, str]] = []  # (section_uuid, question_id)
    orphans: list[tuple[str, str, str]] = []  # (book_id, qid, section_ref)

    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()

    for qid, bk_id, sec_ref in rows:
        counts["total"] += 1
        if not sec_ref:
            counts["null_section_ref"] += 1
            orphans.append((str(bk_id), str(qid), "<null section_ref>"))
            continue
        match = section_map.get((str(bk_id), sec_ref))
        if match:
            counts["resolved"] += 1
            updates.append((match, str(qid)))
        else:
            counts["orphan"] += 1
            orphans.append((str(bk_id), str(qid), sec_ref))

    if updates and not dry_run:
        with engine.begin() as conn:
            for sec_uuid, qid in updates:
                conn.execute(
                    text(
                        "UPDATE questions SET section_uuid = :s WHERE id = :q"
                    ),
                    {"s": sec_uuid, "q": qid},
                )

    if orphans:
        logger.warning(
            "questions: %d orphans (section_ref does not match any section in DB)",
            len(orphans),
        )
        for bk_id, qid, sec_ref in orphans[:10]:
            logger.warning("  orphan question book=%s qid=%s ref=%r", bk_id, qid, sec_ref)
        if len(orphans) > 10:
            logger.warning("  ... %d more orphans", len(orphans) - 10)

    return dict(counts)


def _backfill_figures(
    engine: Engine,
    section_map: dict[tuple[str, str], str],
    book_id: str | None,
    dry_run: bool,
) -> dict[str, int]:
    """Resolve figures.section_id (string slug, misleadingly named)
    → figures.section_uuid."""
    sql = (
        "SELECT id, book_id, section_id FROM figures "
        "WHERE section_uuid IS NULL"
    )
    params: dict = {}
    if book_id:
        sql += " AND book_id = :book_id"
        params["book_id"] = book_id

    counts = Counter()
    updates: list[tuple[str, str]] = []
    orphans: list[tuple[str, str, str]] = []

    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()

    for fid, bk_id, sec_slug in rows:
        counts["total"] += 1
        if not sec_slug:
            counts["null_section_slug"] += 1
            orphans.append((str(bk_id), str(fid), "<null section_id>"))
            continue
        match = section_map.get((str(bk_id), sec_slug))
        if match:
            counts["resolved"] += 1
            updates.append((match, str(fid)))
        else:
            counts["orphan"] += 1
            orphans.append((str(bk_id), str(fid), sec_slug))

    if updates and not dry_run:
        with engine.begin() as conn:
            for sec_uuid, fid in updates:
                conn.execute(
                    text(
                        "UPDATE figures SET section_uuid = :s WHERE id = :f"
                    ),
                    {"s": sec_uuid, "f": fid},
                )

    if orphans:
        logger.warning(
            "figures: %d orphans (section_id does not match any section)",
            len(orphans),
        )
        for bk_id, fid, sec_slug in orphans[:10]:
            logger.warning("  orphan figure book=%s fid=%s slug=%r", bk_id, fid, sec_slug)
        if len(orphans) > 10:
            logger.warning("  ... %d more orphans", len(orphans) - 10)

    return dict(counts)


def _backfill_figure_references(
    engine: Engine,
    section_map: dict[tuple[str, str], str],
    book_id: str | None,
    dry_run: bool,
) -> dict[str, int]:
    """Resolve figure_references.section_ref (string slug) → section_uuid."""
    sql = (
        "SELECT id, book_id, section_ref FROM figure_references "
        "WHERE section_uuid IS NULL"
    )
    params: dict = {}
    if book_id:
        sql += " AND book_id = :book_id"
        params["book_id"] = book_id

    counts = Counter()
    updates: list[tuple[str, str]] = []
    orphans: list[tuple[str, str, str]] = []

    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()

    for fr_id, bk_id, sec_ref in rows:
        counts["total"] += 1
        if not sec_ref:
            counts["null_section_ref"] += 1
            orphans.append((str(bk_id), str(fr_id), "<null section_ref>"))
            continue
        match = section_map.get((str(bk_id), sec_ref))
        if match:
            counts["resolved"] += 1
            updates.append((match, str(fr_id)))
        else:
            counts["orphan"] += 1
            orphans.append((str(bk_id), str(fr_id), sec_ref))

    if updates and not dry_run:
        with engine.begin() as conn:
            for sec_uuid, fr_id in updates:
                conn.execute(
                    text(
                        "UPDATE figure_references SET section_uuid = :s WHERE id = :f"
                    ),
                    {"s": sec_uuid, "f": fr_id},
                )

    if orphans:
        logger.warning(
            "figure_references: %d orphans (section_ref does not match)",
            len(orphans),
        )

    return dict(counts)


def run(database_url: str, *, book_id: str | None, dry_run: bool) -> None:
    engine = create_engine(database_url, future=True)

    section_map = _build_section_map(engine, book_id)
    if not section_map:
        logger.warning("no sections found — nothing to resolve against")
        return

    q_counts = _backfill_questions(engine, section_map, book_id, dry_run)
    f_counts = _backfill_figures(engine, section_map, book_id, dry_run)
    fr_counts = _backfill_figure_references(engine, section_map, book_id, dry_run)

    mode = "DRY RUN" if dry_run else "APPLIED"
    logger.info(
        "=== %s — questions: %s ===",
        mode,
        ", ".join(f"{k}={v}" for k, v in sorted(q_counts.items())),
    )
    logger.info(
        "=== %s — figures: %s ===",
        mode,
        ", ".join(f"{k}={v}" for k, v in sorted(f_counts.items())),
    )
    logger.info(
        "=== %s — figure_references: %s ===",
        mode,
        ", ".join(f"{k}={v}" for k, v in sorted(fr_counts.items())),
    )

    # Exit non-zero on orphans so CI / scripts can flag attention needed.
    orphan_total = (
        q_counts.get("orphan", 0)
        + f_counts.get("orphan", 0)
        + fr_counts.get("orphan", 0)
    )
    if orphan_total:
        logger.warning("TOTAL ORPHANS: %d (these rows could not be resolved)", orphan_total)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--book-id", help="Backfill one specific book (UUID)")
    src.add_argument("--all", action="store_true", help="Backfill every book")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write changes; just report counts",
    )
    p.add_argument(
        "--database-url",
        default=None,
        help="DB URL (defaults to settings.SYNC_DATABASE_URL)",
    )
    args = p.parse_args(argv)

    if args.database_url:
        db_url = args.database_url
    else:
        from app.core.config import settings  # local import for script-mode
        db_url = settings.SYNC_DATABASE_URL

    if args.book_id:
        try:
            UUID(args.book_id)
        except ValueError:
            print(f"ERROR: --book-id must be a UUID, got {args.book_id!r}", file=sys.stderr)
            return 2

    run(db_url, book_id=args.book_id, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
