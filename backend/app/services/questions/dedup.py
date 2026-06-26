"""Cross-section dedup safety net for question extraction.

The v3 worker is section-aligned, so duplicates SHOULD be impossible by
construction — each Gemini call sees only one section's pages. In practice
two edge cases still produce duplicates:

  1. Schema page ranges overlap (section 8.4 ends p.26, 8.5 starts p.25)
     → the same question on p.25 gets extracted by both calls
  2. The same exercise is physically reprinted in two chapters (unusual but
     happens in revision-heavy textbooks)

This module provides a single ``dedup_bank(session, bank_id)`` entrypoint that
the worker calls at the end of extraction. The first occurrence (earliest
page) wins; later occurrences are deleted with a per-row reason logged for
the diagnostic UI.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.question import Question

logger = logging.getLogger(__name__)


_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
# TeX commands like \text{C}, \mu, \frac{1}{2}, \,, etc. need stripping
# before fingerprinting so the same question with different LaTeX
# whitespace/units doesn't fingerprint to two distinct hashes.
_TEX_BRACED = re.compile(r"\\[a-zA-Z]+\{[^}]*\}")     # \text{C}, \mathrm{X}
_TEX_CMD = re.compile(r"\\[a-zA-Z]+")                  # \mu, \times, \frac
_TEX_SYM = re.compile(r"\\[^a-zA-Z]")                  # \,  \;  \!  \\
_DOLLARS = re.compile(r"\$+")
_BRACES = re.compile(r"[{}]")


def _fingerprint(raw_text: str) -> str:
    """Stable hash over the leading 240 chars of normalised text.

    Strips TeX commands so $25 \\mu C$ and $25\\,\\mu C$ collapse to the
    same fingerprint. Otherwise two transcriptions of the same question
    with different LaTeX spacing slip past dedup.
    """
    s = (raw_text or "").lower()
    s = _TEX_BRACED.sub(" ", s)
    s = _TEX_CMD.sub(" ", s)
    s = _TEX_SYM.sub(" ", s)
    s = _DOLLARS.sub(" ", s)
    s = _BRACES.sub(" ", s)
    s = _NON_ALNUM.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return hashlib.sha1(s[:240].encode("utf-8")).hexdigest()


def dedup_bank(session: Session, bank_id: UUID) -> dict[str, Any]:
    """Drop duplicate Question rows within a bank. Returns a stats dict.

    Stats: ``{checked, kept, dropped, groups: [{fingerprint, kept_id, dropped_ids}]}``
    """
    rows = (
        session.execute(
            select(Question)
            .where(Question.bank_id == bank_id)
            .order_by(Question.page_start.asc().nullslast(), Question.created_at.asc())
        )
        .scalars()
        .all()
    )

    by_fp: dict[str, list[Question]] = {}
    for q in rows:
        fp = _fingerprint(q.raw_text or "")
        by_fp.setdefault(fp, []).append(q)

    dropped = 0
    groups: list[dict[str, Any]] = []
    for fp, group in by_fp.items():
        if len(group) <= 1:
            continue
        keeper = group[0]      # earliest page wins
        losers = group[1:]
        for q in losers:
            session.delete(q)
            dropped += 1
        groups.append({
            "fingerprint": fp,
            "kept_id": str(keeper.id),
            "kept_section": keeper.section_ref,
            "dropped_ids": [str(q.id) for q in losers],
            "dropped_sections": [q.section_ref for q in losers],
        })

    if dropped:
        session.commit()
        logger.info("dedup_bank %s — dropped %s duplicates across %s groups",
                    bank_id, dropped, len(groups))

    return {
        "checked": len(rows),
        "kept": len(rows) - dropped,
        "dropped": dropped,
        "groups": groups,
    }
