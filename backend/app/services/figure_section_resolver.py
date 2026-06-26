"""Resolve a figure's correct section.

Gemini's figure extractor assigns each figure to a section using visual
proximity to the nearest header. That's wrong when:

  1. The figure is visually under one header but the prose that DISCUSSES
     it lives under a sibling section (very common when sections share
     pages — e.g. INTRODUCTION ends on page 3 and the Diamond subsection
     also starts on page 3).
  2. Multiple LEAF subsections legitimately share the same single page,
     so page-proximity can't pick the right owner — alphabetical id
     tiebreak silently lands on the wrong leaf for every figure.

ARCHITECTURE — unified, deterministic, label/anchor-first:

    resolve_section_for_figure(...)
        ├─ LABELED    → search every theory section's blocks for the label
        │               (e.g. "Figure 8.1"). Tiebreak by caption text
        │               (the section that contains BOTH label and caption
        │               is the owner; others are cross-references).
        │               NO page fallback — too unreliable for labeled.
        │
        └─ UNLABELED  → search anchor_text in every section's blocks.
                        If unique → that section.
                        If short / ambiguous / no match → page-proximity
                        BUT only assign if exactly ONE leaf section
                        contains the page. Multiple leaves → UNASSIGNED.

Outputs explicit reasons. When no high-confidence answer exists, returns
the Gemini fallback with reason set so callers can surface ambiguity to
the user instead of silently mis-placing the figure.

Pure: same input → same output. No DB writes, no Gemini calls.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.services.figure_normalizer import normalize_for_match

logger = logging.getLogger(__name__)


_MIN_ANCHOR_LEN = 10  # below this, false-positive risk is too high
# For labeled-figure caption tiebreak: minimum normalized caption length
# below which we don't trust caption as a distinguishing signal.
_MIN_CAPTION_LEN = 8


@dataclass
class SectionResolutionResult:
    """Outcome of resolving one figure's section by anchor text.

    `resolved_section_ref` is the section_id slug the figure should be
    assigned to. Empty string means resolution failed (keep Gemini's
    original assignment).
    """
    resolved_section_ref: str
    resolved_section_uuid: Any | None
    reason: str
    matched_sections: list[str]


def _section_blocks_text(section_row: Any) -> str:
    """Flatten a Section row's blocks into one searchable text string.

    Delegates to the canonical block_text.section_blocks_text helper which
    handles ALL block types (def, example, table, list, p, h3, kp, fig, ...)
    by recursively collecting every string-valued field. This avoids the
    recurring class of bug where each new block type required custom
    flattening in the resolver (e.g. def's term: content format).
    """
    from app.services.block_text import section_blocks_text
    blocks = section_row.blocks if hasattr(section_row, "blocks") else None
    return section_blocks_text(blocks)


def resolve_section_by_anchor(
    *,
    anchor_text: str | None,
    page_number: int | None,
    fallback_section_ref: str,
    fallback_section_uuid: Any | None,
    theory_sections: list[Any],
) -> SectionResolutionResult:
    """Find which theory section's blocks contain the anchor_text.

    Args:
        anchor_text: prose describing the figure (from Gemini metadata)
        page_number: PDF page where the figure appears
        fallback_section_ref: Gemini's visual-proximity guess (kept if no match)
        fallback_section_uuid: UUID for the same fallback section
        theory_sections: list of Section rows (must have .section_id, .id,
            .page_start, .page_end, .blocks fields)

    Returns:
        SectionResolutionResult with resolved or fallback assignment.
    """
    # If anchor is missing or too short → keep fallback
    if not anchor_text or len(anchor_text.strip()) < _MIN_ANCHOR_LEN:
        return SectionResolutionResult(
            resolved_section_ref=fallback_section_ref,
            resolved_section_uuid=fallback_section_uuid,
            reason="no_anchor_or_too_short",
            matched_sections=[],
        )

    anchor_norm = normalize_for_match(anchor_text)
    if not anchor_norm:
        return SectionResolutionResult(
            resolved_section_ref=fallback_section_ref,
            resolved_section_uuid=fallback_section_uuid,
            reason="anchor_normalized_empty",
            matched_sections=[],
        )

    matched: list[Any] = []
    for sec in theory_sections:
        section_text = _section_blocks_text(sec)
        if not section_text:
            continue
        section_norm = normalize_for_match(section_text)
        if anchor_norm in section_norm:
            matched.append(sec)

    matched_ids = [getattr(s, "section_id", "?") for s in matched]

    if not matched:
        return SectionResolutionResult(
            resolved_section_ref=fallback_section_ref,
            resolved_section_uuid=fallback_section_uuid,
            reason="anchor_not_found_in_any_section",
            matched_sections=[],
        )

    if len(matched) == 1:
        sec = matched[0]
        return SectionResolutionResult(
            resolved_section_ref=getattr(sec, "section_id", "") or "",
            resolved_section_uuid=getattr(sec, "id", None),
            reason="unique_anchor_match",
            matched_sections=matched_ids,
        )

    # Multiple matches — disambiguate by page_number when available.
    if page_number is not None:
        for sec in matched:
            ps = getattr(sec, "page_start", None)
            pe = getattr(sec, "page_end", None)
            if ps is None or pe is None:
                continue
            if ps <= page_number <= pe:
                return SectionResolutionResult(
                    resolved_section_ref=getattr(sec, "section_id", "") or "",
                    resolved_section_uuid=getattr(sec, "id", None),
                    reason="multi_match_disambig_by_page",
                    matched_sections=matched_ids,
                )

    # Still ambiguous — keep fallback rather than guess.
    return SectionResolutionResult(
        resolved_section_ref=fallback_section_ref,
        resolved_section_uuid=fallback_section_uuid,
        reason="ambiguous_multi_match",
        matched_sections=matched_ids,
    )


# ─── Label-based resolution (for LABELED figures) ────────────────────


def _extract_label_number(label: str) -> str | None:
    """From a label like 'Figure 8.1' or 'Fig 8.1a' extract '8.1' / '8.1a'.

    Returns None if no number-pattern is found.
    """
    if not label:
        return None
    # Match: optional letters, then digit(s).digit(s) optionally followed
    # by more dot-segments, optionally followed by a single letter suffix.
    m = re.search(r'(\d+(?:\.\d+)*[a-zA-Z]?)', label)
    return m.group(1) if m else None


def _build_label_patterns(label: str) -> list[str]:
    """Build normalized search patterns from a figure label.

    For 'Figure 8.1' returns lower-cased variants the prose might use:
    'figure 8.1', 'fig 8.1', 'fig. 8.1', '(fig 8.1)', '(figure 8.1)'.
    """
    num = _extract_label_number(label)
    if not num:
        return []
    patterns = [
        f"figure {num}",
        f"fig {num}",
        f"fig. {num}",
        f"(fig {num})",
        f"(figure {num})",
        f"(fig. {num})",
    ]
    # normalize_for_match handles case + whitespace, but pre-lowercasing
    # avoids relying on that internal contract.
    return [normalize_for_match(p) for p in patterns if p]


def _resolve_by_label(
    *,
    label: str,
    caption: str | None,
    fallback_section_ref: str,
    fallback_section_uuid: Any | None,
    theory_sections: list[Any],
) -> SectionResolutionResult:
    """Find the section that OWNS this labeled figure.

    PRIMARY signal — section TITLE in caption:
        The figure's caption almost always names what the figure depicts;
        the section that OWNS the figure is usually named for that same
        topic. "Structure of diamond" → Diamond section, "Structure of
        graphite" → Graphite section. This signal is robust EVEN WHEN
        the figure caption block was mis-placed by theory extraction,
        because we read section TITLES (set by schema generator), not
        section BLOCKS (set by theory extractor).

    SECONDARY signal — label in section blocks:
        Only used when no section title matches the caption. The section
        whose prose mentions "Figure 8.1" is likely the owner. If
        multiple sections mention it (cross-refs), keep the Gemini
        fallback rather than guess.

    We deliberately do NOT use page proximity for labeled figures — page
    proximity is what caused the original bug.
    """
    caption_norm = normalize_for_match(caption or "")

    # ── PRIMARY-A: "of <topic>" pattern ────────────────────────────
    # Scientific figure captions overwhelmingly follow the pattern
    # "<generic descriptor> of <specific topic>" — "Structure of
    # diamond", "Chemical properties of ammonia", "Manufacture of
    # glass". The SPECIFIC TOPIC after the LAST " of " is what the
    # figure depicts, and that's the section it belongs to. We extract
    # that topic phrase and prefer sections whose title overlaps it.
    #
    # Without this, generic recurring titles like "Chemical Properties"
    # (a sub-heading present in many sections) silently win over the
    # actual topic ("Ammonia") because they happen to be longer.
    topic = _extract_of_topic(caption_norm)
    if topic and len(topic) >= 4:
        topic_owners: list[tuple[int, Any]] = []
        for sec in theory_sections:
            title = getattr(sec, "title", "") or ""
            title_norm = normalize_for_match(title)
            if not title_norm or len(title_norm) < 4:
                continue
            if title_norm in _GENERIC_TITLE_BLACKLIST:
                continue
            # Match if title is a substring of the topic, OR the topic
            # is a substring of the title. Either direction is a strong
            # owner signal:
            #   topic "hydrogen sulphide" in title "hydrogen sulphide h2s"
            #   title "ammonia" in topic "ammonia"
            if title_norm in topic or topic in title_norm:
                # Score by length of the overlap (longer = more specific).
                overlap_len = min(len(title_norm), len(topic))
                topic_owners.append((overlap_len, sec))

        if topic_owners:
            topic_owners.sort(key=lambda t: -t[0])
            sec = topic_owners[0][1]
            return SectionResolutionResult(
                resolved_section_ref=getattr(sec, "section_id", "") or "",
                resolved_section_uuid=getattr(sec, "id", None),
                reason=(
                    "of_topic_unique"
                    if len(topic_owners) == 1
                    else "of_topic_longest"
                ),
                matched_sections=[
                    getattr(s[1], "section_id", "?") for s in topic_owners
                ],
            )

    # ── PRIMARY-B: section title appears anywhere in caption ───────
    # Falls through when no "of <topic>" structure or no section
    # matches the topic. Same logic as before — title in caption with
    # word-boundary check, generic titles excluded.
    if caption_norm and len(caption_norm) >= _MIN_CAPTION_LEN:
        title_owners: list[tuple[int, Any]] = []
        for sec in theory_sections:
            title = getattr(sec, "title", "") or ""
            title_norm = normalize_for_match(title)
            if not title_norm or len(title_norm) < 4:
                continue
            if title_norm in _GENERIC_TITLE_BLACKLIST:
                continue
            if _contains_as_phrase(caption_norm, title_norm):
                title_owners.append((len(title_norm), sec))

        if title_owners:
            title_owners.sort(key=lambda t: -t[0])
            sec = title_owners[0][1]
            return SectionResolutionResult(
                resolved_section_ref=getattr(sec, "section_id", "") or "",
                resolved_section_uuid=getattr(sec, "id", None),
                reason=(
                    "title_in_caption_unique"
                    if len(title_owners) == 1
                    else "title_in_caption_longest"
                ),
                matched_sections=[
                    getattr(s[1], "section_id", "?") for s in title_owners
                ],
            )

    # ── SECONDARY: label appears in section blocks ─────────────────
    patterns = _build_label_patterns(label)
    if not patterns:
        return SectionResolutionResult(
            resolved_section_ref=fallback_section_ref,
            resolved_section_uuid=fallback_section_uuid,
            reason="label_unparseable",
            matched_sections=[],
        )

    matched: list[Any] = []
    for sec in theory_sections:
        text = _section_blocks_text(sec)
        if not text:
            continue
        norm = normalize_for_match(text)
        if any(pat in norm for pat in patterns):
            matched.append(sec)

    matched_ids = [getattr(s, "section_id", "?") for s in matched]

    if not matched:
        return SectionResolutionResult(
            resolved_section_ref=fallback_section_ref,
            resolved_section_uuid=fallback_section_uuid,
            reason="label_not_found_in_any_section",
            matched_sections=[],
        )

    if len(matched) == 1:
        sec = matched[0]
        return SectionResolutionResult(
            resolved_section_ref=getattr(sec, "section_id", "") or "",
            resolved_section_uuid=getattr(sec, "id", None),
            reason="unique_label_match",
            matched_sections=matched_ids,
        )

    # Multiple sections mention the label and no title-in-caption signal
    # could disambiguate. Keep fallback — silently picking one would
    # reintroduce the original bug.
    return SectionResolutionResult(
        resolved_section_ref=fallback_section_ref,
        resolved_section_uuid=fallback_section_uuid,
        reason="ambiguous_label_match",
        matched_sections=matched_ids,
    )


def _extract_of_topic(caption_norm: str) -> str | None:
    """Extract the specific topic from a caption's 'of <topic>' phrase.

    Scientific figure captions follow a near-universal pattern:
        "Figure X.Y <descriptor> of <topic>"
            "Structure of diamond"   → "diamond"
            "Chemical properties of ammonia" → "ammonia"
            "Manufacture of glass"   → "glass"
            "Frasch process for the extraction of Sulphur"
                                     → "sulphur"

    Returns the substring after the LAST " of " (so "extraction of
    Sulphur" inside a longer caption still picks "Sulphur"). Strips
    trailing punctuation. Returns None if no " of " phrase exists or
    the topic looks too long to be a noun phrase (>80 chars).
    """
    if not caption_norm:
        return None
    last_of = caption_norm.rfind(" of ")
    if last_of == -1:
        return None
    topic = caption_norm[last_of + 4:].strip()
    # Strip trailing connective phrases — "ammonia in laboratory" → "ammonia".
    # Conservative: just cut at the first " and ", " in ", " for ", " from "
    # which mark continuation phrases that don't refer to the topic itself.
    for sep in (" and ", " in ", " for ", " from ", " by ", " with ", " at "):
        idx = topic.find(sep)
        if idx != -1:
            topic = topic[:idx].strip()
    if not topic or len(topic) > 80:
        return None
    return topic


# Titles that match too many captions to be a reliable owner signal.
# Lowercase, post-normalize_for_match form.
_GENERIC_TITLE_BLACKLIST = frozenset({
    "introduction", "summary", "overview", "preface", "contents",
    "preliminaries", "preamble", "abstract", "epilogue", "conclusion",
    "appendix", "glossary", "index", "references", "bibliography",
    "exercises", "problems", "questions", "review", "example", "examples",
})


def _contains_as_phrase(haystack: str, needle: str) -> bool:
    """True if `needle` appears in `haystack` at word boundaries.

    Inputs are already normalized (lowercase, whitespace-collapsed,
    NFKD). Word boundary = start of string, end of string, or whitespace.
    Prevents "ion" matching inside "introduction" or "carbon" matching
    inside "hydrocarbon".
    """
    if not needle or not haystack:
        return False
    if needle not in haystack:
        return False
    # Use simple boundary scan; for the small strings here regex would
    # work too but adds escaping concerns.
    n = len(needle)
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx == -1:
            return False
        before_ok = (idx == 0) or (not haystack[idx - 1].isalnum())
        after_idx = idx + n
        after_ok = (after_idx == len(haystack)) or (
            not haystack[after_idx].isalnum()
        )
        if before_ok and after_ok:
            return True
        start = idx + 1


# ─── Safe page-proximity (for UNLABELED fallback) ────────────────────


def _safe_page_proximity(
    *,
    page_number: int | None,
    theory_sections: list[Any],
    fallback_section_ref: str,
    fallback_section_uuid: Any | None,
) -> SectionResolutionResult:
    """Page-proximity fallback that REFUSES to guess on shared-page leaves.

    Returns the unique LEAF section containing this page (a leaf = no
    other candidate's page range is strictly inside this section's).
    If multiple leaves share the page, returns the fallback with a
    distinct reason so the figure can be surfaced for review instead of
    silently landing on the alphabetically-first leaf.
    """
    if page_number is None:
        return SectionResolutionResult(
            resolved_section_ref=fallback_section_ref,
            resolved_section_uuid=fallback_section_uuid,
            reason="no_page_for_proximity",
            matched_sections=[],
        )

    containing: list[Any] = []
    for s in theory_sections:
        ps = getattr(s, "page_start", None)
        pe = getattr(s, "page_end", None)
        if ps is None or pe is None:
            continue
        if ps <= page_number <= pe:
            containing.append(s)

    if not containing:
        return SectionResolutionResult(
            resolved_section_ref=fallback_section_ref,
            resolved_section_uuid=fallback_section_uuid,
            reason="page_proximity_no_match",
            matched_sections=[],
        )

    # Leaf = no other candidate's range is strictly inside this one.
    def is_leaf(sec: Any) -> bool:
        ps = getattr(sec, "page_start", 0)
        pe = getattr(sec, "page_end", 0)
        sec_range = pe - ps
        for other in containing:
            if other is sec:
                continue
            ops = getattr(other, "page_start", 0)
            ope = getattr(other, "page_end", 0)
            if ps <= ops and ope <= pe and (pe - ps) > (ope - ops):
                return False
        return True

    leaves = [s for s in containing if is_leaf(s)]
    leaf_ids = [getattr(s, "section_id", "?") for s in leaves]

    if len(leaves) == 1:
        sec = leaves[0]
        return SectionResolutionResult(
            resolved_section_ref=getattr(sec, "section_id", "") or "",
            resolved_section_uuid=getattr(sec, "id", None),
            reason="page_proximity_unique_leaf",
            matched_sections=leaf_ids,
        )

    # Multiple leaves share this page → genuinely ambiguous. DO NOT pick
    # alphabetically (that was the bug). Surface via fallback.
    return SectionResolutionResult(
        resolved_section_ref=fallback_section_ref,
        resolved_section_uuid=fallback_section_uuid,
        reason="page_proximity_multiple_leaves",
        matched_sections=leaf_ids,
    )


# ─── Unified entry point ─────────────────────────────────────────────


def resolve_section_for_figure(
    *,
    is_labelled: bool,
    label: str | None,
    caption: str | None,
    anchor_text: str | None,
    page_number: int | None,
    fallback_section_ref: str,
    fallback_section_uuid: Any | None,
    theory_sections: list[Any],
) -> SectionResolutionResult:
    """Unified figure→section resolver.

    Routes to label-based resolution for LABELED figures or anchor-text
    resolution (with safe page-proximity fallback) for UNLABELED. See
    module docstring for full architecture.
    """
    if is_labelled:
        if not label:
            return SectionResolutionResult(
                resolved_section_ref=fallback_section_ref,
                resolved_section_uuid=fallback_section_uuid,
                reason="labelled_without_label",
                matched_sections=[],
            )
        return _resolve_by_label(
            label=label,
            caption=caption,
            fallback_section_ref=fallback_section_ref,
            fallback_section_uuid=fallback_section_uuid,
            theory_sections=theory_sections,
        )

    # Unlabeled — try anchor text first (the strong signal).
    anchor_result = resolve_section_by_anchor(
        anchor_text=anchor_text,
        page_number=page_number,
        fallback_section_ref=fallback_section_ref,
        fallback_section_uuid=fallback_section_uuid,
        theory_sections=theory_sections,
    )
    # If anchor resolution gave a real answer, use it.
    if anchor_result.reason in {
        "unique_anchor_match",
        "multi_match_disambig_by_page",
    }:
        return anchor_result

    # Anchor didn't yield → safe page-proximity fallback (refuses to
    # silently pick on shared-page leaves).
    return _safe_page_proximity(
        page_number=page_number,
        theory_sections=theory_sections,
        fallback_section_ref=fallback_section_ref,
        fallback_section_uuid=fallback_section_uuid,
    )


__all__ = [
    "resolve_section_for_figure",
    "resolve_section_by_anchor",
    "SectionResolutionResult",
]
