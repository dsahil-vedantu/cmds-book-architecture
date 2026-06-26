"""Schema Generator — Gemini 2.5 Pro for native PDF understanding.

Uses the google-genai SDK (NOT the deprecated google-generativeai
package). Routes to prompts/v2/schema_architecture.txt (single-column)
or prompts/v2/schema_architecture_multicolumn.txt (multi-column) based
on the upload-time is_multi_column flag AND auto-detection via
pdf_layout_detector (see Day 12 wiring below).

build_schema() is intentionally SYNCHRONOUS. The Celery worker thread
(extract.py:analyse_book_task) runs without an event loop, so async
calls cause "no current event loop" errors from google-genai's httpx
internals. We ensure a loop exists for the thread, then call Gemini
synchronously.

Pipeline (after SCHEMA Week 1 completion):

    Gemini call (one of 3 attempts, attempt 2+ uses corrective prompt)
        ↓
    assign_uuids_to_schema()  — stable section identity
        ↓
    validate_schema()  — 12 hard rules; failure → corrective retry
        ↓
    BookSchema(**data)  — pydantic construction
        ↓
    verify_schema_against_pdf_text()  — pypdf cross-check (missing labels)
        ↓
    cross_check_section_pages()  — pypdf page verification + auto-correct
        ↓
    return validated schema

NO SILENT FIXES. Every issue is either auto-corrected via Gemini retry
with structured feedback, OR auto-corrected via pypdf ground truth, OR
surfaced as a validation error to the user.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.schemas.analyser import BookSchema, SchemaSection
from app.services.prompt_loader import load_raw
from app.services.schema_postpass import verify_schema_against_pdf_text
from app.utils.json_parse import parse_json

logger = logging.getLogger(__name__)


@dataclass
class SchemaBuildResult:
    """Outcome of build_schema (rebalanced).

    `schema` is always non-None when `status != "failed_preflight"`. On
    accept-with-warnings, `status="needs_review"` and `warnings` carries
    the validator's last-known issues plus any sanitizer fixes worth
    surfacing. `last_failed_attempt` carries the raw dict of the LAST
    failed Gemini attempt for offline diagnosis (None on first-pass success).
    """

    schema: BookSchema | None
    status: str  # "ok" | "needs_review"
    warnings: list[dict[str, Any]] = field(default_factory=list)
    last_failed_attempt: dict | None = None


class _SchemaHeartbeat:
    """Background thread that pumps job.last_heartbeat_at every `interval`
    seconds during a long Gemini schema call. No-op when `job_uuid` is None.

    Independent of `app.core.heartbeat.Heartbeat` (which the outer worker
    already wraps around build_schema). This finer-grained pump exists so
    that even if the outer heartbeat ever gets removed, the Gemini call
    itself can't blow past the watchdog's STALE_AFTER_S threshold of 300s.
    """

    def __init__(self, job_uuid: UUID | None, *, interval: float = 30.0) -> None:
        self._job_uuid = job_uuid
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_SchemaHeartbeat":
        if self._job_uuid is None:
            return self
        # Pump once immediately so the watchdog clock resets at the start.
        self._beat()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="schema-gemini-heartbeat"
        )
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self._beat()

    def _beat(self) -> None:
        try:
            # Import lazy so tests/import-time don't require the DB engine.
            from datetime import datetime, timezone

            from app.core.heartbeat import _HeartbeatSession
            from app.models.job import Job

            with _HeartbeatSession() as s:
                job = s.get(Job, self._job_uuid)
                if job is None:
                    return
                job.last_heartbeat_at = datetime.now(timezone.utc)
                s.commit()
        except Exception:
            logger.exception(
                "schema heartbeat write failed for job %s", self._job_uuid
            )

MAX_ATTEMPTS = 3
# SCHEMA Week 5 — switched from flash to pro after user-verified quality
# improvement on production books. Pro handles dense multi-column and
# math-heavy schemas more accurately. Flash remains in use for question
# OCR + QA verifier where Pro's cost is overkill for transcription work.
GEMINI_MODEL = "gemini-2.5-pro"


def _ensure_event_loop() -> None:
    """Ensure the current thread has an event loop.

    google-genai's httpx internals call asyncio.get_event_loop() internally.
    In non-main threads (like our task-analyse_book daemon thread), no loop
    exists by default — this creates one and sets it as current.
    """
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)


def assign_uuids_to_schema(
    data: dict,
    *,
    existing_uuid_by_key: dict[tuple[str, int | None], str] | None = None,
) -> dict:
    """Assign canonical UUIDs to every section in the parsed schema dict.

    SCHEMA Week 1 Day 2 — adds `uuid` field to each section. The UUID
    becomes the canonical identity used by Section rows downstream,
    replacing the slug-based identity that schema_alignment.py tries
    to repair on re-analyse.

    Parameters
    ----------
    data : dict
        Parsed schema dict (Gemini output after parse_json + sanitize).
        Must have a 'sections' key; mutated in place.
    existing_uuid_by_key : dict, optional
        On re-analyse: map of (title, page_start) → uuid for previously
        extracted sections. Matching schema sections preserve their old
        UUID instead of getting a new one. None on first analyse.

    Returns the same dict (mutated). Sections that already carry a
    'uuid' field are left untouched (idempotent — safe to re-run).

    Pure function — no DB I/O. Caller is responsible for providing
    existing_uuid_by_key from the DB if re-analyse safety matters.
    """
    import uuid as _uuid

    existing_uuid_by_key = existing_uuid_by_key or {}

    def _walk(sections):
        if not isinstance(sections, list):
            return
        for s in sections:
            if not isinstance(s, dict):
                continue
            # Idempotent: skip if already assigned
            if s.get("uuid"):
                _walk(s.get("subsections") or [])
                continue
            # Try to preserve existing UUID via (title, page_start) match
            key = (
                (s.get("title") or "").strip().lower(),
                s.get("page_start"),
            )
            preserved = existing_uuid_by_key.get(key)
            s["uuid"] = preserved or str(_uuid.uuid4())
            _walk(s.get("subsections") or [])

    _walk(data.get("sections") or [])
    return data


# SCHEMA Week 1 Day 14 — _sanitize_schema DELETED.
# Replaced architecturally by:
#   * services/schema_validator.py — 12 hard validation rules
#   * services/schema_correctors.py — corrective prompts that drive
#     Gemini to fix issues on retry
#   * services/schema_postpass.cross_check_section_pages —
#     pypdf-verified page correction
# Total LOC removed: ~140. No silent fixes anywhere; every issue is
# either auto-corrected via retry OR surfaced as a validation error.


def _run_gemini_schema(
    pdf_bytes: bytes,
    schema_prompt: str,
    *,
    timeout_s: int = 300,
    job_uuid: UUID | None = None,
) -> dict:
    """One synchronous Gemini call: upload PDF → generate schema → return dict.

    Real socket timeout via ``HttpOptions`` — see app.core.gemini_runtime.
    Schema generation runs over the full book PDF and is the slowest single
    call in the system. Timeout is per-PDF-type (Day 12 wiring): digital
    PDFs get 180s, scanned PDFs get 600s; default 300s if caller skips it.
    """
    from app.core.gemini_runtime import call_gemini_with_pdf

    # SCHEMA Rebalance Layer 2 — heartbeat thread bumps job.last_heartbeat_at
    # every 30s during the Gemini call so the watchdog (STALE_AFTER_S=300)
    # can never kill an in-flight schema generation. The outer worker's
    # Heartbeat() also pumps every 10s; this is defence-in-depth. No-op
    # when job_uuid is None (standalone callers, tests).
    with _SchemaHeartbeat(job_uuid, interval=30.0):
        raw = call_gemini_with_pdf(
            pdf_bytes=pdf_bytes,
            system_prompt=schema_prompt,
            user_prompt="",
            model=GEMINI_MODEL,
            timeout_s=timeout_s,
            max_output_tokens=32000,
            temperature=0.1,
            display_name="textbook_chapter.pdf",
        )
    return parse_json(raw)


def build_schema(
    pdf_bytes: bytes,
    *,
    is_multi_column: bool = False,
    pdf_title: str | None = None,
    job_uuid: UUID | None = None,
    return_result: bool = False,
) -> BookSchema | SchemaBuildResult:
    """Generate a structural schema from PDF bytes using Gemini 2.5 Pro.

    SYNCHRONOUS — call directly, do NOT wrap in asyncio.run().

    When ``is_multi_column`` is True (user-flagged at upload time for
    MHT-CET / JEE / NEET prep books with dense 2-column layouts), the
    multi-column-aware prompt is loaded. SCHEMA Day 12: if the user did
    NOT flag multi-column but the layout detector confidently says
    otherwise on a digital PDF, we auto-route to the multicolumn prompt.
    User's explicit flag always wins.

    Retry behaviour (SCHEMA Week 1 Day 3):
      Attempt 1: standard prompt
      Attempt 2+: corrective prompt — if attempt N's response failed
                  validation (e.g. NON_INTEGER_PAGE), attempt N+1
                  appends specific fix instructions for those errors
                  via schema_correctors.build_corrective_prompt().

      Each retry is no longer identical to the previous — Gemini
      receives targeted feedback and re-emits with fixes applied.
    """
    _ensure_event_loop()

    # SCHEMA Day 12 — preflight FIRST. Fails fast on encrypted/empty/
    # corrupt PDFs before burning a 3-5 minute Gemini call. Also gives
    # us total_pages (validator bounds rule) and a per-PDF-type Gemini
    # timeout (digital: 180s, scanned: 600s).
    from app.services.pdf_preflight import run_preflight
    preflight = run_preflight(pdf_bytes)
    if not preflight.ok:
        raise ValueError(f"PDF preflight failed: {preflight.error}")
    pdf_total_pages = preflight.total_pages
    gemini_timeout_s = preflight.recommended_timeout_s

    # SCHEMA Day 12 — autodetect column layout. User's explicit
    # upload-time flag ALWAYS wins; we only auto-route when the user
    # did NOT flag multi-column AND the detector is confident on a
    # digital PDF (scanned/image-only PDFs have no reliable text-block
    # geometry, so detection is meaningless there).
    effective_multi_column = is_multi_column
    auto_routed = False
    if not is_multi_column and preflight.pdf_type == "digital":
        from app.services.pdf_layout_detector import detect_layout
        layout = detect_layout(pdf_bytes)
        if layout.layout == "multi" and layout.confidence >= 0.7:
            effective_multi_column = True
            auto_routed = True
            logger.info(
                "Schema layout autodetect: routing to multicolumn prompt "
                "(layout=%s confidence=%.2f pages_sampled=%d) — user did "
                "not flag is_multi_column at upload",
                layout.layout, layout.confidence, layout.pages_sampled,
            )

    logger.info(
        "Schema preflight: pdf_type=%s pages=%d timeout=%ds "
        "multi_column=%s%s rotated_pages=%d",
        preflight.pdf_type, preflight.total_pages, gemini_timeout_s,
        effective_multi_column, " (auto)" if auto_routed else "",
        len(preflight.rotation_pages),
    )

    # SCHEMA Week 5 — UNIFORM HANDLING for all PDF types.
    #
    # Previously: image-only PDFs were bypassed entirely (placeholder
    # schema, Gemini never called). That special-case was a patch from
    # an earlier model generation. It's removed now: every PDF type —
    # digital, scanned, image-only — runs through the exact same
    # pipeline. Gemini-2.5-Pro reads PDFs via vision regardless of
    # text layer, so no bypass is needed. If a PDF is genuinely
    # un-extractable, the validator + corrective-retry chain catches
    # it the same way it catches any other failure.
    #
    # Preflight is still useful for observability (PDF type logging,
    # timeout sizing) but does NOT branch the code path.
    logger.info(
        "Schema build: pdf_type=%s, %d page%s — uniform Gemini pipeline (no bypass).",
        preflight.pdf_type,
        preflight.total_pages,
        "" if preflight.total_pages == 1 else "s",
    )

    prompt_name = (
        "schema_architecture_multicolumn" if effective_multi_column else "schema_architecture"
    )
    base_prompt = load_raw(prompt_name, version="v2")

    # Track the previous attempt's validation errors so the next
    # attempt's prompt can include corrective instructions.
    last_errors: list = []
    last_err: Exception | None = None
    # SCHEMA Rebalance Layer 1/5 — track the best attempt for
    # accept-with-warnings + last_failed_schema preservation.
    last_attempt_data: dict | None = None
    last_attempt_warnings: list = []
    last_attempt_schema: BookSchema | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            # SCHEMA Day 3: corrective retry — each attempt after the
            # first appends specific fix instructions from prior errors.
            if attempt == 1 or not last_errors:
                prompt = base_prompt
            else:
                from app.services.schema_correctors import build_corrective_prompt
                prompt = build_corrective_prompt(
                    base_prompt,
                    last_errors,
                    pdf_total_pages=pdf_total_pages,
                )
                logger.info(
                    "Schema attempt %s — using corrective prompt for %d errors",
                    attempt, len(last_errors),
                )

            data = _run_gemini_schema(
                pdf_bytes,
                prompt,
                timeout_s=gemini_timeout_s,
                job_uuid=job_uuid,
            )
            # SCHEMA Day 14: sanitizer DELETED. Validator + corrective
            # retry handle all the cases sanitizer previously masked.
            data = assign_uuids_to_schema(data)

            # SCHEMA Week 5 — normalize hallucinated content_types BEFORE
            # validation. Maps singular/alias values (e.g. 'question',
            # 'solved_examples') and Gemini-invented categories (e.g.
            # 'summary', 'biography') into the validator's vocabulary
            # ({theory, questions, figures}). Idempotent. Closes the
            # BAD_CONTENT_TYPE finding class surfaced by the Week 5 audit
            # on 38 real books.
            from app.services.schema_content_type_normalizer import (
                normalize_schema_content_types,
            )
            data = normalize_schema_content_types(data)

            # SCHEMA Rebalance Layer 4 — deterministic page/puzzle sanitizers
            # run BEFORE validation so common Gemini errors auto-fix instead
            # of burning corrective retries.
            from app.services.schema_page_sanitizer import clamp_pages_to_bounds
            from app.services.schema_puzzle_sanitizer import remove_puzzle_sections

            data, n_clamped = clamp_pages_to_bounds(data, pdf_total_pages)
            if n_clamped:
                logger.info(
                    "Page clamp sanitizer: fixed %d out-of-bounds page value(s) "
                    "(attempt %s)", n_clamped, attempt,
                )
            data, n_puzzles_removed, puzzle_titles = remove_puzzle_sections(data)
            if n_puzzles_removed:
                logger.info(
                    "Puzzle sanitizer: removed %d puzzle section(s) %s (attempt %s)",
                    n_puzzles_removed, puzzle_titles, attempt,
                )

            # Cat A nesting sanitizer — deterministic post-pass that moves
            # misplaced Cat A subsections (siblings of theory) under their
            # immediately preceding Cat B sibling. Runs every attempt so
            # Gemini's mistakes are auto-corrected before validation.
            # Idempotent: re-running on already-sanitized data is a no-op.
            from app.services.schema_cat_a_nesting import sanitize_cat_a_nesting
            data, cat_a_report = sanitize_cat_a_nesting(data, pdf_bytes=pdf_bytes)
            if cat_a_report.reparented or cat_a_report.positional_reparented:
                logger.info(
                    "Cat A nesting sanitizer: sibling=%d positional=%d "
                    "(attempt %s, scanned_skip=%s)",
                    cat_a_report.reparented,
                    cat_a_report.positional_reparented,
                    attempt,
                    cat_a_report.positional_skipped_no_text,
                )

            # ── DETERMINISTIC PAGE ANCHOR (Task 1 Pass 2) ─────────────
            # Replace Gemini-emitted page ranges with PDF-grounded values
            # computed deterministically (pypdf + title match, no LLM).
            # Closes the bug class where Gemini sets every section to
            # page_start=page_end=N (Integrals: every section page=6,
            # missing ~50 questions because the worker scanned 1 page).
            #
            # Runs AFTER content-shape sanitizers (Cat A nesting, etc.)
            # so the tree is final, and BEFORE the validator so it sees
            # real pages. Idempotent + skipped for scanned PDFs.
            try:
                from app.schemas.analyser import BookSchema as _BSch
                from app.services.schema_page_anchor import anchor_pages_from_pdf
                _provisional_schema = _BSch(**data)
                _anchored, anchor_report = anchor_pages_from_pdf(
                    _provisional_schema, pdf_bytes,
                )
                if anchor_report.skipped_no_text:
                    logger.info(
                        "schema_page_anchor: skipped (scanned PDF, no pypdf text)"
                    )
                else:
                    logger.info(
                        "schema_page_anchor (attempt %s): walked=%d anchored=%d "
                        "confirmed=%d repaired=%d phantoms=%d overlaps=%d",
                        attempt,
                        anchor_report.total_sections,
                        anchor_report.anchored,
                        anchor_report.confirmed,
                        anchor_report.repaired,
                        len(anchor_report.phantoms),
                        len(anchor_report.sibling_overlap_warnings),
                    )
                    if anchor_report.phantoms:
                        logger.warning(
                            "schema_page_anchor: %d phantom section(s) (title not "
                            "found in PDF text — kept Gemini's page values): %s",
                            len(anchor_report.phantoms),
                            anchor_report.phantoms[:10],
                        )
                    # Mutated in place. Persist back into `data` dict for
                    # the remaining validator/postpass passes.
                    data = _anchored.model_dump()
            except Exception as e:
                logger.warning(
                    "schema_page_anchor failed (continuing with Gemini pages): %s", e
                )

            # SCHEMA Day 3: hard validation BEFORE accepting the schema.
            # If errors found, save them for the next attempt's corrective
            # prompt and retry.
            from app.services.schema_validator import validate_schema
            validation = validate_schema(data, pdf_total_pages=pdf_total_pages)
            # SCHEMA Rebalance Layer 1 — preserve this attempt's data so
            # we can accept-with-warnings if the loop exhausts retries.
            last_attempt_data = data
            last_attempt_warnings = [
                _err_to_dict(w) for w in validation.warnings
            ]
            if not validation.is_valid:
                last_errors = validation.errors
                logger.warning(
                    "Schema attempt %s failed validation: %d errors, %d warnings",
                    attempt, validation.error_count, validation.warning_count,
                )
                # Trigger next attempt with corrective prompt
                raise ValueError(
                    f"validation failed: {validation.error_count} errors"
                )

            schema = BookSchema(**data)
            last_attempt_schema = schema

            # Postpass — two passes:
            # Pass 1 (legacy): verify_schema_against_pdf_text — finds
            #   labels in PDF that Gemini missed. Logs warnings only.
            try:
                schema, _warnings = verify_schema_against_pdf_text(pdf_bytes, schema)
            except Exception as e:
                logger.warning("schema verifier failed (continuing): %s", e)

            # SCHEMA Day 5 — Pass 2: cross_check_section_pages.
            # For each section, verify title actually appears on claimed
            # page_start. If not, search PDF — if found exactly once
            # elsewhere, AUTO-CORRECT the page. If nowhere, flag as
            # phantom. Skipped for scanned PDFs.
            # Catches the "EXAMPLE 9.18 → page_start=9" subtle case
            # where the validator can't tell page=9 is wrong because
            # 9 IS a valid integer.
            try:
                from app.services.schema_postpass import (
                    cross_check_section_pages, apply_page_corrections,
                )
                cross_result = cross_check_section_pages(pdf_bytes, schema)
                if cross_result.skipped_no_text:
                    logger.info(
                        "schema cross-check: skipped (no pypdf text — scanned PDF)"
                    )
                else:
                    logger.info(
                        "schema cross-check: confirmed=%d corrections=%d "
                        "phantoms=%d skipped=%d",
                        cross_result.confirmed,
                        len(cross_result.corrections),
                        len(cross_result.phantoms),
                        cross_result.skipped_count,
                    )
                    if cross_result.corrections:
                        schema = apply_page_corrections(
                            schema, cross_result.corrections
                        )
                    if cross_result.phantoms:
                        for p in cross_result.phantoms:
                            logger.warning(
                                "schema cross-check: PHANTOM section '%s' "
                                "(claimed page=%s, not found in PDF text)",
                                p.section_title, p.claimed_page_start,
                            )
            except Exception as e:
                logger.warning("schema cross-check failed (continuing): %s", e)

            # SCHEMA Day 8 — Pass 3: cross_check_page_ends.
            # For each section in document order, verify its claimed
            # page_end against where the NEXT heading actually appears
            # in PDF text (via pypdf). Auto-correct (narrow only) when
            # Gemini over-reported page_end. Shared-boundary aware: if
            # next heading sits mid-page, this section's page_end can
            # legitimately equal that page. Skipped for scanned PDFs.
            try:
                from app.services.schema_postpass import (
                    cross_check_page_ends, apply_page_end_corrections,
                )
                end_corrections = cross_check_page_ends(pdf_bytes, schema)
                if end_corrections:
                    logger.info(
                        "schema page_end cross-check: %d correction(s)",
                        len(end_corrections),
                    )
                    schema = apply_page_end_corrections(schema, end_corrections)
                else:
                    logger.info("schema page_end cross-check: all clean")
            except Exception as e:
                logger.warning("schema page_end cross-check failed (continuing): %s", e)

            if return_result:
                return SchemaBuildResult(
                    schema=schema,
                    status="ok",
                    warnings=last_attempt_warnings,
                    last_failed_attempt=None,
                )
            return schema
        except Exception as e:
            last_err = e
            logger.warning("Schema attempt %s failed: %s", attempt, e)

    # SCHEMA Rebalance Layer 1 — never hard-fail. After MAX_ATTEMPTS,
    # accept the LAST attempt's schema with status="needs_review" and
    # surface the validation errors as warnings. Caller (extract worker)
    # writes book.schema_status="needs_review" + book.schema_warnings.
    logger.warning(
        "Schema validation exhausted %d attempts — accepting last attempt "
        "with status=needs_review (last_err=%s)",
        MAX_ATTEMPTS, last_err,
    )

    # Build the surfaced warnings: validator errors (now informational)
    # + any pre-existing warnings from the last attempt.
    surfaced: list[dict[str, Any]] = []
    for e in last_errors:
        d = _err_to_dict(e)
        # Mark these as "downgraded from error" so UI / ops can see
        # they're the reason we landed in needs_review.
        d["from_failed_validation"] = True
        surfaced.append(d)
    surfaced.extend(last_attempt_warnings)

    if last_attempt_data is None:
        # Every attempt blew up before producing parseable data — we
        # really have nothing to save. Surface a synthetic warning and
        # raise so callers know nothing landed.
        raise ValueError(
            f"Schema generation failed after {MAX_ATTEMPTS} attempts with no "
            f"parseable Gemini output: {last_err}"
        )

    # Try to construct a BookSchema from the last attempt; if pydantic
    # rejects it (e.g. fundamentally malformed), fall back to a minimal
    # cover-only schema so the book lifecycle can still progress.
    try:
        salvaged = BookSchema(**last_attempt_data)
    except Exception as e:
        logger.warning(
            "Could not construct BookSchema from last failed attempt "
            "(reason: %s) — caller must handle salvaged=None", e,
        )
        salvaged = None
        surfaced.append({
            "type": "pydantic_construction_failed",
            "severity": "error",
            "message": f"Last attempt's data could not be parsed: {e!s}",
        })

    result = SchemaBuildResult(
        schema=salvaged,
        status="needs_review",
        warnings=surfaced,
        last_failed_attempt=last_attempt_data,
    )
    if return_result:
        return result
    # Legacy callers expect a raised exception on failure. To preserve
    # backward-compat, raise here unless return_result was requested.
    raise ValueError(
        f"Schema generation needs review after {MAX_ATTEMPTS} attempts: "
        f"{last_err}"
    )


def _err_to_dict(err) -> dict[str, Any]:
    """Convert a ValidationError dataclass to a JSON-safe dict for
    surfacing via book.schema_warnings."""
    return {
        "type": getattr(err.type, "value", str(err.type)),
        "section_id": err.section_id,
        "section_title": err.section_title,
        "severity": err.severity,
        "message": err.message,
        "context": err.context,
    }
