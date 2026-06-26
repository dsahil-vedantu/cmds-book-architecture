"""Deterministic parent/child block deduplication for theory extraction.

Theory Worker Unit 5. Replaces 4 layered patches that all relied on Gemini
honoring the PARENT vs LEAF rule:
  - `is_container` flag (extract.py:545)
  - PARENT vs LEAF prompt block (extractor.txt §50-82)
  - Container empty force-pass (extract.py:632-634)
  - Tautological "paranoid post-write check" (extract.py:641-651 — dead code)

This pass runs ONCE after parallel theory extraction completes. It walks
the section tree and removes any parent block whose content also appears
in any descendant section (cross-level — direct child, grandchild, etc.).

Architecture (locked Q1-Q5):
  Q1 — detection: content-hash equality (SHA1 of normalized content; fast,
       exact, no false positives, no fuzzy threshold to tune).
  Q2 — direction: always strip from parent (child is the specific section,
       parent should hold only its own original prose).
  Q3 — depth:    all ancestors ↔ all descendants (grandparent → grandchild).
  Q4 — runs in:  extract.py tail next to linker (Unit 11 promotes later).
  Q5 — types:    all block types (p, eq, list, def, fig, table, example,
                 *_ref, h*). Duplication can happen in any type.

Properties:
  - Deterministic: same input → same output.
  - Idempotent:    running twice strips no additional blocks.
  - Failure-safe:  if anything raises mid-run, the caller catches and
                   leaves the original blocks intact (no destructive write
                   without a successful completion).
  - Observable:    returns per-section strip counts for logging.

Not in scope for Unit 5:
  - Linker chip injection (lives in example_linker, runs AFTER dedup).
  - Figure embedding (lives in figure_embedder, runs AFTER dedup).
  - Cat A / excluded sections (this pass only touches `Section.blocks`
    of theory-eligible sections).
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.section import Section
from app.schemas.analyser import BookSchema, SchemaSection

logger = logging.getLogger(__name__)


# ─── Hashing ──────────────────────────────────────────────────────


_WS_RE = re.compile(r"\s+")


def _normalize_text(s: Any) -> str:
    """Normalize for hash comparison.

    Lowercase + collapse whitespace + strip. Catches Gemini emitting the
    same content with different capitalization or inconsistent spacing.
    """
    if not isinstance(s, str):
        s = str(s) if s is not None else ""
    return _WS_RE.sub(" ", s).strip().lower()


def _block_hash(block: dict) -> str | None:
    """Compute a stable hash for a single block.

    The hash includes the block type AND its content so two blocks of
    different types but identical text don't collide (e.g. an `eq` block
    "x=1" vs a `p` block "x=1" are NOT duplicates — they render differently).

    Returns None if the block has no extractable content (defensive — we
    don't strip empty/malformed blocks; they fall through unchanged).
    """
    if not isinstance(block, dict):
        return None

    btype = (block.get("t") or block.get("type") or "").strip().lower()
    if not btype:
        return None

    # Per block type, collect the fields whose content uniquely identifies
    # the block. Keep this aligned with `invariant_splitter.paragraphs_to_blocks`.
    parts: list[str] = [btype]

    if btype in ("p", "body", "h1", "h2", "h3", "kp", "eq", "heading"):
        parts.append(_normalize_text(block.get("c") or block.get("content")))
    elif btype == "def":
        parts.append(_normalize_text(block.get("term")))
        parts.append(_normalize_text(block.get("c") or block.get("content")))
    elif btype == "list":
        items = block.get("items") or []
        for it in items:
            parts.append(_normalize_text(it))
    elif btype in ("fig", "figure"):
        # caption + content distinguish figures
        parts.append(_normalize_text(block.get("caption")))
        parts.append(_normalize_text(block.get("c") or block.get("content")))
    elif btype == "table":
        parts.append(_normalize_text(block.get("caption")))
        headers = block.get("headers") or []
        if isinstance(headers, list):
            for h in headers:
                parts.append(_normalize_text(h))
        rows = block.get("rows") or []
        if isinstance(rows, list):
            for r in rows:
                if isinstance(r, list):
                    for c in r:
                        parts.append(_normalize_text(c))
                else:
                    parts.append(_normalize_text(r))
    elif btype == "example":
        parts.append(_normalize_text(block.get("label")))
        parts.append(_normalize_text(block.get("prob")))
        eqs = block.get("eqs") or []
        if isinstance(eqs, list):
            for e in eqs:
                parts.append(_normalize_text(e))
    elif btype in ("example_ref", "exercise_ref", "question_ref"):
        # Reference chips: identified by their target section id / label.
        # Treat as unique per (type, section_id, label).
        parts.append(_normalize_text(block.get("section_id")))
        parts.append(_normalize_text(block.get("label")))
    else:
        # Unknown block type — hash the whole dict deterministically.
        # Better than dropping (we'd lose the chance to detect dupes).
        for k in sorted(block.keys()):
            v = block.get(k)
            parts.append(f"{k}={_normalize_text(v)}")

    # Empty content → no useful hash. Skip these (they fall through unchanged).
    payload = " ".join(parts)
    if payload == btype or len(payload) <= len(btype) + 2:
        return None
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


# ─── Tree walk ────────────────────────────────────────────────────


def _collect_descendant_hashes(
    section: SchemaSection,
    blocks_by_id: dict[str, list[dict]],
) -> set[str]:
    """All block hashes from this section's descendants (NOT including itself).

    Recursive — includes children, grandchildren, etc. (Q3 = depth-unlimited).
    """
    hashes: set[str] = set()

    def walk(node: SchemaSection):
        for child in node.subsections or []:
            for b in blocks_by_id.get(child.id, []):
                h = _block_hash(b)
                if h is not None:
                    hashes.add(h)
            walk(child)

    walk(section)
    return hashes


# ─── Public API ───────────────────────────────────────────────────


@dataclass
class DedupReport:
    """What dedup did. Returned for observability/logging."""
    total_sections_scanned: int = 0
    sections_with_strips: int = 0
    total_blocks_stripped: int = 0
    per_section: dict[str, int] = field(default_factory=dict)  # section_id -> count stripped

    def summary(self) -> str:
        if self.total_blocks_stripped == 0:
            return f"dedup: scanned={self.total_sections_scanned}, no duplicates found"
        return (
            f"dedup: scanned={self.total_sections_scanned}, "
            f"sections_with_strips={self.sections_with_strips}, "
            f"blocks_stripped={self.total_blocks_stripped}"
        )


def dedup_theory_blocks(
    session: Session,
    book_uuid,
    schema: BookSchema,
) -> DedupReport:
    """Strip blocks from each section that also appear in any descendant.

    Reads `Section.blocks` for every section in the schema, computes hash
    sets, removes parent's duplicates, writes cleaned blocks back. One
    transaction per book — all writes commit together.

    Failure-safe: if any step raises, the caller (extract.py tail) catches
    and the original blocks survive (no partial commits).
    """
    report = DedupReport()

    # Load every Section.blocks for this book into memory once.
    rows = session.execute(
        select(Section).where(Section.book_id == book_uuid)
    ).scalars().all()
    blocks_by_id: dict[str, list[dict]] = {
        r.section_id: list(r.blocks or []) for r in rows
    }
    row_by_id: dict[str, Section] = {r.section_id: r for r in rows}

    # Walk schema pre-order (parent before children). For each section,
    # collect descendant hashes and strip from this section's blocks.
    def visit(section: SchemaSection):
        report.total_sections_scanned += 1
        own_blocks = blocks_by_id.get(section.id)
        if own_blocks is None:
            # Section not in DB (e.g. excluded) — skip.
            for child in section.subsections or []:
                visit(child)
            return

        descendant_hashes = _collect_descendant_hashes(section, blocks_by_id)
        if not descendant_hashes:
            for child in section.subsections or []:
                visit(child)
            return

        kept: list[dict] = []
        stripped = 0
        for b in own_blocks:
            h = _block_hash(b)
            if h is not None and h in descendant_hashes:
                stripped += 1
                continue
            kept.append(b)

        if stripped > 0:
            # Update both the in-memory blocks_by_id (for downstream
            # descendant calculations) AND the DB row.
            blocks_by_id[section.id] = kept
            row = row_by_id.get(section.id)
            if row is not None:
                row.blocks = kept
                # SQLAlchemy JSON column needs explicit mark-dirty.
                flag_modified(row, "blocks")
            report.sections_with_strips += 1
            report.total_blocks_stripped += stripped
            report.per_section[section.id] = stripped

        for child in section.subsections or []:
            visit(child)

    for top in schema.sections or []:
        visit(top)

    # NOTE: do NOT commit here. The caller (extract.py tail) owns the
    # transaction. Committing inside this function would defeat any rollback
    # the caller might want if a subsequent tail step (linker, embedder,
    # finalize) fails. flush() makes the changes visible within this session
    # so downstream linker/embedder operate on the deduped state.
    if report.total_blocks_stripped > 0:
        session.flush()
        logger.info(
            "theory_dedup book=%s %s; per-section: %s",
            book_uuid, report.summary(), report.per_section,
        )
    else:
        logger.info("theory_dedup book=%s %s", book_uuid, report.summary())

    return report
