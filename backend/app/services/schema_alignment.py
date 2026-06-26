"""Schema-DB ID alignment.

When the schema is regenerated (POST /analyse on an existing book) or
edited (PATCH /api/books/{id}), the new schema may carry fresh
section_id strings while the DB already has Section rows with the old
extraction-time IDs. Letting the new IDs win would orphan extracted
content from its schema slot.

This helper walks the new schema and, for every node that matches an
existing DB section by (title + page range), force-keeps the existing
DB section_id. Unmatched nodes keep their fresh IDs.

After alignment:
  - DB section_ids never change post-extraction.
  - The schema's hierarchy can evolve freely (titles, order, parents,
    excluded vs included).
  - Frontend joins by section_id are stable.
  - figure_references.section_ref + questions.section_ref also remain
    valid because they point at the locked DB IDs.

Matching strategy (in priority order):
  1. Exact (case-folded, whitespace-normalized) title + same page_start.
  2. Same title + overlapping page range.
  3. Same page_start + page_end (handles renames where pages didn't move).
No match → leave the schema node's id unchanged.

Conflicts (two schema nodes match the same DB id) are resolved by
first match wins; later matches fall through to their original IDs.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

from app.schemas.analyser import BookSchema, ExcludedSection, SchemaSection

logger = logging.getLogger(__name__)


def _norm_title(t: str | None) -> str:
    """Normalize a title for matching: lowercase, collapse whitespace,
    strip trailing punctuation/dots/dashes."""
    if not t:
        return ""
    s = re.sub(r"\s+", " ", t).strip().lower()
    s = s.rstrip(" .:-—")
    return s


def _pages_overlap(
    a_start: int | None,
    a_end: int | None,
    b_start: int | None,
    b_end: int | None,
) -> bool:
    """True if two page ranges share at least one page."""
    if None in (a_start, a_end, b_start, b_end):
        return False
    return a_start <= b_end and b_start <= a_end  # type: ignore[operator]


def _build_existing_index(
    existing_sections: Iterable,
) -> tuple[dict, dict, dict]:
    """Build three lookups from existing DB Section rows:

      title_pagestart : {(norm_title, page_start) : section_id}
      title_only      : {norm_title : [(section_id, page_start, page_end), ...]}
      page_range      : {(page_start, page_end) : section_id}

    Only sections the worker successfully processed (status passed/failed)
    participate — pending/skipped rows shouldn't pin schema IDs since
    they may not represent real extracted content.
    """
    title_pagestart: dict[tuple[str, int], str] = {}
    title_only: dict[str, list[tuple[str, int | None, int | None]]] = {}
    page_range: dict[tuple[int, int], str] = {}

    for sec in existing_sections:
        status = getattr(sec, "status", "")
        if status not in ("passed", "failed"):
            continue
        sid = getattr(sec, "section_id", None)
        if not sid:
            continue
        title = _norm_title(getattr(sec, "title", ""))
        ps = getattr(sec, "page_start", None)
        pe = getattr(sec, "page_end", None)

        if title and ps is not None:
            title_pagestart.setdefault((title, ps), sid)
        if title:
            title_only.setdefault(title, []).append((sid, ps, pe))
        if ps is not None and pe is not None:
            page_range.setdefault((ps, pe), sid)

    return title_pagestart, title_only, page_range


def _match_node(
    node_title: str,
    node_ps: int | None,
    node_pe: int | None,
    title_pagestart: dict,
    title_only: dict,
    page_range: dict,
    consumed_ids: set[str],
) -> str | None:
    """Return an existing section_id that best matches this node, or None.

    Priority: title+page_start exact > title+page-overlap > page-range exact.
    Each existing section_id can only be claimed by one schema node
    (consumed_ids tracks first-match-wins).
    """
    title = _norm_title(node_title)

    # 1. Exact title + page_start
    if title and node_ps is not None:
        sid = title_pagestart.get((title, node_ps))
        if sid and sid not in consumed_ids:
            return sid

    # 2. Same title + overlapping page range
    if title:
        for sid, ps, pe in title_only.get(title, []):
            if sid in consumed_ids:
                continue
            if _pages_overlap(node_ps, node_pe, ps, pe):
                return sid

    # 3. Page range exact (handles renames where page span is the anchor)
    if node_ps is not None and node_pe is not None:
        sid = page_range.get((node_ps, node_pe))
        if sid and sid not in consumed_ids:
            return sid

    return None


def _uniquify_ids(schema: BookSchema) -> dict[str, str]:
    """Walk the schema and force every section.id to be UNIQUE.

    When the same id appears multiple times (Gemini occasionally emits
    duplicates, especially on multi-column pages where two sections sit
    on the same line and the slug derivation collides), the SECOND and
    subsequent occurrences get renamed with a numeric suffix
    (``-2``, ``-3``, ...). The first occurrence keeps its original id.

    Returns a ``{original_id : new_id}`` remap dict for any nodes that
    were renamed (empty when the schema was already unique).

    This pre-pass runs BEFORE alignment so the alignment helper sees a
    valid unique-id schema and never produces collisions downstream.
    Without it, a duplicate in Gemini's output silently propagated
    through alignment and caused two section_heading rows in the final
    draft to share the same id — the merge code then resolved BOTH
    section_heading lookups to the same DB row, dropping the second
    section's content from the user-visible output.
    """
    seen: set[str] = set()
    remap: dict[str, str] = {}

    def fresh_id(base: str) -> str:
        n = 2
        while True:
            candidate = f"{base}-{n}"
            if candidate not in seen:
                return candidate
            n += 1

    def walk(node: SchemaSection) -> None:
        sid = node.id or ""
        if sid in seen:
            new_sid = fresh_id(sid)
            remap[sid] = new_sid
            node.id = new_sid
            seen.add(new_sid)
            logger.warning(
                "schema_alignment: duplicate section.id %r in input schema "
                "— renamed second occurrence to %r (title=%r)",
                sid, new_sid, node.title,
            )
        elif sid:
            seen.add(sid)
        for sub in node.subsections:
            walk(sub)

    for s in schema.sections:
        walk(s)
    return remap


def _verify_unique_ids(schema: BookSchema) -> list[str]:
    """Post-alignment sanity check. Returns a list of any ids that
    appear more than once (empty when the schema is sound)."""
    counts: dict[str, int] = {}

    def walk(node: SchemaSection) -> None:
        sid = node.id or ""
        if sid:
            counts[sid] = counts.get(sid, 0) + 1
        for sub in node.subsections:
            walk(sub)

    for s in schema.sections:
        walk(s)
    return [sid for sid, n in counts.items() if n > 1]


def align_schema_ids_to_existing_sections(
    new_schema: BookSchema,
    existing_sections: Iterable,
) -> tuple[BookSchema, dict[str, str]]:
    """Walk new_schema and rewrite each node's id to an existing DB
    section_id when a match is found. Returns the (possibly mutated)
    schema plus a {old_id: new_id} remap dict for caller logging.

    The schema instance is mutated in place AND returned (callers can
    use either; we return for clarity at call sites).
    """
    # Pre-pass: uniquify any duplicate section ids that Gemini may have
    # emitted in the fresh schema. Without this the alignment loop can
    # silently produce a schema whose two different sections share the
    # same id, causing downstream lookups (final_merge, sections table
    # joins, frontend sorting) to collapse both sections into one row.
    pre_remap = _uniquify_ids(new_schema)

    title_pagestart, title_only, page_range = _build_existing_index(
        existing_sections
    )
    if not title_only and not page_range:
        return new_schema, dict(pre_remap)

    consumed: set[str] = set()
    remap: dict[str, str] = dict(pre_remap)

    def walk_section(node: SchemaSection) -> None:
        match = _match_node(
            node.title, node.page_start, node.page_end,
            title_pagestart, title_only, page_range, consumed,
        )
        if match is not None and match != node.id:
            # Defensive: if the matched DB id was already claimed by an
            # earlier node (shouldn't happen because _match_node honours
            # consumed_ids, but belt-and-braces), refuse to introduce a
            # duplicate.
            if match in consumed:
                return
            remap[node.id] = match
            node.id = match
            consumed.add(match)
        elif match is not None:
            # ID was already correct, still mark consumed so a sibling
            # with the same title doesn't steal it.
            consumed.add(match)
        for sub in node.subsections:
            walk_section(sub)

    def walk_excluded(node: ExcludedSection) -> None:
        # ExcludedSection has no `id` field — only the `sections` table
        # uses ids; excluded sections are referenced by title verbatim
        # in questions.section_ref. So nothing to align here; we walk
        # into subsections anyway in case future schema versions add ids.
        for sub in node.subsections:
            walk_excluded(sub)

    for s in new_schema.sections:
        walk_section(s)
    for ex in new_schema.excluded_sections:
        walk_excluded(ex)

    # Post-pass: final sanity check. If anything went wrong above and
    # the schema still has duplicate ids, uniquify them now. This is
    # the safety net — we should never persist a schema with duplicates.
    duplicates = _verify_unique_ids(new_schema)
    if duplicates:
        logger.warning(
            "schema_alignment: post-align still has %d duplicate id(s): %s — "
            "running second uniquify pass",
            len(duplicates), duplicates,
        )
        post_remap = _uniquify_ids(new_schema)
        remap.update(post_remap)

    if remap:
        logger.info(
            "schema_alignment: %d id remap(s) applied: %s",
            len(remap),
            ", ".join(f"{k}→{v}" for k, v in list(remap.items())[:5]),
        )

    return new_schema, remap
