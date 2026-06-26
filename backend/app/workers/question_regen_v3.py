"""Question regeneration worker — v3.

R2 (2026-05-08): new v3-quality regeneration worker that processes the
PREVIOUSLY EXTRACTED questions (not PDF re-OCR). For each source question,
calls Gemini with the `question_regenerator_v3` system prompt and persists
the generated variants as new rows in the questions table with
``regen_id = regen.id`` set.

Architecture:
  - One Gemini call per source question (cheap text-only call)
  - Concurrency: 4-wide via existing gemini_runtime semaphore
  - Heartbeat keeps the watchdog quiet during long regen runs
  - section_ref is ALWAYS stamped from the source question (Q4 rule mirrored)
  - regen_id flags the row as generated; originals have regen_id=NULL

What this worker does NOT do (deferred to later R-steps):
  - R3  Custom-instruction priority modes (override / layer / specific)
  - R4  API params (similarity_level, count, question_type) — hardcoded defaults for R2
  - R5  Migration for new QuestionRegeneration columns
  - R6  Section-level retry endpoint
  - R8+ Frontend changes
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.gemini_runtime import (
    call_gemini_text_only,
    call_gemini_text_with_images,
)
from app.core.heartbeat import Heartbeat
from app.models.figure import Figure
from app.models.figure_reference import FigureReference
from app.models.book import Book
from app.models.job import Job
from app.models.question import Question
from app.models.question_bank import QuestionBank
from app.models.question_regeneration import QuestionRegeneration
from app.services.prompt_loader import load_raw
from app.utils.json_parse import parse_json
from app.workers.celery_app import celery_app
from app.workers.runner import register as register_task

logger = logging.getLogger(__name__)

# Single sync engine + session factory (mirrors questions_v3.py).
_sync_engine = create_engine(
    settings.DATABASE_URL.replace("+aiosqlite", "").replace("+asyncpg", ""),
    pool_pre_ping=True,
)
SyncSession = sessionmaker(bind=_sync_engine, class_=Session, autoflush=False)

# Defaults for R2 — R4/R5 make these configurable per regen run via API/UI.
DEFAULT_SIMILARITY = "numbers_and_rephrase"
DEFAULT_COUNT = 1
DEFAULT_QUESTION_TYPE = "same_as_source"
DEFAULT_PRIORITY_MODE = "override"

# Priority mode is locked to override only.
_VALID_PRIORITY_MODES = {"override"}

# Valid similarity levels.
_VALID_SIMILARITY_LEVELS = {
    "numbers_only",
    "numbers_and_rephrase",
    "numbers_rephrase_add_concept",
    "new_question_same_topic",
    "same_topic_add_one_concept",
    "same_chapter_any_topic",
}


def _priority_mode_block(mode: str, custom_instructions: str) -> str:
    """Build the framing block that tells Gemini HOW to apply custom
    instructions. Mode is always override; parameter kept for compatibility.

    Returns "" if custom_instructions is empty/None.
    """
    txt = (custom_instructions or "").strip()
    if not txt:
        return ""
    header = (
        "PRIORITY MODE: OVERRIDE\n"
        "The custom_instructions below COMPLETELY REPLACE the default "
        "similarity-level behavior. Follow them above all other rules "
        "EXCEPT factual correctness (which always wins). The similarity "
        "level still selects which aspects are conceptually LOCKED, but "
        "every other generation choice (tone, structure, language, "
        "style, pattern) is dictated by these instructions."
    )
    return header + "\n\nCustom instructions:\n" + txt

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_TIMEOUT_S = 150
MAX_OUTPUT_TOKENS = 32768

# Completeness contract for question regen — every source question MUST get
# at least one variant. A single Gemini call sometimes returns empty (esp.
# multimodal/figure questions) or transiently fails; without a retry the
# source was silently skipped (0 variants), and a section-reseed even
# wiped the existing variants first, leaving the section blank. Bounded
# retry per source closes both gaps. Mirrors the extraction-side Pass-3
# philosophy: retry empties, never silently drop. Cost is bounded — extra
# calls only fire on sources that came back empty the first time.
_REGEN_SOURCE_MAX_ATTEMPTS = 3
_REGEN_SOURCE_BACKOFF_S = (1.0, 2.0)  # waits between attempts 1→2, 2→3

# Step 2 (chained diagram regen) — Pro for visual fidelity, low temperature
# for structurally-stable LaTeX/SVG output.
DIAGRAM_GEN_MODEL = "gemini-2.5-pro"
DIAGRAM_GEN_TEMPERATURE = 0.2

# Question kind enum allowed in the DB (matches Question.kind column).
_LEGACY_KINDS = {"exercise", "example", "problem", "mcq", "review", "other"}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _update_job(session: Session, job_id: UUID, **fields: Any) -> None:
    """Update a job row by id."""
    job = session.get(Job, job_id)
    if job is None:
        return
    for k, v in fields.items():
        if hasattr(job, k):
            setattr(job, k, v)
    session.commit()


def _update_regen(session: Session, regen_id: UUID, **fields: Any) -> None:
    """Update a QuestionRegeneration row by id."""
    r = session.get(QuestionRegeneration, regen_id)
    if r is None:
        return
    for k, v in fields.items():
        if hasattr(r, k):
            setattr(r, k, v)
    session.commit()


def _map_question_type_to_kind(qtype: str | None) -> str:
    """Map Gemini's question_type (14 types from the prompt) to the legacy
    Question.kind enum used by the DB.

    Conservative — anything unrecognised falls to "other". The full
    question_type string is preserved separately in Question.question_type.
    """
    if not qtype:
        return "exercise"
    q = qtype.lower().strip()
    if "scq" in q or "mcq" in q or "binary" in q or "assertion" in q:
        return "mcq"
    if "integer" in q or "numerical" in q or "fill" in q:
        return "exercise"
    if "subjective" in q or "comprehension" in q:
        return "exercise"
    if "matching" in q:
        return "exercise"
    if q in _LEGACY_KINDS:
        return q
    return "other"


def _flatten_source_section_refs(
    regen: QuestionRegeneration,
    bank_id: UUID,
    session: Session,
) -> list[str]:
    """Return the list of section_refs to regenerate from, based on regen
    scope.

    scope="bank"      → every section_ref that has at least one extracted
                        Question row (regen_id IS NULL) in this bank.
    scope="sections"  → use regen.section_refs verbatim.
    """
    if regen.scope == "sections":
        return list(regen.section_refs or [])

    rows = session.execute(
        select(Question.section_ref).where(
            Question.bank_id == bank_id,
            Question.regen_id.is_(None),
        ).distinct()
    ).all()
    return [r[0] for r in rows if r[0]]


def _build_user_prompt(
    source: Question,
    *,
    similarity_level: str,
    count: int,
    question_type: str,
    custom_instructions: str | None,
    priority_mode: str,
    subject: str | None,
    chapter: str | None,
    grade: str | None,
    board: str | None,
) -> str:
    """Build the user prompt for one source-question regeneration call.

    Format mirrors the "INPUTS YOU WILL RECEIVE" section in
    `question_regenerator_v3.txt`. When custom_instructions is present, a
    PRIORITY MODE block (R3) is prepended that tells Gemini HOW to apply
    them (override / layer_on_top / specific_aspects).
    """
    def _maybe(v: str | None, fallback: str = "infer from source") -> str:
        return v if (v and str(v).strip()) else fallback

    parts: list[str] = []

    # Priority-mode block (only if custom instructions present).
    pm_block = _priority_mode_block(priority_mode, custom_instructions or "")
    if pm_block:
        parts.append(pm_block)
        parts.append("")

    parts.extend([
        f"similarity_level    : {similarity_level}",
        f"question_type       : {question_type}",
        f"subject             : {_maybe(subject)}",
        f"chapter             : {_maybe(chapter)}",
        f"grade               : {_maybe(grade)}",
        f"board               : {_maybe(board)}",
        f"custom_instructions : {custom_instructions or '(none)'}",
        "",
        "source_question     :",
        (source.raw_text or "").strip(),
    ])
    if source.solution_text:
        parts.append("")
        parts.append("source_answer       :")
        parts.append(source.solution_text.strip())
    return "\n".join(parts)


def _load_source_image_bytes(
    session: Session,
    book_id: UUID,
    question_id: UUID,
) -> list[tuple[bytes, str]]:
    """Phase 4 — fetch image bytes for any figures attached to this
    question via figure_references. Returns [(bytes, mime), ...]. Empty
    when no images attached or none have stored bytes.

    Variant choice mirrors the embedder's rule: regen variant if approved,
    else original. Only includes references with placement_kind != hidden
    and placement_kind != unattached.
    """
    refs = (
        session.execute(
            select(FigureReference)
            .where(FigureReference.book_id == book_id)
            .where(FigureReference.question_id == question_id)
            .where(FigureReference.context == "question")
            .where(FigureReference.is_hidden.is_(False))
            .where(FigureReference.placement_kind != "unattached")
        )
        .scalars()
        .all()
    )
    if not refs:
        return []
    fig_ids = {r.figure_id for r in refs}
    figs = (
        session.execute(select(Figure).where(Figure.id.in_(fig_ids)))
        .scalars()
        .all()
    )
    out: list[tuple[bytes, str]] = []
    for f in figs:
        data = (
            f.regen_image_bytes
            if (f.regen_image_bytes and f.approved_at is not None)
            else f.image_bytes
        )
        if not data:
            continue
        mime = f.mime_type or "image/png"
        out.append((data, mime))
    return out


async def _regen_one_source(
    source: Question,
    *,
    system_prompt: str,
    similarity_level: str,
    count: int,
    question_type: str,
    custom_instructions: str | None,
    priority_mode: str,
    subject: str | None,
    chapter: str | None,
    grade: str | None,
    board: str | None,
    image_bytes_list: list[tuple[bytes, str]] | None = None,
    image_addendum_prompt: str | None = None,
) -> dict[str, Any]:
    """Single Gemini call to regenerate `count` variants from `source`.

    PHASE 4 — multimodal branch:
      When ``image_bytes_list`` is non-empty AND ``settings.MULTIMODAL_REGEN_ENABLED``
      is True, the call goes through ``call_gemini_text_with_images`` on Pro
      with the image_addendum appended to the system prompt. Each returned
      regen item then carries ``image_needs_regen`` + ``image_regen_reason``
      fields used downstream to optionally chain a figure regeneration.

      Otherwise the existing text-only Flash path runs (unchanged behaviour).

    Returns:
        {"ok": bool, "items": [<regen dict>...], "notes": str, "error": str}
    """
    user_prompt = _build_user_prompt(
        source,
        similarity_level=similarity_level,
        count=count,
        question_type=question_type,
        custom_instructions=custom_instructions,
        priority_mode=priority_mode,
        subject=subject,
        chapter=chapter,
        grade=grade,
        board=board,
    )

    # Decide path
    use_multimodal = bool(
        image_bytes_list
        and settings.MULTIMODAL_REGEN_ENABLED
    )

    # Bounded retry: a source MUST yield ≥1 variant. Retry on transient
    # failure OR a valid-but-empty response (0 usable items). Only after
    # exhausting attempts do we report failure — at which point the caller
    # records a visible gap in stats (never a silent drop).
    last_err = ""
    for attempt in range(1, _REGEN_SOURCE_MAX_ATTEMPTS + 1):
        try:
            if use_multimodal:
                # Append the image-addendum rules to the standard system prompt
                full_system = system_prompt
                if image_addendum_prompt:
                    full_system = system_prompt + "\n\n" + image_addendum_prompt
                raw = await asyncio.to_thread(
                    call_gemini_text_with_images,
                    system_prompt=full_system,
                    user_prompt=user_prompt,
                    image_bytes_list=image_bytes_list,
                    # Pro for multimodal — better visual reasoning than Flash
                    model="gemini-2.5-pro",
                    timeout_s=GEMINI_TIMEOUT_S,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                    temperature=0.4,
                )
            else:
                raw = await asyncio.to_thread(
                    call_gemini_text_only,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    model=GEMINI_MODEL,
                    timeout_s=GEMINI_TIMEOUT_S,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                    temperature=0.4,
                )
            data = parse_json(raw)
            if not isinstance(data, dict):
                last_err = "response was not a JSON object"
            else:
                items = list(data.get("regenerated") or [])
                notes = str(data.get("notes") or "")
                # Keep only items that have a non-empty question string.
                items = [it for it in items if isinstance(it, dict)
                         and (it.get("question") or "").strip()]
                if items:
                    # Normalise multimodal-only fields so they always exist.
                    for it in items:
                        if "image_needs_regen" not in it:
                            it["image_needs_regen"] = False
                        if "image_regen_reason" not in it:
                            it["image_regen_reason"] = ""
                    if attempt > 1:
                        logger.info(
                            "regen-v3 source q=%s recovered on attempt %d",
                            source.id, attempt,
                        )
                    return {"ok": True, "items": items, "notes": notes, "error": ""}
                # Valid JSON but zero usable items → retry.
                last_err = "empty result (0 usable variants)"
        except Exception as e:
            last_err = str(e)
            logger.warning(
                "regen-v3 single-source call failed (q=%s, multimodal=%s, "
                "attempt=%d/%d): %s",
                source.id, use_multimodal, attempt,
                _REGEN_SOURCE_MAX_ATTEMPTS, e,
            )
        # Backoff before the next attempt (skip after the final one).
        if attempt < _REGEN_SOURCE_MAX_ATTEMPTS:
            await asyncio.sleep(
                _REGEN_SOURCE_BACKOFF_S[
                    min(attempt - 1, len(_REGEN_SOURCE_BACKOFF_S) - 1)
                ]
            )

    logger.warning(
        "regen-v3 source q=%s produced NO variants after %d attempts: %s",
        source.id, _REGEN_SOURCE_MAX_ATTEMPTS, last_err,
    )
    return {"ok": False, "items": [], "notes": "",
            "error": f"no variants after {_REGEN_SOURCE_MAX_ATTEMPTS} attempts: {last_err}"}


async def _generate_latex_diagram_for_question(
    *,
    original_image_bytes_list: list[tuple[bytes, str]],
    regenerated_question_text: str,
    regenerated_solution_text: str,
) -> dict[str, Any] | None:
    """Step 2 (Dual-Step Chained Generation) — diagram regen.

    Given the ORIGINAL diagram image(s) plus the newly regenerated question
    text and its worked solution, ask Gemini 2.5 Pro to synthesize a
    compilable standalone LaTeX diagram AND a parallel inline SVG whose
    labels/values match the regenerated question.

    This runs as a SEPARATE chained call (not folded into the question-regen
    prompt) so neither task degrades the other. Pro + low temperature is used
    for structural fidelity. Returns the normalized diagram dict, or None when
    there are no images / the prompt is missing / the call fails (the caller
    treats None as "no diagram payload" and proceeds normally).
    """
    return await asyncio.to_thread(
        _generate_diagram_blocking,
        original_image_bytes_list=original_image_bytes_list,
        regenerated_question_text=regenerated_question_text,
        regenerated_solution_text=regenerated_solution_text,
    )


def _image_dimensions(
    image_bytes_list: list[tuple[bytes, str]] | None,
) -> tuple[int, int] | None:
    """Read (width, height) in px of the first source figure, or None."""
    if not image_bytes_list:
        return None
    try:
        import io as _io

        from PIL import Image

        with Image.open(_io.BytesIO(image_bytes_list[0][0])) as img:
            return img.size  # (w, h)
    except Exception:
        return None


def _build_diagram_user_prompt(
    q_text: str,
    sol_text: str,
    *,
    custom_instructions: str | None = None,
    previous_diagram: dict[str, Any] | None = None,
    source_size: tuple[int, int] | None = None,
    mode: str = "question",
) -> str:
    """Build the diagram-gen user prompt.

    mode="question": q_text = regenerated question, sol_text = worked solution.
    mode="theory":   q_text = regenerated theory content the figure illustrates,
                     sol_text = the figure's caption/label.

    For a user-driven reseed we also pass the CURRENT diagram (so the model
    refines rather than starts over) and the user's customization instruction at
    highest priority. When the original figure's pixel size is known, we pass it
    so the SVG/LaTeX mirror the source figure's aspect ratio.
    """
    if mode == "table":
        parts = [
            "Here is a composite TABLE figure (text + one or more embedded "
            "graphics) from a textbook, attached as an image. Rebuild ONLY its "
            "grid structure and text as a crisp vector SVG, transcribing all text "
            "VERBATIM, and leave each embedded graphic as a {{GRAPHIC_N}} <image> "
            "placeholder with a reported normalized bbox — exactly per your system "
            "instructions. Do NOT redraw the graphics yourself.",
            "",
            f"TABLE CAPTION / TITLE:\n{sol_text or '(none)'}",
        ]
        ctx = (q_text or "").strip()
        if ctx:
            parts += [
                "",
                "SURROUNDING CONTEXT (for disambiguating labels only — do not add "
                "any text that is not visible in the table image):\n" + ctx,
            ]
    elif mode == "theory":
        parts = [
            "Here is the newly regenerated THEORY content that the attached "
            "figure illustrates. Regenerate the figure as clean LaTeX + a "
            "parallel SVG so it matches the NEW theory (updated labels, values, "
            "structure), based on the original attached figure.",
            "",
            f"REGENERATED THEORY (what this figure must illustrate):\n{q_text}",
            "",
            f"FIGURE CAPTION / LABEL:\n{sol_text}",
        ]
    else:
        parts = [
            "Here is the newly regenerated question and its worked solution. "
            "Generate a corresponding LaTeX diagram and a parallel SVG rendering "
            "based on the original attached image context.",
            "",
            f"REGENERATED QUESTION:\n{q_text}",
            "",
            f"REGENERATED SOLUTION:\n{sol_text}",
        ]
    if source_size:
        w, h = source_size
        aspect = (w / h) if h else 1.0
        if mode == "table":
            # Tables are text-dense — a narrow canvas clips the prose column. Use
            # the source's actual pixel dimensions as the viewBox (1 unit = 1 px)
            # so there is ample room, and demand hard wrapping inside each column.
            parts += [
                "",
                f"CANVAS SIZE — the source table is {w}×{h} px. Set the SVG to "
                f'viewBox="0 0 {w} {h}" with width="{w}" height="{h}" so 1 unit = 1 '
                "source pixel. Lay out the grid and cells in these pixel coordinates. "
                "CRITICAL: keep every text line WELL INSIDE its column — wrap to a new "
                "<text> line (or <tspan x=… dy=…>) BEFORE the text reaches the column's "
                "right border, and never let any glyph cross a column divider or the "
                "outer table border. Leave a small even margin inside each cell.",
            ]
        else:
            # Suggest a viewBox that keeps the SAME aspect ratio, normalized to a
            # ~360px-wide canvas (legible when rasterized + embedded in Word).
            vw = 360
            vh = max(1, round(360 / aspect)) if aspect else 360
            parts += [
                "",
                "SOURCE FIGURE SIZE — match the book's format: the original diagram "
                f"is {w}×{h} px (width:height aspect ≈ {aspect:.2f}). Reproduce the "
                f"SAME shape and proportions: set the SVG to "
                f'viewBox="0 0 {vw} {vh}" with width="{vw}" height="{vh}", and lay '
                "out the LaTeX standalone with matching proportions and a small, "
                "even border — so the regenerated figure occupies the same size "
                "format as the source. Do NOT stretch or distort to a different "
                "aspect ratio.",
            ]
    if previous_diagram and (
        previous_diagram.get("latex_code") or previous_diagram.get("svg_preview")
    ):
        parts += [
            "",
            "CURRENT DIAGRAM (refine THIS — keep what is correct, change only what "
            "the customization instruction asks, stay consistent with the question):",
            "LaTeX:\n" + (previous_diagram.get("latex_code") or "(none)"),
        ]
    if custom_instructions and custom_instructions.strip():
        parts += [
            "",
            "USER CUSTOMIZATION INSTRUCTION (highest priority — apply this to the "
            "diagram while keeping it factually consistent with the question and "
            "obeying every SVG safety rule):",
            custom_instructions.strip(),
        ]
    return "\n".join(parts)


def _generate_diagram_blocking(
    *,
    original_image_bytes_list: list[tuple[bytes, str]],
    regenerated_question_text: str,
    regenerated_solution_text: str,
    custom_instructions: str | None = None,
    previous_diagram: dict[str, Any] | None = None,
    mode: str = "question",
    system_prompt_name: str = "latex_diagram_generator",
) -> dict[str, Any] | None:
    """Synchronous diagram generation (one blocking Gemini call). Shared by the
    question diagram path, the theory-figure regen (mode="theory"), and the
    composite-table vector rebuild (mode="table", system_prompt_name=
    "figures/table_structuring_svg")."""
    if not original_image_bytes_list:
        return None
    try:
        system_prompt = load_raw(system_prompt_name)
    except Exception as e:  # prompt file missing — degrade gracefully
        logger.warning("diagram prompt %r unavailable: %s", system_prompt_name, e)
        return None

    user_prompt = _build_diagram_user_prompt(
        regenerated_question_text,
        regenerated_solution_text,
        custom_instructions=custom_instructions,
        previous_diagram=previous_diagram,
        source_size=_image_dimensions(original_image_bytes_list),
        mode=mode,
    )

    try:
        raw = call_gemini_text_with_images(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_bytes_list=original_image_bytes_list,
            model=DIAGRAM_GEN_MODEL,          # Pro for high visual fidelity
            timeout_s=GEMINI_TIMEOUT_S,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            temperature=DIAGRAM_GEN_TEMPERATURE,  # low temp → structural stability
        )
        data = parse_json(raw)
        if isinstance(data, dict):
            graphics = data.get("graphics")
            return {
                "fallback_to_original": bool(data.get("fallback_to_original", False)),
                "subject": str(data.get("subject") or "").strip(),
                "latex_code": str(data.get("latex_code") or "").strip(),
                "svg_preview": str(data.get("svg_preview") or "").strip(),
                "description": str(data.get("description") or "").strip(),
                # Table path only: list of {"id", "bbox"} graphic regions to crop,
                # redraw, and embed into the SVG. Empty/absent for diagram paths.
                "graphics": graphics if isinstance(graphics, list) else [],
            }
    except Exception as e:
        logger.warning("LaTeX diagram generation call failed: %s", e)
    return None


def _validate_diagram_renderable(diagram: dict[str, Any]) -> None:
    """Probe that the model's SVG actually rasterizes (sync; run via to_thread).

    Mutates ``diagram`` in place: on render failure WHEN a rasterizer is
    available, flips ``fallback_to_original`` to True and appends a note so the
    UI shows the fallback card instead of a diagram that can't be exported. If
    no rasterizer is installed at all, leaves the diagram untouched (the browser
    can still render the SVG; the export keeps the original figure).
    """
    try:
        from app.services.svg_raster import (
            rasterize_svg_to_png,
            rasterizer_available,
        )
        if not rasterizer_available():
            return
        if rasterize_svg_to_png(diagram.get("svg_preview")) is None:
            diagram["fallback_to_original"] = True
            note = "auto-fallback: generated SVG failed to rasterize"
            desc = (diagram.get("description") or "").strip()
            diagram["description"] = f"{desc} ({note})" if desc else note
    except Exception as e:  # never let validation break the regen run
        logger.warning("diagram renderability validation errored: %s", e)


async def _attach_diagrams_to_items(
    items: list[dict[str, Any]],
    image_bytes_list: list[tuple[bytes, str]] | None,
) -> None:
    """Run Step 2 diagram regen for each regenerated item that has a source
    diagram, mutating each item dict in place with a ``regenerated_diagram``
    payload. No-op when multimodal is off or there are no source images.

    Called from the ASYNC worker context (after _regen_one_source, before the
    sync persist) so we never nest event loops — the guide's run_until_complete
    approach would crash inside the already-running asyncio loop.
    """
    if not (image_bytes_list and settings.MULTIMODAL_REGEN_ENABLED):
        return
    for it in items:
        q_text = (it.get("question") or "").strip()
        if not q_text:
            continue
        sol_text = (it.get("solution") or it.get("answer") or "").strip()
        diagram = await _generate_latex_diagram_for_question(
            original_image_bytes_list=image_bytes_list,
            regenerated_question_text=q_text,
            regenerated_solution_text=sol_text,
        )
        if diagram:
            # Layered-Hydration validation: confirm the model's SVG actually
            # rasterizes BEFORE we commit it, so the browser preview and the
            # Word export stay consistent. If a rasterizer is available but the
            # SVG fails to render, downgrade to fallback (keep original figure)
            # rather than shipping a diagram that previews but won't export.
            if (
                not diagram.get("fallback_to_original")
                and diagram.get("svg_preview")
            ):
                await asyncio.to_thread(_validate_diagram_renderable, diagram)
            it["regenerated_diagram"] = diagram


def reseed_diagram_for_question(
    question_id: UUID,
    custom_instructions: str | None = None,
) -> dict[str, Any] | None:
    """Synchronous, single-question diagram RESEED (custom-instruction driven).

    Mirrors the "Reseed this section" pattern but targets one regenerated
    question's diagram. Loads the question's inherited source figure image and
    its CURRENT diagram, regenerates with the user's instruction (refining the
    existing diagram), validates renderability, and persists the new payload
    into ``qc_local["regenerated_diagram"]``. Returns the new diagram dict, or
    ``{"_error": ...}`` for caller-handled failure cases.

    Called from the async API endpoint via ``asyncio.to_thread`` so the blocking
    Gemini call never stalls the event loop.
    """
    from sqlalchemy.orm.attributes import flag_modified

    with SyncSession() as session:
        q = session.get(Question, question_id)
        if q is None:
            return {"_error": "not_found"}
        # Load the original diagram image. Prefer the regen question's own
        # inherited figure_references, but the post-regen embedder pass can
        # re-materialize (and drop) those, so fall back to the SOURCE question's
        # figure — which is exactly what the original auto-gen used.
        image_bytes_list = _load_source_image_bytes(session, q.book_id, question_id)
        if not image_bytes_list:
            src_qid = getattr(q, "source_question_id", None)
            if src_qid:
                image_bytes_list = _load_source_image_bytes(
                    session, q.book_id, src_qid
                )
        if not image_bytes_list:
            return {"_error": "no_figure"}

        prev = None
        if isinstance(q.qc_local, dict):
            rd = q.qc_local.get("regenerated_diagram")
            if isinstance(rd, dict):
                prev = rd

        diagram = _generate_diagram_blocking(
            original_image_bytes_list=image_bytes_list,
            regenerated_question_text=q.raw_text or "",
            regenerated_solution_text=q.solution_text or "",
            custom_instructions=custom_instructions,
            previous_diagram=prev,
        )
        if not diagram:
            return {"_error": "generation_failed"}

        _validate_diagram_renderable(diagram)

        qc = dict(q.qc_local) if isinstance(q.qc_local, dict) else {}
        qc["regenerated_diagram"] = diagram
        qc["image_regen"] = {
            "needed": not diagram.get("fallback_to_original", False),
            "reason": diagram.get("description") or "Reseeded diagram",
        }
        q.qc_local = qc
        flag_modified(q, "qc_local")
        session.commit()
        return diagram


def _blocks_to_text(
    blocks: list[dict[str, Any]] | None,
    *,
    around: int | None = None,
    window: int = 4,
) -> str:
    """Flatten theory blocks to plain text. When ``around`` (a block index) is
    given, only the ±window blocks near it are used (the text the figure
    actually illustrates); otherwise a capped prefix of the section."""
    if not blocks:
        return ""
    if around is not None:
        lo = max(0, around - window)
        hi = min(len(blocks), around + window + 1)
        sel = blocks[lo:hi]
    else:
        sel = blocks[:12]
    out: list[str] = []
    for b in sel:
        if not isinstance(b, dict):
            continue
        t = b.get("t")
        if t in ("p", "h3", "kp"):
            c = b.get("c")
            if c:
                out.append(str(c))
        elif t == "def":
            term = (b.get("term") or "").strip()
            c = (b.get("c") or "").strip()
            out.append((f"{term}: {c}" if term else c).strip())
        elif t == "eq":
            c = b.get("c")
            if c:
                out.append(f"Equation: {c}")
        elif t == "list":
            out.extend(str(x) for x in (b.get("items") or []))
        elif t == "fig":
            c = b.get("c")
            if c:
                out.append(f"[Figure: {c}]")
    return "\n".join(p for p in out if p).strip()[:4000]


def _theory_context_for_figure(session: Session, fig: Any) -> str:
    """Build the regenerated-theory context for a theory figure: the caption
    plus the regenerated blocks around the figure's placement. Falls back to the
    original section blocks when the section has no regeneration yet."""
    from app.models.figure_reference import FigureReference
    from app.models.regeneration import Regeneration
    from app.models.section import Section

    ref = (
        session.execute(
            select(FigureReference)
            .where(FigureReference.figure_id == fig.id)
            .where(FigureReference.context == "theory")
        )
        .scalars()
        .first()
    )
    section_ref = (ref.section_ref if ref else None) or fig.section_id
    block_idx = ref.placement_block_idx if ref else None

    blocks: list[dict[str, Any]] | None = None
    regen = (
        session.execute(
            select(Regeneration)
            .where(Regeneration.book_id == fig.book_id)
            .order_by(Regeneration.created_at.desc())
        )
        .scalars()
        .first()
    )
    if regen and isinstance(regen.blocks_by_section, dict):
        blocks = regen.blocks_by_section.get(section_ref)
    if not blocks:
        sec = (
            session.execute(
                select(Section)
                .where(Section.book_id == fig.book_id)
                .where(Section.section_id == section_ref)
            )
            .scalars()
            .first()
        )
        blocks = sec.blocks if sec else []

    parts: list[str] = []
    if getattr(fig, "caption", None):
        parts.append(f"Caption: {fig.caption}")
    body = _blocks_to_text(blocks, around=block_idx, window=4)
    if body:
        parts.append(body)
    return "\n\n".join(parts)[:4500]


# ── Engine routing ─────────────────────────────────────────────────────────

# semantic_type → regen engine. Composite "table" figures become a crisp vector
# grid with the graphic embedded; diagrams/charts (schematics, flowcharts,
# graphic organizers) go through the LaTeX/SVG vector engine; everything else
# (realistic illustrations, photos) keeps the image-model redraw — the image
# model reproduces organic graphics well but garbles dense text.
_ENGINE_BY_SEMANTIC_TYPE = {
    "table": "table_embed",
    "diagram": "vector",
    "chart": "vector",
    "illustration": "image",
    "figure": "image",
}


def pick_regen_engine(fig: Any) -> str:
    """Choose the regeneration engine for a figure by ``semantic_type``.

    Returns one of ``"table_embed" | "vector" | "image"``. Unknown types fall
    back to ``"image"`` (the historical default). Callers must still honor
    ``settings.FIGURE_ENGINE_ROUTING_ENABLED`` — when that flag is off, route
    everything through the image model regardless of this result.
    """
    stype = (getattr(fig, "semantic_type", None) or "").strip().lower()
    return _ENGINE_BY_SEMANTIC_TYPE.get(stype, "image")


def regenerate_theory_figure(
    figure_id: UUID,
    custom_instructions: str | None = None,
) -> dict[str, Any]:
    """Manual, on-demand regen of a THEORY figure to match the regenerated
    theory — same LaTeX/SVG engine as question diagrams. NEVER auto-runs; only
    when the user hits Regenerate.

    On success rasterizes the SVG to PNG and stores it as the figure's APPROVED
    regen variant (``regen_image_bytes`` + ``approved_at``), so Preview /
    Composer / Export pick it up via their existing regen-variant path. The
    LaTeX/SVG payload is kept in ``regen_meta`` for re-use/inspection. On a
    fallback verdict the original figure is left untouched.
    """
    from datetime import datetime, timezone

    from sqlalchemy.orm.attributes import flag_modified

    from app.models.figure import Figure
    from app.services.svg_raster import rasterize_svg_to_png

    with SyncSession() as session:
        fig = session.get(Figure, figure_id)
        if fig is None:
            return {"_error": "not_found"}
        if not fig.image_bytes:
            return {"_error": "no_image"}
        image_bytes_list = [(fig.image_bytes, fig.mime_type or "image/png")]
        context = _theory_context_for_figure(session, fig)
        caption = (getattr(fig, "caption", None) or "").strip()
        prev = None
        if isinstance(fig.regen_meta, dict):
            rd = fig.regen_meta.get("diagram")
            if isinstance(rd, dict):
                prev = rd

        diagram = _generate_diagram_blocking(
            original_image_bytes_list=image_bytes_list,
            regenerated_question_text=context,
            regenerated_solution_text=caption,
            custom_instructions=custom_instructions,
            previous_diagram=prev,
            mode="theory",
        )
        if not diagram:
            return {"_error": "generation_failed"}
        _validate_diagram_renderable(diagram)
        if diagram.get("fallback_to_original") or not diagram.get("svg_preview"):
            return {"_error": "fallback", "description": diagram.get("description") or ""}

        png = rasterize_svg_to_png(diagram["svg_preview"])
        if not png:
            return {"_error": "rasterize_failed"}

        fig.regen_image_bytes = png
        fig.approved_at = datetime.now(timezone.utc)
        meta = dict(fig.regen_meta) if isinstance(fig.regen_meta, dict) else {}
        meta["diagram"] = diagram
        meta["source"] = "theory_latex_diagram"
        meta["engine"] = "vector"
        meta.pop("discarded", None)
        fig.regen_meta = meta
        flag_modified(fig, "regen_meta")
        session.commit()
        return {
            "ok": True,
            "figure_id": str(figure_id),
            "engine": "vector",
            "subject": diagram.get("subject") or "",
            "description": diagram.get("description") or "",
        }


def _embed_table_graphics(
    source_image_bytes: bytes,
    svg: str,
    graphics: list[dict[str, Any]],
    *,
    redraw: bool = True,
) -> tuple[str, int]:
    """Fill each ``{{GRAPHIC_N}}`` placeholder in ``svg`` with an inline base64
    data URI.

    For every reported graphic region we crop it from the source table image
    (normalized bbox → pixels), optionally redraw the crop via the image model
    (``redraw=True``; on failure we keep the faithful crop), and substitute it for
    its ``{{GRAPHIC_<id>}}`` token. Any placeholder we cannot fill is replaced with
    a 1×1 transparent pixel so a leftover token never breaks rasterization.

    Returns ``(svg, n_embedded)``.
    """
    import base64 as _b64
    import io as _io
    import re as _re

    from PIL import Image

    from app.services.figures import regenerator as fig_regen

    def _data_uri(png_bytes: bytes) -> str:
        return "data:image/png;base64," + _b64.b64encode(png_bytes).decode("ascii")

    n_embedded = 0
    try:
        src = Image.open(_io.BytesIO(source_image_bytes)).convert("RGB")
    except Exception as e:
        logger.warning("table embed: cannot open source image: %s", e)
        src = None

    if src is not None:
        W, H = src.size
        for g in graphics:
            if not isinstance(g, dict):
                continue
            gid = g.get("id")
            bbox = g.get("bbox") or {}
            try:
                bx0 = float(bbox.get("x0", 0))
                by0 = float(bbox.get("y0", 0))
                bx1 = float(bbox.get("x1", 0))
                by1 = float(bbox.get("y1", 0))
            except (TypeError, ValueError):
                continue
            # The model may report the bbox as fractions (0–1) or as source-image
            # pixels (it tends to mirror the SOURCE FIGURE SIZE hint). Detect and
            # normalize to pixels either way.
            if max(bx0, by0, bx1, by1) <= 1.5:
                bx0, by0, bx1, by1 = bx0 * W, by0 * H, bx1 * W, by1 * H
            px0, px1 = sorted((bx0, bx1))
            py0, py1 = sorted((by0, by1))
            px0 = max(0, min(W, round(px0)))
            px1 = max(0, min(W, round(px1)))
            py0 = max(0, min(H, round(py0)))
            py1 = max(0, min(H, round(py1)))
            if px1 - px0 < 4 or py1 - py0 < 4:
                continue
            buf = _io.BytesIO()
            src.crop((px0, py0, px1, py1)).save(buf, "PNG")
            crop_png = buf.getvalue()
            graphic_png = crop_png
            if redraw:
                try:
                    graphic_png = fig_regen.regenerate(
                        crop_png,
                        style="enhanced",
                        figure_meta={"context": "embedded table graphic"},
                        mime_type="image/png",
                    )
                except Exception as e:
                    logger.warning(
                        "table embed: redraw failed for graphic %s — keeping "
                        "original crop (%s: %s)",
                        gid, type(e).__name__, e,
                    )
                    graphic_png = crop_png
            # Normalize to real PNG bytes — Gemini's image-out returns JPEG, and a
            # JPEG body under a data:image/png URI fails to decode in resvg (the
            # graphic silently renders blank). Re-encoding guarantees the mime
            # matches the bytes.
            try:
                _norm = _io.BytesIO()
                Image.open(_io.BytesIO(graphic_png)).convert("RGB").save(_norm, "PNG")
                graphic_png = _norm.getvalue()
            except Exception as e:
                logger.warning("table embed: graphic %s re-encode failed: %s", gid, e)
            token = "{{GRAPHIC_%s}}" % gid
            if token in svg:
                svg = svg.replace(token, _data_uri(graphic_png))
                n_embedded += 1

    # Sweep any unfilled placeholders → 1×1 transparent pixel (never leave a token).
    tbuf = _io.BytesIO()
    Image.new("RGBA", (1, 1), (0, 0, 0, 0)).save(tbuf, "PNG")
    svg = _re.sub(r"\{\{GRAPHIC_[^}]*\}\}", _data_uri(tbuf.getvalue()), svg)
    return svg, n_embedded


def regenerate_table_figure(
    figure_id: UUID,
    custom_instructions: str | None = None,
    *,
    redraw_graphics: bool = True,
) -> dict[str, Any]:
    """Regenerate a composite TABLE figure (text + embedded graphics) as a crisp
    vector SVG: verbatim text + grid rebuilt by the model, each embedded graphic
    cropped from the source, AI-redrawn, and embedded back into its cell. The
    rasterized PNG is stored as the figure's APPROVED regen variant.

    On a fallback verdict or a render failure the ORIGINAL figure is kept and an
    ``_error`` is returned — the caller must NOT re-route a table to the image
    model (that reintroduces the garbled-text problem). Pure data tables (no
    embedded graphic) just get the crisp grid.
    """
    from datetime import datetime, timezone

    from sqlalchemy.orm.attributes import flag_modified

    from app.models.figure import Figure
    from app.services.svg_raster import rasterize_svg_to_png

    with SyncSession() as session:
        fig = session.get(Figure, figure_id)
        if fig is None:
            return {"_error": "not_found"}
        if not fig.image_bytes:
            return {"_error": "no_image"}
        image_bytes_list = [(fig.image_bytes, fig.mime_type or "image/png")]
        caption = (getattr(fig, "caption", None) or "").strip()
        context = _theory_context_for_figure(session, fig)

        diagram = _generate_diagram_blocking(
            original_image_bytes_list=image_bytes_list,
            regenerated_question_text=context,
            regenerated_solution_text=caption,
            custom_instructions=custom_instructions,
            previous_diagram=None,
            mode="table",
            system_prompt_name="figures/table_structuring_svg",
        )
        if not diagram:
            return {"_error": "generation_failed"}
        if diagram.get("fallback_to_original") or not diagram.get("svg_preview"):
            return {"_error": "fallback", "description": diagram.get("description") or ""}

        # Crop + redraw + embed the graphics; rasterize the SUBSTITUTED svg. We
        # keep the placeholder svg (small) in regen_meta — never the base64-laden
        # final svg, which would bloat the JSON and duplicate regen_image_bytes.
        final_svg, n_embedded = _embed_table_graphics(
            fig.image_bytes,
            diagram["svg_preview"],
            diagram.get("graphics") or [],
            redraw=redraw_graphics,
        )
        png = rasterize_svg_to_png(final_svg)
        if not png:
            return {"_error": "rasterize_failed"}

        fig.regen_image_bytes = png
        fig.approved_at = datetime.now(timezone.utc)
        meta = dict(fig.regen_meta) if isinstance(fig.regen_meta, dict) else {}
        meta["diagram"] = diagram  # placeholder svg + graphics bboxes (small)
        meta["source"] = "table_structuring"
        meta["engine"] = "table_embed"
        meta["graphics_embedded"] = n_embedded
        meta.pop("discarded", None)
        fig.regen_meta = meta
        flag_modified(fig, "regen_meta")
        session.commit()
        return {
            "ok": True,
            "figure_id": str(figure_id),
            "engine": "table_embed",
            "graphics_embedded": n_embedded,
            "subject": diagram.get("subject") or "",
            "description": diagram.get("description") or "",
        }


def compute_vector_png(
    session: Session,
    fig: Any,
    custom_instructions: str | None = None,
) -> tuple[bytes | None, dict[str, Any] | None]:
    """Compute a vector (LaTeX/SVG) regen PNG for ``fig`` WITHOUT persisting.

    Read-only against ``session``. Returns ``(png_bytes, diagram_meta)``;
    ``png_bytes`` is None on fallback/failure (caller keeps original or routes
    to the image model). Used by the batch worker so persistence stays in the
    batch's own session/bookkeeping.
    """
    from app.services.svg_raster import rasterize_svg_to_png

    if not getattr(fig, "image_bytes", None):
        return None, None
    image_bytes_list = [(fig.image_bytes, fig.mime_type or "image/png")]
    context = _theory_context_for_figure(session, fig)
    caption = (getattr(fig, "caption", None) or "").strip()
    diagram = _generate_diagram_blocking(
        original_image_bytes_list=image_bytes_list,
        regenerated_question_text=context,
        regenerated_solution_text=caption,
        custom_instructions=custom_instructions,
        mode="theory",
    )
    if not diagram:
        return None, None
    _validate_diagram_renderable(diagram)
    if diagram.get("fallback_to_original") or not diagram.get("svg_preview"):
        return None, diagram
    png = rasterize_svg_to_png(diagram["svg_preview"])
    return (png or None), diagram


def compute_table_png(
    session: Session,
    fig: Any,
    custom_instructions: str | None = None,
    *,
    redraw_graphics: bool = True,
) -> tuple[bytes | None, dict[str, Any] | None, int]:
    """Compute a composite-table regen PNG for ``fig`` WITHOUT persisting.

    Read-only against ``session`` (the graphic redraw makes its own Gemini
    calls). Returns ``(png_bytes, diagram_meta, n_embedded)``; ``png_bytes`` is
    None on fallback/failure. The caller MUST keep the original on None — a table
    must never be routed to the whole-image model.
    """
    from app.services.svg_raster import rasterize_svg_to_png

    if not getattr(fig, "image_bytes", None):
        return None, None, 0
    image_bytes_list = [(fig.image_bytes, fig.mime_type or "image/png")]
    caption = (getattr(fig, "caption", None) or "").strip()
    context = _theory_context_for_figure(session, fig)
    diagram = _generate_diagram_blocking(
        original_image_bytes_list=image_bytes_list,
        regenerated_question_text=context,
        regenerated_solution_text=caption,
        custom_instructions=custom_instructions,
        mode="table",
        system_prompt_name="figures/table_structuring_svg",
    )
    if not diagram:
        return None, None, 0
    if diagram.get("fallback_to_original") or not diagram.get("svg_preview"):
        return None, diagram, 0
    final_svg, n_embedded = _embed_table_graphics(
        fig.image_bytes,
        diagram["svg_preview"],
        diagram.get("graphics") or [],
        redraw=redraw_graphics,
    )
    png = rasterize_svg_to_png(final_svg)
    return (png or None), diagram, n_embedded


def _persist_regen_items(
    session: Session,
    *,
    regen: QuestionRegeneration,
    source: Question,
    items: list[dict[str, Any]],
) -> int:
    """Persist regenerated Question rows. Returns the count inserted.

    ARCHITECTURE RULE (mirrors Q4):
      section_ref / section_title / bank_id / book_id are ALL stamped from
      the worker's known state (source row + regen row). NEVER from the
      Gemini item dict. Item-level fields (question, answer, etc.) are
      model-supplied. The section anchor is owned by the worker.

    PHASE 4 — multimodal regen:
      Each regen question INHERITS figure_references from the source
      (same images attached at same offsets). When item carries
      ``image_needs_regen=true``, the verdict + reason is also stored
      under qc_local["image_regen"] so the frontend can surface a
      "⚠ Figure needs regen" hint on that variant.
    """
    # Variants no longer inherit figure_references from the source — the
    # Step-2 LaTeX/SVG diagram is the only image a variant shows. So we
    # don't pre-load source_refs anymore.

    from app.services.question_latex_normalizer import normalize_question_latex
    from app.workers.questions_v3 import _finalize_solution_flag

    inserted = 0
    for it in items:
        text = (it.get("question") or "").strip()
        if not text:
            continue
        answer = (it.get("answer") or "").strip() or None
        # Structure-mirroring (prompt Rule 8) — see comments above
        solution = (it.get("solution") or "").strip() or None
        solution_text = solution or answer
        # Q5: regenerated text is fresh Gemini output — normalize LaTeX/
        # chemistry so the variant renders the same as extracted questions.
        # Without this, regenerated questions show raw $...$ / Unicode.
        text, _ = normalize_question_latex(text)
        if solution_text:
            solution_text, _ = normalize_question_latex(solution_text)
        # SOLUTION is the deliberate exception to source-mirroring: the
        # prompt ALWAYS generates a full worked solution for regenerated
        # questions (Category A), even when the source had no printed
        # solution (e.g. exercise questions). So we keep whatever solution
        # the model produced and do NOT gate on source.has_solution.
        # Q1 invariant: solution_text + has_solution finalized in lockstep.
        solution_text, has_solution = _finalize_solution_flag(solution_text)
        model_says_options = bool(it.get("options"))
        has_options = model_says_options and bool(source.has_options)
        q_type = (it.get("question_type") or source.question_type or "").strip() or None
        kind = _map_question_type_to_kind(q_type)

        # Phase 4 — capture multimodal verdict in qc_local
        qc_local: dict[str, Any] = {
            "pass": True, "score": 1.0, "failures": [],
        }
        if it.get("image_needs_regen") is True:
            qc_local["image_regen"] = {
                "needed": True,
                "reason": (it.get("image_regen_reason") or "").strip(),
            }

        # Step 2 — chained LaTeX/SVG diagram regen (attached upstream in the
        # async worker by _attach_diagrams_to_items). Stored verbatim inside
        # qc_local["regenerated_diagram"] so no DB migration is needed. When
        # the model could synthesize a vector equivalent (not a fallback), we
        # also surface the image_regen hint so the UI flags that the figure
        # was reconstructed to match the new values.
        regen_diagram = it.get("regenerated_diagram")
        if isinstance(regen_diagram, dict):
            qc_local["regenerated_diagram"] = regen_diagram
            qc_local["image_regen"] = {
                "needed": not regen_diagram.get("fallback_to_original", False),
                "reason": regen_diagram.get("description")
                or "Generated LaTeX/SVG vector equivalent",
            }

        row = Question(
            bank_id=regen.bank_id,
            book_id=regen.book_id,
            regen_id=regen.id,
            source_question_id=source.id,
            section_ref=source.section_ref,
            section_uuid=source.section_uuid,
            section_title=source.section_title,
            page_start=source.page_start,
            page_end=source.page_end,
            raw_text=text,
            qc_local=qc_local,
            attempts=1,
            status="passed",
            # Inherit the textbook-original question number from the source.
            # Previously hardcoded None — that's what broke ordering in
            # Preview/Composer/DOCX: when a book has regen variants the merge
            # prefers variants (prefer_regen=true), so variants drove the
            # items list with no number → couldn't sort → jumbled UI.
            # Variants of Q5 still cluster + share label "Question 5".
            question_number=source.question_number,
            exercise_ref=source.exercise_ref,
            chapter_ref=source.chapter_ref,
            kind=kind,
            question_type=q_type,
            has_options=has_options,
            solution_text=solution_text,
            has_solution=has_solution,
            identified_total=None,
            qc_status="pending",
        )
        session.add(row)
        # Regen variants do NOT inherit figure_references from the source.
        # A regenerated question has new values, so the source's original
        # figure would be misleading. The Step-2 diagram step (see
        # _attach_diagrams_to_items) produces a fresh LaTeX/SVG diagram per
        # image-bearing variant, stored in qc_local.regenerated_diagram —
        # that is the only image a variant shows; if it's absent/failed,
        # the variant shows no figure (never the stale original).

        inserted += 1
    session.commit()
    return inserted


def _persist_regen_fallback(
    session: Session,
    *,
    regen: QuestionRegeneration,
    source: Question,
    reason: str,
) -> int:
    """No-skip guarantee — retain the original, flagged.

    When a source question yields 0 usable variants after all retries, the
    old behaviour dropped it: the source vanished from the regen output and
    was only counted as ``failed`` in stats. That silently lost ~5% of
    questions on some books.

    Instead, persist ONE row carrying the ORIGINAL question text + solution,
    flagged ``qc_local.regen_failed`` so the frontend can badge it
    ("⚠ couldn't regenerate — original retained") and the user can retry it
    via the existing section-level retry. This guarantees every source
    question is present in the regen output (count parity) — completing the
    "retry empties, never silently drop" philosophy already stated at the
    top of this module.

    Section anchoring + figure inheritance mirror ``_persist_regen_items``.
    Returns 1 (the retained row).
    """
    qc_local: dict[str, Any] = {
        "pass": False,
        "score": 0.0,
        "failures": ["regen_failed"],
        # Frontend hint: this row is the source verbatim, not a regenerated
        # variant. Surfaces a badge + retry affordance.
        "regen_failed": {
            "retained_original": True,
            "reason": (reason or "no variants produced")[:300],
        },
    }

    row = Question(
        bank_id=regen.bank_id,
        book_id=regen.book_id,
        regen_id=regen.id,
        source_question_id=source.id,
        section_ref=source.section_ref,
        section_uuid=source.section_uuid,
        section_title=source.section_title,
        page_start=source.page_start,
        page_end=source.page_end,
        # Carry the ORIGINAL text + solution verbatim (already normalized at
        # extraction time, so no re-normalization needed).
        raw_text=source.raw_text,
        qc_local=qc_local,
        attempts=_REGEN_SOURCE_MAX_ATTEMPTS,
        # 'passed' so it renders alongside generated variants in the regen
        # output (the failure is signalled via qc_local, not by hiding it).
        status="passed",
        # Inherit textbook-original question number from source — see the
        # other regen-variant creation site above for the full rationale.
        question_number=source.question_number,
        exercise_ref=source.exercise_ref,
        chapter_ref=source.chapter_ref,
        kind=source.kind,
        question_type=source.question_type,
        has_options=source.has_options,
        solution_text=source.solution_text,
        has_solution=source.has_solution,
        identified_total=None,
        qc_status="pending",
    )
    session.add(row)
    # Fallback (retained-original) variants also do NOT inherit
    # figure_references. The fallback is a flagged copy of the source's
    # text for review only; the source itself still carries the figure
    # on its own question_id, so nothing visual is lost.

    session.commit()
    return 1


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------
async def _run_regen_v3(regen_id: UUID, job_id: UUID) -> dict[str, Any]:
    with SyncSession() as session:
        regen = session.get(QuestionRegeneration, regen_id)
        if regen is None:
            raise ValueError(f"Regen {regen_id} not found")
        bank = session.get(QuestionBank, regen.bank_id)
        book = session.get(Book, regen.book_id)
        if bank is None or book is None:
            raise ValueError("Bank or Book missing for regen")

        # R4 will read these from regen.* once the columns land in R5.
        # Forward-compat: getattr falls back to defaults if column not yet
        # present on the model.
        similarity_level = (
            (getattr(regen, "similarity_level", None) or "").strip()
            or DEFAULT_SIMILARITY
        )
        if similarity_level not in _VALID_SIMILARITY_LEVELS:
            similarity_level = DEFAULT_SIMILARITY
        count = DEFAULT_COUNT  # always 1, locked
        question_type = (
            (getattr(regen, "question_type", None) or "").strip()
            or DEFAULT_QUESTION_TYPE
        )
        priority_mode = DEFAULT_PRIORITY_MODE  # always override
        custom_instructions = (regen.custom_instructions or "").strip() or None

        # Snapshot for downstream use.
        subject = bank.subject or None
        grade = getattr(book, "grade_level", None) or None
        board = getattr(book, "board", None) or None
        bank_id = bank.id
        book_id_snapshot = regen.book_id
        regen.status = "extracting"
        regen.last_error = None
        session.commit()

        # Load source questions for the requested sections.
        section_refs = _flatten_source_section_refs(regen, bank_id, session)
        if not section_refs:
            _update_regen(
                session, regen.id,
                status="ready",
                finished_at=datetime.utcnow(),
                extraction_stats={
                    "sections": [], "totals": {
                        "expected_total": 0, "extracted_total": 0,
                        "complete": 0, "partial": 0, "empty": 0, "failed": 0,
                    },
                },
            )
            _update_job(session, job_id,
                        status="succeeded", progress=100,
                        message="No sections in regen scope",
                        finished_at=datetime.utcnow())
            return {"ok": True, "regen_id": str(regen.id), "total": 0}

        source_qs = session.execute(
            select(Question).where(
                Question.bank_id == bank_id,
                Question.regen_id.is_(None),
                Question.section_ref.in_(section_refs),
            )
        ).scalars().all()

        if not source_qs:
            _update_regen(
                session, regen.id,
                status="ready",
                finished_at=datetime.utcnow(),
                extraction_stats={
                    "sections": [], "totals": {
                        "expected_total": 0, "extracted_total": 0,
                        "complete": 0, "partial": 0, "empty": 0, "failed": 0,
                    },
                },
            )
            _update_job(session, job_id,
                        status="succeeded", progress=100,
                        message="No source questions to regenerate",
                        finished_at=datetime.utcnow())
            return {"ok": True, "regen_id": str(regen.id), "total": 0}

        # Capture source IDs BEFORE the wipe/commit. After commit() the
        # source_qs objects are expired and accessing their attributes
        # outside the session would trigger a refresh — fails because the
        # session is closed.
        source_ids = [q.id for q in source_qs]
        total_sources = len(source_ids)

        # Wipe any previous regen rows for THIS regen so a retry is clean.
        # (R6 offers section-level retry that wipes only one section.)
        session.execute(
            delete(Question).where(Question.regen_id == regen.id)
        )
        session.commit()

        system_prompt = load_raw("question_regenerator_v3")
        # Phase 4 — optional multimodal addendum prompt. Loaded once here
        # and conditionally appended per source by _regen_one_source when
        # the source has attached images AND MULTIMODAL_REGEN_ENABLED.
        try:
            image_addendum_prompt: str | None = load_raw(
                "question_regenerator_v3_image_addendum"
            )
        except Exception:
            image_addendum_prompt = None

        _update_job(
            session, job_id,
            status="running", progress=5,
            message=f"v3 regen — {total_sources} source question(s) × {count} variants",
        )

    # Stats accumulators.
    section_counts: dict[str, dict[str, int]] = {}
    total_generated = 0
    total_failed = 0

    async def _process_one(qid: UUID) -> tuple[UUID, dict[str, Any]]:
        # Re-fetch source row in its own session for thread-safe SQLite use.
        with SyncSession() as own:
            source = own.get(Question, qid)
            if source is None:
                return qid, {"ok": False, "items": [], "notes": "",
                             "error": "source vanished"}
            # Detach values we need so the session can close.
            cached = {
                "section_ref": source.section_ref,
                "raw_text": source.raw_text,
                "solution_text": source.solution_text,
                "question_type": source.question_type,
                "section_title": source.section_title,
                "page_start": source.page_start,
                "page_end": source.page_end,
                "exercise_ref": source.exercise_ref,
                "chapter_ref": source.chapter_ref,
            }
            src_book_id = source.book_id
            # Phase 4 — load image bytes if multimodal feature is on. Cheap
            # if the question has no attached figures (empty list).
            if settings.MULTIMODAL_REGEN_ENABLED:
                image_bytes_list = _load_source_image_bytes(
                    own, src_book_id, qid,
                )
            else:
                image_bytes_list = []
        # Build a transient Question-like object for prompt building. Easiest:
        # use a tiny attribute holder rather than instantiating ORM detached.
        class _SrcView:
            pass
        sv = _SrcView()
        for k, v in cached.items():
            setattr(sv, k, v)
        sv.id = qid

        result = await _regen_one_source(
            sv,  # type: ignore[arg-type]
            system_prompt=system_prompt,
            similarity_level=similarity_level,
            count=count,
            question_type=question_type,
            custom_instructions=custom_instructions,
            priority_mode=priority_mode,
            subject=subject,
            chapter=cached.get("section_title") or None,
            grade=grade,
            board=board,
            image_bytes_list=image_bytes_list or None,
            image_addendum_prompt=image_addendum_prompt,
        )
        # Step 2 — chained diagram regen for image-bearing questions. Mutates
        # each item with a `regenerated_diagram` payload before persistence.
        if result.get("ok") and result.get("items"):
            await _attach_diagrams_to_items(result["items"], image_bytes_list or None)
        # Persist within a fresh session.
        if result.get("ok") and result.get("items"):
            with SyncSession() as own:
                src = own.get(Question, qid)
                regen_obj = own.get(QuestionRegeneration, regen_id)
                if src is not None and regen_obj is not None:
                    inserted = _persist_regen_items(
                        own, regen=regen_obj, source=src,
                        items=result["items"],
                    )
                    result["persisted"] = inserted
        else:
            # No-skip guarantee: regen produced 0 usable variants after all
            # retries → retain the ORIGINAL question (flagged) so the source
            # is never silently dropped from the output. persisted stays 0
            # (no GENERATED variant) but the row exists, flagged for retry.
            with SyncSession() as own:
                src = own.get(Question, qid)
                regen_obj = own.get(QuestionRegeneration, regen_id)
                if src is not None and regen_obj is not None:
                    _persist_regen_fallback(
                        own, regen=regen_obj, source=src,
                        reason=str(result.get("error") or "no variants"),
                    )
                    result["fallback"] = True
        return qid, result

    # Heartbeat-wrap the parallel run.
    with Heartbeat(
        job_id,
        base_msg=f"Regen ({total_sources} source questions)",
        progress=5,
    ):
        tasks = [asyncio.create_task(_process_one(qid)) for qid in source_ids]
        done = 0
        for coro in asyncio.as_completed(tasks):
            qid, result = await coro
            done += 1
            progress = 5 + int(90 * done / max(total_sources, 1))

            # Accumulate per-section stats. We need section_ref for the qid —
            # cheapest: read it once before the task starts. We could cache
            # in source_qs, but doing a tiny lookup here keeps the loop simple.
            with SyncSession() as own:
                src = own.get(Question, qid)
                section_ref = src.section_ref if src else "?"
            persisted = int(result.get("persisted") or 0)
            ok = bool(result.get("ok"))
            fallback = bool(result.get("fallback"))
            bucket = section_counts.setdefault(section_ref, {
                "source_count": 0, "generated": 0, "failed": 0, "retained": 0,
            })
            bucket["source_count"] += 1
            bucket["generated"] += persisted
            if ok and persisted > 0:
                total_generated += persisted
            else:
                # Regen failed for this source. The question is NOT dropped —
                # the original was retained (fallback row). Count it as failed
                # (regen didn't produce a variant) AND as retained (present in
                # output) so stats are transparent: failed == regen misses,
                # retained == originals kept so nothing is skipped.
                bucket["failed"] += 1
                total_failed += 1
                if fallback:
                    bucket["retained"] += 1

            with SyncSession() as own:
                _update_job(own, job_id, progress=progress,
                            message=f"Regen {done}/{total_sources} sources — "
                                    f"{total_generated} generated, "
                                    f"{total_failed} failed")

    # Post-regen embedder pass: regenerated variants get fresh
    # FigureReference rows materialized against their own raw_text +
    # solution_text. Without this, variants that reference a figure only
    # in their solution would never have it attached, so the
    # image_regen_hint badge wouldn't surface on the Final/Preview cards.
    try:
        from app.services.figure_embedder import embed_figures_for_book_sync
        with SyncSession() as own:
            embed_counters = embed_figures_for_book_sync(own, book_id_snapshot)
            logger.info("post-regen embed: %s", embed_counters)
    except Exception as e:
        logger.warning("post-regen embed failed: %s", e)

    # Final stats + status.
    sections_report: list[dict[str, Any]] = []
    complete = partial = empty = failed = 0
    for ref, c in section_counts.items():
        if c["failed"] == c["source_count"] and c["generated"] == 0:
            sec_status = "failed"
            failed += 1
        elif c["failed"] > 0:
            sec_status = "partial"
            partial += 1
        elif c["generated"] == 0:
            sec_status = "empty"
            empty += 1
        else:
            sec_status = "complete"
            complete += 1
        sections_report.append({
            "section_ref": ref,
            "source_count": c["source_count"],
            "generated": c["generated"],
            "failed": c["failed"],
            # How many of the failures had their ORIGINAL retained (no-skip
            # fallback). retained == failed means nothing was dropped.
            "retained": c.get("retained", 0),
            "status": sec_status,
        })

    total_retained = sum(c.get("retained", 0) for c in section_counts.values())
    stats = {
        "sections": sections_report,
        "totals": {
            "expected_total": total_sources * count,
            "extracted_total": total_generated,
            "complete": complete,
            "partial": partial,
            "empty": empty,
            "failed": failed,
            # Originals retained via the no-skip fallback. Invariant:
            # every source appears in output → generated + retained covers
            # all source questions (no silent drops).
            "retained_originals": total_retained,
        },
    }

    bank_status = "ready" if total_failed == 0 else "partial"
    with SyncSession() as session:
        _update_regen(session, regen_id,
                      status=bank_status,
                      finished_at=datetime.utcnow(),
                      extraction_stats=stats)
        _update_job(session, job_id,
                    status="succeeded", progress=100,
                    message=f"Regen complete — generated {total_generated} "
                            f"across {len(section_counts)} section(s)",
                    finished_at=datetime.utcnow())

    return {
        "ok": True,
        "regen_id": str(regen_id),
        "total_sources": total_sources,
        "total_generated": total_generated,
        "total_failed": total_failed,
    }


# ---------------------------------------------------------------------------
# R6 — Section-level retry
# Run regen for a SINGLE section_ref within an existing regen. Wipes only
# the rows for that (regen_id, section_ref) — other sections stay intact.
# Per-section status is recomputed and merged into regen.extraction_stats
# without touching other sections' reports.
# ---------------------------------------------------------------------------
async def _run_regen_one_section_v3(
    regen_id: UUID,
    section_ref: str,
    job_id: UUID,
    section_custom_instructions: str | None = None,
) -> dict[str, Any]:
    """Re-run regeneration for ONE section.

    If `section_custom_instructions` is provided, it OVERRIDES the regen's
    persisted custom_instructions for this single retry — the regen record
    itself is NOT mutated. This is how the UI's "Reseed this section" dialog
    layers per-section instructions on top of the broader regen params.
    """
    with SyncSession() as session:
        regen = session.get(QuestionRegeneration, regen_id)
        if regen is None:
            raise ValueError(f"Regen {regen_id} not found")
        bank = session.get(QuestionBank, regen.bank_id)
        book = session.get(Book, regen.book_id)
        if bank is None or book is None:
            raise ValueError("Bank or Book missing for regen")

        # Same param resolution as the main worker.
        similarity_level = (
            (getattr(regen, "similarity_level", None) or "").strip()
            or DEFAULT_SIMILARITY
        )
        if similarity_level not in _VALID_SIMILARITY_LEVELS:
            similarity_level = DEFAULT_SIMILARITY
        count = DEFAULT_COUNT  # always 1, locked
        question_type = (
            (getattr(regen, "question_type", None) or "").strip()
            or DEFAULT_QUESTION_TYPE
        )
        priority_mode = DEFAULT_PRIORITY_MODE  # always override
        # Section-level instructions OVERRIDE the regen's persisted custom
        # instructions for this single retry. The regen record stays clean.
        if section_custom_instructions and section_custom_instructions.strip():
            custom_instructions = section_custom_instructions.strip()
        else:
            custom_instructions = (regen.custom_instructions or "").strip() or None
        subject = bank.subject or None
        grade = getattr(book, "grade_level", None) or None
        board = getattr(book, "board", None) or None
        bank_id = bank.id
        book_id_snapshot = regen.book_id

        # Wipe ONLY this section's regen rows. Other sections preserved.
        session.execute(
            delete(Question).where(
                Question.regen_id == regen.id,
                Question.section_ref == section_ref,
            )
        )
        session.commit()

        # Load source Questions for this section.
        source_qs = session.execute(
            select(Question).where(
                Question.bank_id == bank_id,
                Question.regen_id.is_(None),
                Question.section_ref == section_ref,
            )
        ).scalars().all()

        if not source_qs:
            _update_job(session, job_id,
                        status="succeeded", progress=100,
                        message=f"No source questions in section {section_ref}",
                        finished_at=datetime.utcnow())
            return {"ok": True, "regen_id": str(regen.id),
                    "section_ref": section_ref, "total": 0}

        total_sources = len(source_qs)
        system_prompt = load_raw("question_regenerator_v3")
        try:
            image_addendum_prompt: str | None = load_raw(
                "question_regenerator_v3_image_addendum"
            )
        except Exception:
            image_addendum_prompt = None
        source_ids = [q.id for q in source_qs]

        _update_job(
            session, job_id,
            status="running", progress=5,
            message=f"Section retry — {section_ref} ({total_sources} sources × {count})",
        )

    # Per-source processing (same pattern as main worker).
    total_generated = 0
    total_failed = 0
    total_retained = 0

    async def _process_one(qid: UUID) -> tuple[UUID, dict[str, Any]]:
        with SyncSession() as own:
            source = own.get(Question, qid)
            if source is None:
                return qid, {"ok": False, "items": [],
                             "error": "source vanished"}
            cached = {
                "section_ref": source.section_ref,
                "raw_text": source.raw_text,
                "solution_text": source.solution_text,
                "question_type": source.question_type,
                "section_title": source.section_title,
                "page_start": source.page_start,
                "page_end": source.page_end,
                "exercise_ref": source.exercise_ref,
                "chapter_ref": source.chapter_ref,
            }
            src_book_id = source.book_id
            if settings.MULTIMODAL_REGEN_ENABLED:
                image_bytes_list = _load_source_image_bytes(
                    own, src_book_id, qid,
                )
            else:
                image_bytes_list = []

        class _SrcView:
            pass
        sv = _SrcView()
        for k, v in cached.items():
            setattr(sv, k, v)
        sv.id = qid

        result = await _regen_one_source(
            sv,  # type: ignore[arg-type]
            system_prompt=system_prompt,
            similarity_level=similarity_level,
            count=count,
            question_type=question_type,
            custom_instructions=custom_instructions,
            priority_mode=priority_mode,
            subject=subject,
            chapter=cached.get("section_title") or None,
            grade=grade,
            board=board,
            image_bytes_list=image_bytes_list or None,
            image_addendum_prompt=image_addendum_prompt,
        )
        # Step 2 — chained diagram regen (same as the full-regen path).
        if result.get("ok") and result.get("items"):
            await _attach_diagrams_to_items(result["items"], image_bytes_list or None)
        if result.get("ok") and result.get("items"):
            with SyncSession() as own:
                src = own.get(Question, qid)
                regen_obj = own.get(QuestionRegeneration, regen_id)
                if src is not None and regen_obj is not None:
                    inserted = _persist_regen_items(
                        own, regen=regen_obj, source=src,
                        items=result["items"],
                    )
                    result["persisted"] = inserted
        else:
            # No-skip guarantee (mirrors the full-regen path): retain the
            # original question, flagged, so a section retry never drops a
            # source that failed to regenerate.
            with SyncSession() as own:
                src = own.get(Question, qid)
                regen_obj = own.get(QuestionRegeneration, regen_id)
                if src is not None and regen_obj is not None:
                    _persist_regen_fallback(
                        own, regen=regen_obj, source=src,
                        reason=str(result.get("error") or "no variants"),
                    )
                    result["fallback"] = True
        return qid, result

    with Heartbeat(
        job_id,
        base_msg=f"Section retry — {section_ref}",
        progress=5,
    ):
        tasks = [asyncio.create_task(_process_one(qid)) for qid in source_ids]
        done = 0
        for coro in asyncio.as_completed(tasks):
            qid, result = await coro
            done += 1
            progress = 5 + int(90 * done / max(total_sources, 1))
            persisted = int(result.get("persisted") or 0)
            ok = bool(result.get("ok"))
            if ok and persisted > 0:
                total_generated += persisted
            else:
                total_failed += 1
                if result.get("fallback"):
                    total_retained += 1
            with SyncSession() as own:
                _update_job(own, job_id, progress=progress,
                            message=f"Section retry {done}/{total_sources} — "
                                    f"{total_generated} generated")

    # Post-regen embedder pass — mirror the full-regen path so section
    # retries also get figures attached to newly created variants.
    try:
        from app.services.figure_embedder import embed_figures_for_book_sync
        with SyncSession() as own:
            embed_counters = embed_figures_for_book_sync(own, book_id_snapshot)
            logger.info("post-section-regen embed: %s", embed_counters)
    except Exception as e:
        logger.warning("post-section-regen embed failed: %s", e)

    # Merge this section's report into the regen's extraction_stats without
    # touching other sections. If extraction_stats is missing, build minimal.
    if total_failed == total_sources and total_generated == 0:
        sec_status = "failed"
    elif total_failed > 0:
        sec_status = "partial"
    elif total_generated == 0:
        sec_status = "empty"
    else:
        sec_status = "complete"

    this_section_report = {
        "section_ref": section_ref,
        "source_count": total_sources,
        "generated": total_generated,
        "failed": total_failed,
        "retained": total_retained,
        "status": sec_status,
    }

    with SyncSession() as session:
        regen_obj = session.get(QuestionRegeneration, regen_id)
        stats = dict(regen_obj.extraction_stats or {}) if regen_obj else {}
        sections = list(stats.get("sections") or [])
        # Replace existing report for this section_ref or append.
        sections = [s for s in sections if s.get("section_ref") != section_ref]
        sections.append(this_section_report)

        # Recompute totals.
        total_expected = sum(int(s.get("source_count", 0)) for s in sections)
        total_generated_all = sum(int(s.get("generated", 0)) for s in sections)
        complete = sum(1 for s in sections if s.get("status") == "complete")
        partial = sum(1 for s in sections if s.get("status") == "partial")
        empty = sum(1 for s in sections if s.get("status") == "empty")
        failed_count = sum(1 for s in sections if s.get("status") == "failed")

        stats["sections"] = sections
        stats["totals"] = {
            "expected_total": total_expected * count,
            "extracted_total": total_generated_all,
            "complete": complete,
            "partial": partial,
            "empty": empty,
            "failed": failed_count,
        }

        new_regen_status = "ready" if failed_count == 0 else "partial"
        _update_regen(session, regen_id,
                      status=new_regen_status,
                      extraction_stats=stats)
        _update_job(session, job_id,
                    status="succeeded", progress=100,
                    message=f"Section retry complete — generated "
                            f"{total_generated} in {section_ref}",
                    finished_at=datetime.utcnow())

    return {
        "ok": True,
        "regen_id": str(regen_id),
        "section_ref": section_ref,
        "total_sources": total_sources,
        "total_generated": total_generated,
        "total_failed": total_failed,
        "section_status": sec_status,
    }


# ---------------------------------------------------------------------------
# Sync entry-points + task registration
# ---------------------------------------------------------------------------
def _extract_questions_regen_v3(regen_id: str, job_id: str) -> dict[str, Any]:
    regen_uuid = UUID(regen_id)
    job_uuid = UUID(job_id)
    try:
        return asyncio.run(_run_regen_v3(regen_uuid, job_uuid))
    except Exception as e:
        logger.exception("extract_questions_regen_v3 failed")
        with SyncSession() as session:
            _update_regen(session, regen_uuid,
                          status="failed",
                          finished_at=datetime.utcnow(),
                          last_error=str(e)[:2000])
            _update_job(session, job_uuid,
                        status="failed",
                        error=str(e)[:2000],
                        finished_at=datetime.utcnow())
        return {"ok": False, "error": str(e)}


def _retry_regen_section_v3(
    regen_id: str,
    section_ref: str,
    job_id: str,
    section_custom_instructions: str | None = None,
) -> dict[str, Any]:
    regen_uuid = UUID(regen_id)
    job_uuid = UUID(job_id)
    try:
        return asyncio.run(
            _run_regen_one_section_v3(
                regen_uuid,
                section_ref,
                job_uuid,
                section_custom_instructions=section_custom_instructions,
            )
        )
    except Exception as e:
        logger.exception("retry_regen_section_v3 failed")
        with SyncSession() as session:
            _update_job(session, job_uuid,
                        status="failed",
                        error=str(e)[:2000],
                        finished_at=datetime.utcnow())
        return {"ok": False, "error": str(e)}


# Celery-mode task wrappers. Inline path uses the underscore functions via
# register_task; Celery path uses these wrappers. Behavior identical.
@celery_app.task(name="extract_questions_regen_v3", bind=True)
def extract_questions_regen_v3_task(self, regen_id: str, job_id: str) -> dict[str, Any]:
    return _extract_questions_regen_v3(regen_id, job_id)


@celery_app.task(name="retry_regen_section_v3", bind=True)
def retry_regen_section_v3_task(
    self,
    regen_id: str,
    section_ref: str,
    job_id: str,
    section_custom_instructions: str | None = None,
) -> dict[str, Any]:
    return _retry_regen_section_v3(
        regen_id, section_ref, job_id, section_custom_instructions
    )


register_task("extract_questions_regen_v3", _extract_questions_regen_v3)
register_task("retry_regen_section_v3", _retry_regen_section_v3)
