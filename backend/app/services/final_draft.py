"""Final Draft seeder + operations — Phase 3.2.

Two responsibilities:

1) ``seed_draft_items_from_merge`` — convert the build_final_merge output
   into a flat ordered list of authoring items. Each item carries a stable
   id used by the composer for drag-drop reorder.

2) ``apply_operation`` — pure function that mutates an items list per a
   typed operation dict. Used by the PATCH endpoint. Operations are
   declarative so the frontend can replay them without server round-trips
   for instant feedback.
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.content_stream import resolve_block_figures
from app.services.final_merge import build_final_merge


def _new_id() -> str:
    """Stable per-item id used by the composer. Short prefix helps with
    debugging draft JSON in the DB."""
    return "it_" + uuid.uuid4().hex[:12]


def _is_chip(block: dict[str, Any]) -> bool:
    return block.get("t") in ("example_ref", "exercise_ref", "question_ref")


# LaTeX commands that take an immediate label argument with NO braces and so
# MUST be followed by a space (e.g. \angle BAC). Models frequently glue the
# label on (\angleBAC), which KaTeX/OMML parse as ONE undefined control
# sequence → the whole $$…$$ block fails to render and shows raw source in
# BOTH the Preview and the DOCX export. Re-inserting the space makes the math
# valid again. Idempotent: \angle BAC (already spaced) is unaffected because
# the lookahead requires a letter IMMEDIATELY after the command.
_GLUED_CMD_RE = re.compile(r"\\(angle|triangle)(?=[A-Za-z])")


def _deglue_latex(value: Any) -> Any:
    """Recursively repair glued label-commands (\\angleBAC → \\angle BAC) in
    every string field of an item. Walks dicts/lists so it covers eq/text
    blocks, question raw_text/solution_text, custom_text, captions, etc.
    Safe on prose: the regex only matches the literal \\angle / \\triangle
    commands, which never appear outside LaTeX."""
    if isinstance(value, str):
        return _GLUED_CMD_RE.sub(r"\\\1 ", value)
    if isinstance(value, list):
        return [_deglue_latex(v) for v in value]
    if isinstance(value, dict):
        return {k: _deglue_latex(v) for k, v in value.items()}
    return value


async def seed_draft_items_from_merge(
    session: AsyncSession,
    book_id: UUID,
    *,
    prefer_regen: bool = True,
) -> list[dict[str, Any]]:
    """Build the ordered list of items for a fresh draft.

    Mirrors what the Final view shows:
      - Chips with an in-doc target section render the CHILD SECTION
        inline at the chip's exact position — a small sub-heading, then
        the child's blocks, figures, and numbered questions. The child
        is removed from the standalone section sequence (no duplicate
        render later) and "consumed" tracking is global.
      - Chips whose target is missing/already-consumed render as a label
        pill fallback (no silent drop).
      - Children referenced by NO chip render at the END of their declared
        parent (schema order) — never dropped.
      - Section heading echo (first h3 matching section title) is dropped.
      - Embedded figures are interleaved with theory blocks by
        placement_block_idx.
      - Standalone (orphan) questions still come after the theory body.
    """
    doc = await build_final_merge(session, book_id, prefer_regen=prefer_regen)
    section_ids_in_doc: set[str] = {s["section_id"] for s in doc["sections"]}
    sec_by_id: dict[str, dict[str, Any]] = {
        s["section_id"]: s for s in doc["sections"]
    }
    items: list[dict[str, Any]] = []

    # Book-level label → unattached-figure map. The embedder sometimes marks a
    # figure "unattached" (no inline anchor found, e.g. when a section's theory
    # text was too OCR-garbled to anchor against) — yet the theory STILL
    # carries a `fig` placeholder block for it in the CORRECT section (e.g.
    # "Figure 6.1" in Symmetry). The extraction/regen review view binds the
    # image to that placeholder BY LABEL; we do the same here so Preview /
    # Composer / DOCX match it, instead of exiling the figure to the trailing
    # "Unattached figures" tray. Anything not label-bound stays in the tray.
    def _norm_label(s: str) -> str:
        return re.sub(r"[^a-z0-9.]", "", (s or "").lower())

    _unattached_by_label: dict[str, dict[str, Any]] = {}
    for _f in (doc.get("unattached_figures") or []):
        _lbl = _norm_label(_f.get("figure_number") or _f.get("label") or "")
        if _lbl and _lbl not in _unattached_by_label:
            _unattached_by_label[_lbl] = _f
    _label_bound_ids: set[str] = set()  # figure ids consumed via label-binding

    # Derive chip-based parent→children mapping. A chip in section X
    # pointing to in-doc section Y declares Y as a child of X. First
    # claim wins (cycle guard — a child section can have at most one
    # parent). Children NOT referenced by any chip fall through to the
    # top-level emit loop and render in their natural schema position.
    parent_of_child: dict[str, str] = {}
    ordered_children_of: dict[str, list[str]] = {}
    for parent_sec in doc["sections"]:
        psid = parent_sec["section_id"]
        for b in (parent_sec.get("blocks") or []):
            if not _is_chip(b):
                continue
            target = (b.get("section_id") or "").strip()
            if (
                target
                and target != psid
                and target in section_ids_in_doc
                and target not in parent_of_child
            ):
                parent_of_child[target] = psid
                ordered_children_of.setdefault(psid, []).append(target)

    # Tracks section_ids already emitted via inline-at-chip recursion so
    # the top-level emit loop skips them (would otherwise duplicate).
    consumed: set[str] = set()

    def _emit_section(
        sec: dict[str, Any],
        *,
        depth: int,
        parent_render_level: int = 0,
    ) -> None:
        """Emit one section's full content. Recurses on chips to inline
        child sections; safe against cycles via the `consumed` set.

        Heading level math:
          - depth == 0 (top-level call from outer loop) → use the
            section's schema-declared `level` so the doc's natural
            hierarchy is preserved.
          - depth >= 1 (inline render at parent's chip position) →
            render exactly one level under the parent's render level.
            Decoupling from `sec.level` here protects against schemas
            where a child's declared level is over-deep relative to its
            parent (illustrations declared at level 3 nested under a
            level-2 section would otherwise render as h4; user expects
            h3 — one level under parent's h2).
        Capped at h6 so Markdown heading limits aren't exceeded.
        """
        section_id = sec["section_id"]
        if section_id in consumed:
            return
        consumed.add(section_id)

        title = sec.get("section_title") or section_id
        if depth <= 0:
            base_level = int(sec.get("level") or 0)
            level = max(1, min(6, base_level))
        else:
            level = max(1, min(6, parent_render_level + 1))

        items.append({
            "id": _new_id(),
            "type": "section_heading",
            "parent_section_id": section_id,
            "section_id": section_id,
            "title": title,
            "level": level,
            "regen": sec.get("block_source") == "regen",
        })

        # Theory blocks interleaved with inline figures and inlined
        # questions (the latter come from the chip↔question merge done
        # server-side in build_final_merge). Dedupe leading h3 echo.
        blocks: list[dict[str, Any]] = list(sec.get("blocks") or [])
        figures = list(sec.get("embedded_figures") or [])
        inlined_by_idx = dict(sec.get("inlined_questions_by_block_idx") or {})

        # Helper — emit inlined questions at an anchor key, but for each
        # question whose `section_ref` points to an in-doc CHILD section,
        # render the FULL child section in its place (heading + blocks +
        # remaining questions) at this anchor. This is the real "chip
        # at exact position" behaviour:
        #   - The chip blocks themselves are stripped from `blocks` by
        #     `build_final_merge`'s chip↔question merge before we get
        #     them, so chip-position can no longer be detected via
        #     iteration of `blocks`.
        #   - Instead, the merge anchors one of the child's questions
        #     into `inlined_by_idx` at the parent's surviving block
        #     index AT/BEFORE the original chip. That anchor IS the
        #     chip's position; the migrated question carries the child
        #     section_ref. We use it as the trigger to inline the
        #     child here, in place of the migrated question.
        #   - Multiple inlined questions at the same anchor pointing to
        #     the same child collapse into one child render (consumed
        #     tracking prevents duplicate emit).
        def _emit_inlined_at(anchor_key: str) -> None:
            for q in inlined_by_idx.get(anchor_key, []):
                child_ref = (q.get("section_ref") or "").strip()
                if (
                    child_ref
                    and child_ref != section_id
                    and child_ref in sec_by_id
                    and child_ref not in consumed
                ):
                    _emit_section(sec_by_id[child_ref], depth=depth + 1, parent_render_level=level)
                    continue
                items.append({
                    "id": _new_id(),
                    "type": "question",
                    "parent_section_id": section_id,
                    "question": q,
                })

        if blocks and blocks[0].get("t") == "h3":
            h3_text = (blocks[0].get("c") or "").strip().lower()
            if h3_text == title.strip().lower():
                blocks = blocks[1:]
                shifted_figs = []
                for f in figures:
                    idx = f.get("placement_block_idx")
                    if idx is None:
                        shifted_figs.append(f)
                    elif idx == 0:
                        shifted_figs.append({**f, "placement_block_idx": None})
                    else:
                        shifted_figs.append({**f, "placement_block_idx": idx - 1})
                figures = shifted_figs
                # Shift inlined question anchors too
                shifted_inlined: dict[str, list[dict[str, Any]]] = {}
                for k, qs in inlined_by_idx.items():
                    try:
                        ki = int(k)
                    except (TypeError, ValueError):
                        continue
                    if ki == 0:
                        # was after the dropped h3 → demote to start
                        shifted_inlined.setdefault("-1", []).extend(qs)
                    elif ki < 0:
                        shifted_inlined.setdefault(str(ki), []).extend(qs)
                    else:
                        shifted_inlined.setdefault(str(ki - 1), []).extend(qs)
                inlined_by_idx = shifted_inlined

        figures_by_idx: dict[int, list[dict[str, Any]]] = {}
        trailing_figs: list[dict[str, Any]] = []
        for f in figures:
            idx = f.get("placement_block_idx")
            if idx is None:
                trailing_figs.append(f)
            else:
                figures_by_idx.setdefault(int(idx), []).append(f)

        # Inlined questions BEFORE any block (anchor "-1") — each one
        # gets the child-inlining treatment via `_emit_inlined_at`.
        _emit_inlined_at("-1")

        for i, b in enumerate(blocks):
            # Chip handling — render the TARGET child section's full
            # content inline at the chip's exact position (small sub-
            # heading + child's blocks + figures + numbered questions).
            # The child is added to `consumed` so the outer loop won't
            # re-emit it as a standalone section. Fallback to a chip
            # label item when target is missing or already consumed
            # (cycle / multi-parent guard — never silently drops the
            # chip's existence).
            if _is_chip(b):
                target = (b.get("section_id") or "").strip()

                # Emit any FIGURE anchored at this chip's index regardless
                # of inline-vs-fallback path — figures placed by the
                # embedder at the chip's position are not duplicated by
                # the child-section emit.
                def _emit_chip_figures() -> None:
                    for f in figures_by_idx.get(i, []):
                        items.append({
                            "id": _new_id(),
                            "type": "figure",
                            "parent_section_id": section_id,
                            "figure": f,
                        })

                if target and target in sec_by_id and target not in consumed:
                    # INLINE PATH — render the full child section at the
                    # chip position. Keep parent-anchored figures.
                    _emit_section(sec_by_id[target], depth=depth + 1, parent_render_level=level)
                    _emit_chip_figures()
                    continue
                # FALLBACK PATH — chip's target missing / already
                # consumed elsewhere. Emit the chip block (renders as a
                # label pill on the frontend) + any anchored figures and
                # inlined questions (child-aware via `_emit_inlined_at`).
                items.append({
                    "id": _new_id(),
                    "type": "block",
                    "parent_section_id": section_id,
                    "block": b,
                })
                _emit_chip_figures()
                _emit_inlined_at(str(i))
                continue
            # Conditional fig-block suppression: drop the theory
            # extractor's `fig` placeholder block when a figure item is
            # already rendering ADJACENT to it (at this index OR the
            # previous index). The figure_embedder places figures at
            # placement_block_idx=N meaning "render after block N", so a
            # fig block sitting at index N+1 immediately follows that
            # render and would visually duplicate the image (image +
            # muted "📷 caption" callout for the same figure).
            #
            # When no adjacent figure exists, KEEP the fig block — its
            # muted callout signals to the reader that a figure was
            # extracted by the theory worker but the embedder couldn't
            # link an actual image to this spot. Previously fig blocks
            # were unconditionally suppressed in renderers, which caused
            # silent gaps for unlinked labelled figures.
            if isinstance(b, dict) and b.get("t") == "fig":
                has_adjacent_figure = (
                    bool(figures_by_idx.get(i))
                    or bool(figures_by_idx.get(i - 1))
                )
                if has_adjacent_figure:
                    # Emit any figure item(s) anchored AT this exact
                    # index. Figures anchored at i-1 already rendered
                    # immediately before this block (via the previous
                    # iteration's `figures_by_idx.get(i)` emit), so no
                    # additional emit needed here.
                    for f in figures_by_idx.get(i, []):
                        items.append({
                            "id": _new_id(),
                            "type": "figure",
                            "parent_section_id": section_id,
                            "figure": f,
                        })
                    _emit_inlined_at(str(i))
                    continue
                # No adjacent figure → try to BIND a figure to this
                # placeholder BY LABEL (matches the extraction/regen review
                # view). If a label-matched unattached figure exists, emit its
                # image here and skip the empty placeholder; otherwise keep
                # the placeholder as before.
                _flbl = _norm_label(b.get("label") or b.get("c") or "")
                _bound = _unattached_by_label.get(_flbl) if _flbl else None
                if _bound is not None:
                    _bid = str(_bound.get("figure_id") or _bound.get("id") or "")
                    if _bid and _bid not in _label_bound_ids:
                        _label_bound_ids.add(_bid)
                        items.append({
                            "id": _new_id(),
                            "type": "figure",
                            "parent_section_id": section_id,
                            "figure": _bound,
                        })
                        _emit_inlined_at(str(i))
                        continue
                # No adjacent figure and no label match → keep the fig block
                # as a visible placeholder. Fall through to the emit-block path.
            # Resolve block + its anchored figures into ordered nodes via the
            # single positional-truth resolver. For non-list blocks (and lists
            # without interior char-offset figures) this returns exactly
            # [block, fig, fig...] — identical to the previous stack-after-
            # block behaviour. For a LIST with interior figures, the list is
            # split at item boundaries so figures interleave between items
            # (matching TheoryView). See content_stream.resolve_block_figures.
            for node in resolve_block_figures(b, figures_by_idx.get(i, [])):
                if node["kind"] == "figure":
                    items.append({
                        "id": _new_id(),
                        "type": "figure",
                        "parent_section_id": section_id,
                        "figure": node["figure"],
                    })
                else:
                    items.append({
                        "id": _new_id(),
                        "type": "block",
                        "parent_section_id": section_id,
                        "block": node["block"],
                    })
            _emit_inlined_at(str(i))

        for f in trailing_figs:
            items.append({
                "id": _new_id(),
                "type": "figure",
                "parent_section_id": section_id,
                "figure": f,
            })

        for q in sec.get("questions") or []:
            items.append({
                "id": _new_id(),
                "type": "question",
                "parent_section_id": section_id,
                "question": q,
            })

        # End-of-section trailing children — any section declared as a
        # child of THIS section (via a chip pointer above) that wasn't
        # already consumed inline gets emitted here at depth+1. Catches
        # the case where the schema declares a child but the parent's
        # theory doesn't carry a chip pointer to it — we still render
        # it visibly inside its parent, never silently drop. Preserves
        # chip-declared schema order via `ordered_children_of`.
        for child_sid in ordered_children_of.get(section_id, []):
            if child_sid not in consumed and child_sid in sec_by_id:
                _emit_section(sec_by_id[child_sid], depth=depth + 1, parent_render_level=level)

    # Top-level emit — walk the doc's section order; `_emit_section`
    # skips any section already pulled in via inline-at-chip recursion
    # (consumed set is global to this draft seed). Children declared
    # by chips render under their parents; children declared only by
    # schema (no chip) render in their natural schema position OR via
    # the parent's end-of-section trailing emit above.
    for sec in doc["sections"]:
        _emit_section(sec, depth=0)

    # Emit unattached figures at the END of the items list so they
    # remain visible in Preview / Composer / DOCX / Markdown. These are
    # figures the embedder couldn't place in any section (no label
    # match, no anchor match, no question_no match, no page→section
    # resolution). Without surfacing them here, the user has no way to
    # see them in the document view — they only appear in the Figures
    # tab. Rendered with a synthetic parent_section_id so the front-end
    # can group them under an "Unattached figures" heading.
    # Drop any figure already label-bound to an inline placeholder above, so
    # it isn't ALSO shown in the trailing tray (no duplicates).
    unattached = [
        f for f in (doc.get("unattached_figures") or [])
        if str(f.get("figure_id") or f.get("id") or "") not in _label_bound_ids
    ]
    if unattached:
        # Synthetic section heading so the tray sits visually distinct.
        items.append({
            "id": _new_id(),
            "type": "section_heading",
            "parent_section_id": "__unattached__",
            "section_id": "__unattached__",
            "title": "Unattached Figures",
            "level": 2,
            "regen": False,
        })
        for f in unattached:
            items.append({
                "id": _new_id(),
                "type": "figure",
                "parent_section_id": "__unattached__",
                "figure": f,
            })

    # Final pass: repair glued LaTeX label-commands (\angleBAC → \angle BAC)
    # across every item, so math renders in BOTH the Preview (KaTeX) and the
    # DOCX export (OMML) — they both consume these exact items.
    items = [_deglue_latex(it) for it in items]
    return items


# ---------------------------------------------------------------------------
# Operations — applied by PATCH endpoint
# ---------------------------------------------------------------------------

class OperationError(ValueError):
    """Raised when an operation refers to an unknown item id or has a bad
    payload. The API translates these into 400 responses."""


def apply_operation(
    items: list[dict[str, Any]],
    op: dict[str, Any],
) -> list[dict[str, Any]]:
    """Apply a single typed operation to the items list. Returns a NEW
    list (does not mutate the input).

    Supported operations:
      {op: "reorder",   id, after_id | "start"}   move item next to another
      {op: "remove",    id}                       drop one item
      {op: "edit_item", id, patch: {...}}         shallow-merge patch
      {op: "insert_custom_text", after_id | "start", content}
      {op: "insert_existing", after_id | "start", item: {...complete item dict...}}
    """
    name = op.get("op")
    new = list(items)
    if name == "reorder":
        target_id = op.get("id")
        after_id = op.get("after_id", "start")
        if not target_id:
            raise OperationError("reorder: missing id")
        moved = _pop_by_id(new, target_id)
        if moved is None:
            raise OperationError(f"reorder: unknown id {target_id}")
        _insert_after(new, after_id, moved)
        return new
    if name == "remove":
        target_id = op.get("id")
        if not target_id:
            raise OperationError("remove: missing id")
        if _pop_by_id(new, target_id) is None:
            raise OperationError(f"remove: unknown id {target_id}")
        return new
    if name == "edit_item":
        target_id = op.get("id")
        patch = op.get("patch") or {}
        if not target_id or not isinstance(patch, dict):
            raise OperationError("edit_item: id and patch required")
        for it in new:
            if it.get("id") == target_id:
                # shallow-merge — caller can target nested fields via the
                # appropriate key (e.g. patch={"block": {...new block...}}).
                it.update(patch)
                return new
        raise OperationError(f"edit_item: unknown id {target_id}")
    if name == "insert_custom_text":
        content = op.get("content") or ""
        item = {
            "id": _new_id(),
            "type": "custom_text",
            "parent_section_id": None,
            "content": content,
        }
        _insert_after(new, op.get("after_id", "start"), item)
        return new
    if name == "insert_existing":
        provided = op.get("item")
        if not isinstance(provided, dict) or "type" not in provided:
            raise OperationError("insert_existing: item dict with `type` required")
        item = {**provided, "id": _new_id()}
        _insert_after(new, op.get("after_id", "start"), item)
        return new
    raise OperationError(f"unknown operation: {name}")


def _pop_by_id(arr: list[dict[str, Any]], item_id: str) -> dict[str, Any] | None:
    for i, it in enumerate(arr):
        if it.get("id") == item_id:
            return arr.pop(i)
    return None


def _insert_after(
    arr: list[dict[str, Any]],
    after_id: str,
    item: dict[str, Any],
) -> None:
    if after_id == "start":
        arr.insert(0, item)
        return
    for i, it in enumerate(arr):
        if it.get("id") == after_id:
            arr.insert(i + 1, item)
            return
    # Unknown anchor → append at end (safest no-op fallback)
    arr.append(item)
