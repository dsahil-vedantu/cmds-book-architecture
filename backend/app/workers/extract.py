"""Celery tasks for the extraction pipeline.

Tasks:
  - analyse_book_task       — Gemini schema generation (P2)
  - extract_book_task       — Per-section Gemini OCR extraction in schema order
  - re_extract_section_task — User-triggered re-extract (OCR retry for a section)
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.storage import download_pdf
from app.models.book import Book
from app.models.job import Job
from app.models.regeneration import Regeneration
from app.models.section import Section
from app.schemas.analyser import AnalyserResult, BookSchema
from app.schemas.regen import RegenParams
from app.services.chunk_builder import flatten_sections
from app.services.regenerator import post_regen_qc, regenerate_section
from app.services.schema_builder import build_schema
from app.core.heartbeat import Heartbeat
from app.services.theory_extractor import (
    ExtractionResult,
    extract_section_with_qc,
    re_extract_with_fix,
)
from app.services.theory_slice import (
    SliceComputationError,
    compute_extraction_slice,
)
from app.workers.celery_app import celery_app
from app.workers.runner import register as register_task

logger = logging.getLogger(__name__)

_sync_engine = create_engine(
    settings.SYNC_DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5, max_overflow=5, pool_timeout=20, pool_recycle=900,
)
SyncSession = sessionmaker(bind=_sync_engine, class_=Session, autoflush=False)


# Outcome classification — a QC failure is "soft" when the section still
# carries real content and the only signal is a completeness heuristic
# (low density / suspected truncation). Density retries have ALREADY run
# inside extract_section_with_qc; this governs the FINAL disposition only.
# Hard failures (empty result, normalizer drop-ratio, solution/question
# bleed, OCR error) still mark the section failed. Invariant: a section
# that extracted real content is never a hard "failed" that poisons
# theory_status — it is accepted and flagged for review instead.
_SOFT_QC_SIGNALS = ("Content density too low",)


def _is_soft_qc_failure(failures: list[str]) -> bool:
    return bool(failures) and all(
        any(sig in f for sig in _SOFT_QC_SIGNALS) for f in failures
    )


def _update_job(session: Session, job_id: UUID, **fields) -> None:
    job = session.get(Job, job_id)
    if job is None:
        return
    for k, v in fields.items():
        setattr(job, k, v)
    # Bump heartbeat on every progress/message update so the watchdog measures
    # time since real progress, not since job start. Without this, any run
    # longer than the watchdog's stale window (5 min) gets killed regardless
    # of how much work is actually happening.
    from datetime import datetime, timezone
    job.last_heartbeat_at = datetime.now(timezone.utc)
    session.commit()



def _local_analyse_pdf(pdf_bytes: bytes) -> "AnalyserResult | None":
    """Compute PDF metadata locally via pymupdf for digital PDFs.

    Returns None if the PDF appears to be scanned/image-based (< 200 chars of
    extractable text across the first 10 pages). In that case, metadata is
    derived from the Gemini schema output instead.
    """
    import re as _re

    try:
        import pymupdf

        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        n_pages = len(doc)
        sample_pages = min(n_pages, 10)

        full_text = ""
        for i in range(sample_pages):
            try:
                full_text += doc[i].get_text() or ""
            except Exception:
                pass
        doc.close()

        if len(full_text.strip()) < 200:
            return None  # scanned/image — metadata derived from Gemini schema instead

        word_count = len(full_text.split())
        # Scale word estimate to full document
        estimated_words = int(word_count * n_pages / max(sample_pages, 1))

        has_equations = bool(
            _re.search(
                r"[=∫∑∏√∞≤≥∈∉∀∃]|\\frac|\\sqrt|\d+\s*[+\-\*/]\s*\d+",
                full_text,
            )
        )
        has_tables = bool(
            _re.search(r"\t\S+\t", full_text)
            or (full_text.count("\n") / max(len(full_text), 1)) > 0.15
        )
        has_diagrams = bool(
            _re.search(r"fig(?:ure)?\.?\s*\d|diagram|illustration|graph", full_text, _re.I)
        )

        return AnalyserResult(
            pdf_type="digital",
            estimated_pages=n_pages,
            estimated_words=estimated_words,
            # Title/subject come from the schema (P2) — leave empty here.
            document_title="",
            subject="",
            has_equations=has_equations,
            has_tables=has_tables,
            has_diagrams=has_diagrams,
        )
    except Exception as e:
        logger.warning("Local PDF analysis failed, will use Gemini schema metadata: %s", e)
        return None


# ── analyse_book (Sprint 1) ─────────────────────────────────────────────

@celery_app.task(name="analyse_book", bind=True)
def analyse_book_task(self, book_id: str, job_id: str) -> dict:
    book_uuid = UUID(book_id)
    job_uuid = UUID(job_id)

    with SyncSession() as session:
        _update_job(
            session,
            job_uuid,
            status="running",
            started_at=datetime.utcnow(),
            message="Downloading PDF",
            progress=5,
        )

        book = session.get(Book, book_uuid)
        if book is None or not book.pdf_url:
            _update_job(
                session,
                job_uuid,
                status="failed",
                error="Book or pdf_url missing",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "book_not_found"}

        try:
            pdf_bytes = download_pdf(book.pdf_url)

            # Fast path: try to derive P1 metadata locally (pymupdf, no Claude call).
            # This works for digital PDFs and saves one full agent subprocess round-trip.
            _update_job(session, job_uuid, message="Analysing PDF", progress=15)
            local_result = _local_analyse_pdf(pdf_bytes)

            # Read the user-set multi-column flag from book.analyser (set
            # at upload time via POST /api/books form param is_multi_column).
            # When True, build_schema routes to the multi-column-aware prompt
            # so dense MCQ-bank pages (MHT-CET / JEE) don't get mis-tagged
            # as "all explanations" and silently dropped. Defaults to False
            # so single-column books are processed exactly as before.
            existing_analyser = book.analyser or {}
            is_multi_column = bool(existing_analyser.get("is_multi_column", False))

            # Always run Gemini schema (handles digital, scanned, and image PDFs natively).
            # For digital PDFs we have local_result metadata; for scanned/image we derive
            # metadata from the schema output — no Claude P1 call needed for any type.
            pdf_type = "digital" if local_result is not None else "scanned"
            layout_tag = "multi-column" if is_multi_column else pdf_type
            _update_job(session, job_uuid, message=f"Running Gemini schema ({layout_tag} PDF)", progress=30)
            # Phase 5d (CONTRACT.md §2): mark schema stage as running. Lets
            # /quality endpoint distinguish "schema in flight" from "schema
            # not yet attempted". Watchdog (Phase 7) will look for stale
            # "running" stages.
            book.schema_status = "running"
            session.commit()
            # Heartbeat keeps the watchdog from killing long Gemini schema
            # calls for scanned PDFs (5–10 min is normal for image-based pages).
            with Heartbeat(
                job_uuid,
                base_msg=f"Running Gemini schema ({layout_tag} PDF)",
                progress=30,
            ):
                # SCHEMA Rebalance — request the structured result so we
                # can handle accept-with-warnings (status="needs_review")
                # without losing the salvaged schema.
                build_result = build_schema(
                    pdf_bytes,
                    is_multi_column=is_multi_column,
                    pdf_title=(book.title or None),
                    job_uuid=job_uuid,
                    return_result=True,
                )
            schema = build_result.schema
            schema_build_status = build_result.status
            schema_warnings_payload = build_result.warnings
            last_failed_attempt = build_result.last_failed_attempt
            if schema is None:
                # No salvageable schema — fall through to the except path
                # via raise, which marks schema_status="failed" + writes
                # book.last_failed_schema for offline diagnosis.
                if last_failed_attempt is not None:
                    book.last_failed_schema = last_failed_attempt
                if schema_warnings_payload:
                    book.schema_warnings = schema_warnings_payload
                session.commit()
                raise ValueError(
                    "Schema generation produced no parseable output across all attempts"
                )

            # Derive AnalyserResult: use pymupdf fast-path if available, otherwise
            # build it entirely from the Gemini schema output (no Claude P1 needed).
            if local_result is not None:
                analyser_result = local_result
            else:
                import pymupdf as _pymupdf
                try:
                    _doc = _pymupdf.open(stream=pdf_bytes, filetype="pdf")
                    _n = len(_doc)
                    _doc.close()
                except Exception:
                    _n = schema.total_pages or 0
                analyser_result = AnalyserResult(
                    pdf_type="scanned",
                    estimated_pages=_n or schema.total_pages or 0,
                    estimated_words=(_n or 1) * 250,
                    document_title=schema.document_title,
                    subject=schema.subject or "",
                    has_equations=True,
                    has_tables=False,
                    has_diagrams=True,
                )

            # Always patch title/subject from schema (Gemini reads cover page correctly).
            analyser_result = AnalyserResult(
                **{**analyser_result.model_dump(),
                   "document_title": schema.document_title or analyser_result.document_title,
                   "subject": schema.subject or analyser_result.subject}
            )

            # Preserve the upload-time multi-column flag on analyser
            # overwrite so re-analyse calls keep routing to the right prompt.
            new_analyser = analyser_result.model_dump()
            if is_multi_column:
                new_analyser["is_multi_column"] = True

            # Lock previously-extracted section_ids when re-analysing an
            # existing book. The freshly generated schema can carry new IDs
            # (e.g. Gemini moves from "5-introduction" to "5.3"); without
            # alignment, all DB sections become orphans of the new schema
            # and the sidebar / merge / export silently lose them. Match
            # each new node to an existing DB Section row by (title + page
            # range) and force the new node's id back to the extraction-time
            # id. No-op when there are no existing sections (first analyse).
            try:
                from app.services.schema_alignment import (
                    align_schema_ids_to_existing_sections,
                )
                existing_secs = (
                    session.execute(
                        select(Section).where(Section.book_id == book.id)
                    )
                ).scalars().all()
                if existing_secs:
                    schema, _remap = align_schema_ids_to_existing_sections(
                        schema, existing_secs
                    )
            except Exception as e:
                logger.warning(
                    "schema_alignment failed (continuing with fresh IDs): %s", e
                )

            # ── ATOMIC SCHEMA PUBLISH (race-safe) ──────────────────────
            # Schema content + status flip MUST happen as one transaction.
            # Two analyse jobs racing on the same book both set
            # schema_status="running" at start; the first to finish
            # CAS-flips it to "done" and dispatches theory. Previously the
            # loser still wrote book.schema BEFORE the CAS check, clobbering
            # the winner's schema while theory was already running against
            # the winner's version. Result: schema in DB diverged from the
            # schema theory consumed (Unit-and-Measurement bug — 17
            # sections silently lost).
            #
            # Fix: gate EVERY write (analyser, schema, warnings, title,
            # subject, status) on owning the "running" slot. If we lost the
            # race, drop the entire result. The winner's writes stand.
            from app.workers.orchestrator import cas_set_stage
            # SCHEMA Rebalance — needs_review is a non-blocking outcome:
            # schema is saved + lifecycle continues, but the schema_status
            # flag tells the UI / downstream stages that validator
            # warnings need user attention.
            target_status = (
                "needs_review" if schema_build_status == "needs_review" else "done"
            )
            if cas_set_stage(
                session, book_uuid, "schema", target_status, from_states=("running",),
            ):
                # We own the slot — publish ALL the content atomically.
                book.analyser = new_analyser
                book.schema = schema.model_dump()
                if schema_warnings_payload:
                    book.schema_warnings = schema_warnings_payload
                else:
                    book.schema_warnings = None
                if last_failed_attempt is not None:
                    book.last_failed_schema = last_failed_attempt
                # Preserve the user-supplied title. Only fall back to the
                # schema's guessed title if the upload had none.
                if not (book.title and book.title.strip()):
                    book.title = schema.document_title or "Untitled"
                book.subject = schema.subject or book.subject
                book.status = "schema_ready"
                session.commit()
            else:
                logger.info(
                    "analyse_book: schema_status no longer 'running' — "
                    "dropping schema write entirely (sibling/reset won) "
                    "book=%s. NOT writing book.schema, book.analyser, "
                    "or any related fields — winner's values stand.",
                    book_uuid,
                )
                # Don't dispatch the coordinator either — the winner
                # already did at its own completion. Return early.
                _update_job(
                    session,
                    job_uuid,
                    status="succeeded",
                    progress=100,
                    message="Schema race lost — winner's schema preserved",
                    finished_at=datetime.utcnow(),
                )
                return {"ok": True, "book_id": str(book_uuid), "lost_race": True}

            success_message = (
                f"Schema saved with {len(schema_warnings_payload)} validator "
                f"warning(s) — needs_review"
                if schema_build_status == "needs_review"
                else "Schema ready for approval"
            )
            _update_job(
                session,
                job_uuid,
                status="succeeded",
                progress=100,
                message=success_message,
                finished_at=datetime.utcnow(),
            )

            # Phase 6 (ORCH Day 3) — auto-fire post-schema orchestrator.
            # coordinate_extraction is idempotent and decides whether to
            # actually kick off theory based on current state. Removes
            # the dependency on a polling frontend to call /approve —
            # schema completion now triggers downstream extraction
            # without any UI interaction.
            try:
                from app.workers.runner import dispatch
                dispatch("coordinate_extraction", str(book_uuid))
                logger.info(
                    "analyse_book: dispatched coordinator for book=%s",
                    book_uuid,
                )
            except Exception as e:
                # Coordinator dispatch failure should not fail the
                # schema task — user can manually re-fire via /approve
                # as a fallback.
                logger.warning(
                    "analyse_book: coordinator dispatch failed (continuing): %s",
                    e,
                )

            return {"ok": True, "book_id": str(book_uuid)}

        except Exception as e:
            logger.exception("analyse_book_task failed")
            session.rollback()
            _update_job(
                session,
                job_uuid,
                status="failed",
                error=str(e)[:2000],
                finished_at=datetime.utcnow(),
            )
            book = session.get(Book, book_uuid)
            if book is not None:
                # Atomic CAS — only mark schema_status="failed" if we
                # still own the running slot. Without this guard, a
                # duplicate-dispatched worker's late failure could
                # overwrite a sibling's successful "done" state — which
                # produced the "Backend reports book.status='failed'"
                # dead-end on Class 9th Maths. With B1 (CAS at dispatch)
                # duplicates shouldn't happen, but this is defence in
                # depth — also protects against /re-extract racing with
                # an in-flight worker.
                from app.workers.orchestrator import cas_set_stage
                if cas_set_stage(
                    session, book_uuid, "schema", "failed",
                    from_states=("running",),
                ):
                    book.status = "failed"
                    session.commit()
                else:
                    logger.info(
                        "analyse_book: dropping failure tail — "
                        "schema_status no longer 'running' book=%s",
                        book_uuid,
                    )
            return {"ok": False, "error": str(e)}


# ── extract_book (Sprint 2) ─────────────────────────────────────────────

@celery_app.task(name="extract_book", bind=True)
def extract_book_task(self, book_id: str, job_id: str) -> dict:
    """Per-section Gemini OCR extraction using page ranges from approved schema."""
    book_uuid = UUID(book_id)
    job_uuid = UUID(job_id)

    with SyncSession() as session:
        _update_job(
            session,
            job_uuid,
            status="running",
            started_at=datetime.utcnow(),
            message="Loading book + schema",
            progress=2,
        )
        book = session.get(Book, book_uuid)
        if book is None or book.schema is None:
            _update_job(
                session,
                job_uuid,
                status="failed",
                error="Book or schema missing",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "book_or_schema_missing"}

        try:
            if not book.pdf_url:
                raise RuntimeError("PDF URL missing — cannot extract")
            pdf_bytes = download_pdf(book.pdf_url)

            # Phase 5d (CONTRACT.md §2): mark theory stage as running.
            book.theory_status = "running"
            session.commit()

            schema = BookSchema(**book.schema)
            # all_sections: full flat list — used for both DB upsert and extraction.
            # chunk_builder bounds every section to its own content only (header → next
            # section start), so extracting all sections causes zero duplication — each
            # section only gets its own intro text, not its children's content.
            all_sections = flatten_sections(schema)
            if not all_sections and not (schema.excluded_sections or []):
                raise RuntimeError("Schema has no sections AND no excluded banks — approve the schema before extracting")

            # Skip pure Cat A (questions-only) sections from theory extraction.
            # They're handled by the question pipeline as placeholders. Calling
            # Gemini on them wastes attempts (Gemini correctly returns nothing
            # per the placeholder rule, QC fails, retries burn out).
            # Sections with "theory" in content_types (including Mixed
            # "theory + questions") DO get extracted as theory.
            to_extract = [
                s for s in all_sections
                if "theory" in (s.content_types or ["theory"])
            ]

            skipped_cat_a = len(all_sections) - len(to_extract)
            if skipped_cat_a > 0:
                logger.info(
                    "Skipping %d Cat A (questions-only) sections from theory extraction "
                    "(handled by question pipeline as placeholders)",
                    skipped_cat_a,
                )

            # Top-level section IDs (direct children of schema root).
            # These get extracted with their FULL page range to give a complete
            # chapter overview — not trimmed like intermediate containers.
            top_level_ids = {s.id for s in schema.sections if s.type != "excluded"}

            # Upsert ALL section rows so page ranges and hierarchy are stored
            existing = {
                s.section_id: s
                for s in session.execute(
                    select(Section).where(Section.book_id == book_uuid)
                ).scalars().all()
            }
            from uuid import UUID as _UUID
            for sec_schema in all_sections:
                sec = existing.get(sec_schema.id)
                if sec is None:
                    # Canonical identity (CONTRACT.md §1): when the schema
                    # carries a UUID for this section, use it as the Section
                    # row's primary key. This makes Section.id == schema.uuid
                    # so downstream UUID-keyed lookups (sections endpoint
                    # ordering, embedder placement) match cleanly without
                    # slug heuristics. Legacy schemas without uuid keep
                    # default uuid4() — old books still work via slug.
                    sec_kwargs = {
                        "book_id": book_uuid,
                        "section_id": sec_schema.id,
                        "title": sec_schema.title,
                        "level": sec_schema.level,
                        "page_start": sec_schema.page_start,
                        "page_end": sec_schema.page_end,
                        "blocks": [],
                        "status": "pending",
                        "attempts": 0,
                    }
                    if getattr(sec_schema, "uuid", None):
                        try:
                            sec_kwargs["id"] = _UUID(sec_schema.uuid)
                        except (TypeError, ValueError):
                            pass  # malformed uuid → fall back to default uuid4()
                    sec = Section(**sec_kwargs)
                    session.add(sec)
                else:
                    sec.title = sec_schema.title
                    sec.level = sec_schema.level
                    sec.page_start = sec_schema.page_start
                    sec.page_end = sec_schema.page_end
                    sec.status = "pending"
            session.commit()

            # SCHEMA Week 5 / Solution 1 — pure-Q chapter fast-path.
            # If the schema has zero Cat B sections (theory worker has
            # nothing to extract), skip the extraction loop + tails
            # entirely and finalize immediately. Without this, the
            # worker still iterates an empty list, runs linker +
            # embedder tails (which are no-ops for pure-Q but still
            # consume time), and may LOOK to the user like "still
            # analysing" even though there's nothing to do.
            if not to_extract:
                logger.info(
                    "extract_book: pure-Q chapter — 0 Cat B sections to extract "
                    "(book=%s). Fast-path: skip Gemini loop + linker/embedder "
                    "tails, finalize immediately.",
                    book_uuid,
                )
                from app.workers.orchestrator import cas_set_stage
                if cas_set_stage(
                    session, book_uuid, "theory", "done",
                    from_states=("running",),
                ):
                    session.refresh(book)
                    book.theory_finalized_at = datetime.utcnow()
                    from app.services.book_status import derive_book_status
                    derived = derive_book_status(book)
                    book.status = "extracting" if derived == "queued" else derived
                    session.commit()
                else:
                    logger.info(
                        "extract_book pure-Q fast-path: CAS lost (theory_status "
                        "no longer 'running') — terminal write dropped book=%s",
                        book_uuid,
                    )
                # Fire coordinator so Q+F dispatch can proceed even though
                # theory found nothing.
                try:
                    from app.workers.runner import dispatch
                    dispatch("coordinate_extraction", str(book_uuid))
                except Exception as e:
                    logger.warning(
                        "extract_book pure-Q: coordinator dispatch failed "
                        "(continuing): %s", e,
                    )
                _update_job(
                    session, job_uuid,
                    status="succeeded", progress=100,
                    message=(
                        "Pure-Q chapter — no theory sections to extract. "
                        "Question pipeline will handle the content."
                    ),
                    finished_at=datetime.utcnow(),
                )
                return {
                    "ok": True,
                    "book_id": str(book_uuid),
                    "total": 0,
                    "failed": [],
                    "pure_q_fast_path": True,
                }

            total = len(to_extract)
            failed_section_ids: list[str] = []

            # ── PARALLEL SECTION EXTRACTION ────────────────────────────────
            # Each section's Gemini call is an independent HTTP request — no
            # shared context, no cross-section bleeding (impossible by design,
            # since Gemini doesn't keep state between requests). Concurrency
            # controlled by THEORY_SECTION_CONCURRENCY env var (default 8).
            # Set to 1 to revert to sequential behaviour byte-for-byte.
            #
            # SAFETY GUARDS (all preserved from sequential version):
            #   1. Same theory_extractor.extract_section_with_qc call per
            #      section — same prompt, same Pro model, same retry policy.
            #   2. Each task uses its OWN SyncSession for DB writes (no
            #      shared session across tasks → no race / lock corruption).
            #   3. Failures are isolated per-section (return_exceptions=True)
            #      — one section's crash does not abort the batch.
            #   4. Per-section retry inside extract_section_with_qc is
            #      untouched (MAX_ATTEMPTS=3 + backoff).
            #   5. Post-write section_id verification (paranoid guard) catches
            #      any cross-section write corruption with a loud assert.
            #   6. Schema postpass + example_linker still run AFTER all
            #      sections complete — same invariant as the sequential loop.
            CONCURRENCY = max(1, int(os.environ.get("THEORY_SECTION_CONCURRENCY", "8")))

            # Unit 1 — Page-Range Engine. Pre-compute deterministic
            # SliceSpec per section via the single source-of-truth function
            # in theory_slice. No more scattered effective_page_end formula;
            # no more silent fallbacks. Sections whose slice can't be
            # resolved get persisted as status="failed" with a clear
            # diagnostic and are skipped from the parallel phase.
            import pymupdf as _pymupdf
            try:
                _doc = _pymupdf.open(stream=pdf_bytes, filetype="pdf")
                pdf_total_pages = len(_doc)
                _doc.close()
            except Exception:
                pdf_total_pages = schema.total_pages or 0

            section_payloads: list[dict] = []
            slice_failures: list[tuple[str, str]] = []
            for i, sec_schema in enumerate(to_extract, start=1):
                try:
                    slice_spec = compute_extraction_slice(
                        sec_schema, schema, pdf_total_pages
                    )
                except SliceComputationError as e:
                    logger.warning(
                        "Slice computation failed for section=%s book=%s: %s",
                        sec_schema.id, book_uuid, e.reason,
                    )
                    slice_failures.append((sec_schema.id, e.reason))
                    continue
                if slice_spec.diagnostics:
                    logger.info(
                        "Slice diagnostics for section=%s: %s",
                        sec_schema.id, "; ".join(slice_spec.diagnostics),
                    )
                section_payloads.append({
                    "sec_schema": sec_schema,
                    "slice_spec": slice_spec,
                    "idx": i,
                })

            # Persist slice failures up-front so the user sees a clear
            # status='failed' with a diagnostic rather than a stuck section.
            if slice_failures:
                with SyncSession() as _fail_session:
                    for sid, reason in slice_failures:
                        _sec = _fail_session.execute(
                            select(Section).where(
                                Section.book_id == book_uuid,
                                Section.section_id == sid,
                            )
                        ).scalar_one_or_none()
                        if _sec is not None:
                            _sec.status = "failed"
                            _sec.qc_local = {
                                "pass": False,
                                "failures": [f"slice_unresolvable: {reason}"],
                            }
                            _sec.attempts = (_sec.attempts or 0) + 1
                    _fail_session.commit()

            # Shared mutable counter for monotonic progress reporting. Each
            # task increments under the lock and writes the new progress
            # via its own SyncSession.
            done_counter = {"n": 0}
            counter_lock = asyncio.Lock()

            async def _extract_and_persist(payload: dict) -> tuple[str, str]:
                """Run one section through extract_section_with_qc and
                persist the result. Returns (section_id, outcome) where
                outcome is one of: ok / failed / skipped / crashed."""
                sec_schema = payload["sec_schema"]
                slice_spec = payload["slice_spec"]
                is_container = slice_spec.is_container
                try:
                    result: ExtractionResult = await extract_section_with_qc(
                        section_id=sec_schema.id,
                        title=sec_schema.title,
                        level=sec_schema.level,
                        pdf_bytes=pdf_bytes,
                        slice_spec=slice_spec,
                    )
                except Exception as e:
                    logger.exception(
                        "extract_section_with_qc crashed on %s", sec_schema.id
                    )
                    # Persist failure in this task's own session
                    with SyncSession() as own_session:
                        sec = own_session.execute(
                            select(Section).where(
                                Section.book_id == book_uuid,
                                Section.section_id == sec_schema.id,
                            )
                        ).scalar_one_or_none()
                        if sec is not None:
                            sec.status = "failed"
                            sec.qc_local = {
                                "pass": False,
                                "failures": [str(e)[:500]],
                            }
                            sec.attempts = (sec.attempts or 0) + 1
                        own_session.commit()
                    return (sec_schema.id, "crashed")

                # Persist success in this task's own session
                outcome: str
                with SyncSession() as own_session:
                    sec = own_session.execute(
                        select(Section).where(
                            Section.book_id == book_uuid,
                            Section.section_id == sec_schema.id,
                        )
                    ).scalar_one()
                    sec.blocks = result.blocks
                    sec.qc_local = result.qc.to_dict()
                    sec.attempts = result.attempts
                    # Container parents (sections with non-excluded children)
                    # legitimately return empty blocks when the parent-vs-leaf
                    # rule (f0a574a) finds no content between the parent
                    # heading and the first child — children carry it all.
                    # Mark these as "passed" with empty blocks so the heading
                    # stays visible in the sidebar + Preview / Composer /
                    # DOCX with a blank body, preserving the schema
                    # hierarchy. Previously these were "skipped" and hidden,
                    # making the chapter look like sections were missing.
                    if is_container and not result.blocks:
                        sec.status = "passed"
                        outcome = "passed"
                    elif result.qc.pass_:
                        sec.status = "passed"
                        outcome = "passed"
                    elif result.blocks and _is_soft_qc_failure(result.qc.failures):
                        # Section extracted real content but tripped ONLY a
                        # soft completeness signal (low density / suspected
                        # truncation). Density retries already ran inside
                        # extract_section_with_qc. Accept the content and flag
                        # it for review rather than marking the section failed
                        # — a hard failure here would poison theory_status to
                        # "partial" even though the content is present.
                        sec.status = "passed"
                        sec.qc_local = {
                            **result.qc.to_dict(),
                            "low_density_review": True,
                        }
                        outcome = "passed"
                    else:
                        sec.status = "failed"
                        outcome = "failed"
                    own_session.commit()
                    # PARANOID: verify section_id matches after commit. If
                    # something somehow corrupted the write, fail LOUDLY.
                    check = own_session.execute(
                        select(Section).where(
                            Section.book_id == book_uuid,
                            Section.section_id == sec_schema.id,
                        )
                    ).scalar_one()
                    if check.section_id != sec_schema.id:
                        raise RuntimeError(
                            f"Post-write section_id mismatch: "
                            f"expected={sec_schema.id} got={check.section_id}"
                        )

                # Atomic progress update — each task contributes one tick.
                # Progress goes 10 → 95 as sections complete.
                async with counter_lock:
                    done_counter["n"] += 1
                    n_done = done_counter["n"]
                with SyncSession() as own_session:
                    prog = 10 + int(85 * n_done / max(total, 1))
                    _update_job(
                        own_session,
                        job_uuid,
                        message=f"Extracted {n_done}/{total} sections",
                        progress=prog,
                    )
                    own_session.commit()
                return (sec_schema.id, outcome)

            async def _run_parallel() -> list[tuple[str, str]]:
                sem = asyncio.Semaphore(CONCURRENCY)

                async def gated(p):
                    async with sem:
                        return await _extract_and_persist(p)

                # return_exceptions=True so one section's unexpected error
                # in the OUTER plumbing (not the Gemini call — that's caught
                # inside) doesn't bring down the whole batch.
                return await asyncio.gather(
                    *[gated(p) for p in section_payloads],
                    return_exceptions=True,
                )

            # One outer Heartbeat covers the whole parallel phase so the
            # watchdog doesn't kill the job during long Gemini calls. With
            # concurrency=4, expected wall-clock is total_sections/4 × per-
            # section time. Heartbeat thread is independent of the worker.
            with Heartbeat(
                job_uuid,
                base_msg=(
                    f"Extracting {total} sections "
                    f"(parallel × {CONCURRENCY})"
                ),
                progress=10,
            ):
                outcomes = asyncio.run(_run_parallel())

            # Tally failures and crashes for the final job summary
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    # An unexpected error inside _extract_and_persist itself
                    # (not the Gemini call — that's caught inside). Log and
                    # treat the whole batch as having had a failure; we
                    # cannot determine which section_id it came from at this
                    # layer, so the message will reflect it at job-end.
                    logger.exception("section task plumbing crashed: %s", outcome)
                    continue
                section_id, status = outcome
                if status in ("failed", "crashed"):
                    failed_section_ids.append(section_id)

            # Phase 5d / ORCH Day 4 — derive theory_status from outcomes
            # but DEFER persisting it until after example_linker +
            # figure_embedder finish their tail work. Today's race:
            # theory_status="done" committed here → frontend polling sees
            # "done" → fires kickRestParallel → questions+figures workers
            # start while figure_embedder (in tail below) is still mid-
            # run, causing two embedder passes to race on Section blocks.
            # Strict ORCH design: theory_status remains "running" in DB
            # until AFTER the tail completes, then committed together
            # with theory_finalized_at — the coordinator gate field.
            if total == 0:
                _derived_theory_status = "done"
            elif len(failed_section_ids) == 0:
                _derived_theory_status = "done"
            elif len(failed_section_ids) == total:
                _derived_theory_status = "failed"
            else:
                _derived_theory_status = "partial"
            # NB: book.theory_status NOT set here; book.status NOT updated
            # here. Both deferred to the post-tail finalization block
            # below (after example_linker + figure_embedder).

            # Theory Worker Unit 5 — parent/child block dedup. Strips
            # any parent block whose content also appears in a descendant
            # (direct child, grandchild, etc.). Replaces the 4 layered
            # patches (`is_container` flag + PARENT vs LEAF prompt rule +
            # container empty force-pass + tautological paranoid check)
            # with one deterministic post-extraction pass. Runs BEFORE the
            # linker so chips aren't inserted into blocks that are about
            # to be stripped. Failure-safe: on exception, original blocks
            # survive (no destructive write without successful completion).
            try:
                from app.services.theory_dedup import dedup_theory_blocks
                dedup_theory_blocks(session, book_uuid, schema)
            except Exception as e:
                logger.warning("theory_dedup failed (book=%s): %s", book_uuid, e)

            # Inject example/exercise placeholder chips into parent theory
            # sections. Idempotent post-processing — does not modify
            # transcribed theory blocks beyond inserting `question_ref`
            # references for child `<parent>-example-N` sections.
            try:
                from app.services.example_linker import link_examples_to_theory_sync
                link_examples_to_theory_sync(session, book_uuid)
            except Exception as e:
                logger.warning("example_linker failed (book=%s): %s", book_uuid, e)

            # Auto-embed figures into the freshly-extracted theory blocks.
            # If figures were extracted before theory, this is when the
            # figure_references finally land in the right sections. No-op if
            # the book has no figures yet — embedder is idempotent.
            try:
                from app.services.figure_embedder import embed_figures_for_book_sync
                embed_counters = embed_figures_for_book_sync(session, book_uuid)
                logger.info(
                    "[embed] post-theory book=%s %s", book_uuid, embed_counters
                )
            except Exception as e:
                logger.warning(
                    "figure_embedder failed post-theory (book=%s): %s", book_uuid, e
                )

            # Phase 6 (ORCH Day 4) — FINALIZE theory after tail completes.
            # example_linker + figure_embedder have flushed; it's now
            # safe to mark theory truly done. theory_finalized_at is the
            # coordinator's gate field for dispatching questions+figures.
            #
            # Atomic CAS — only commit the terminal status if we still
            # own the "running" slot. Drops cleanly if /re-extract reset
            # us or a duplicate worker is racing.
            from app.workers.orchestrator import cas_set_stage
            if cas_set_stage(
                session, book_uuid, "theory", _derived_theory_status,
                from_states=("running",),
            ):
                # Refresh in-memory book then write finalized_at + derive
                # book.status. We won the race; do all the bookkeeping.
                session.refresh(book)
                book.theory_finalized_at = datetime.utcnow()
                from app.services.book_status import derive_book_status
                derived = derive_book_status(book)
                book.status = "extracting" if derived == "queued" else derived
                session.commit()
            else:
                logger.info(
                    "extract_book: dropping theory terminal write — "
                    "theory_status no longer 'running' book=%s",
                    book_uuid,
                )
                # Still skip-fire the coordinator below — if we lost the
                # race, the winning sibling/reset already triggered the
                # next coordinator pass. Returning here would leave the
                # job in a half-finished state for the caller, so fall
                # through to the job-update + return block below.

            # Step the state machine forward — coordinator typically
            # dispatches extract_questions_v3 + extract_figures_v2 in
            # parallel from here. Idempotent; safe even if frontend
            # also polled and tried to fire kickRestParallel.
            try:
                from app.workers.runner import dispatch
                dispatch("coordinate_extraction", str(book_uuid))

                # ── FAIR QUEUE WAKE-UP ───────────────────────────────
                # We just freed a theory slot. Wake the oldest pending
                # book so the queue advances. Coordinator is idempotent
                # — if no book is queued, the dispatch is a no-op.
                # Without this, queued books would sit forever until
                # something else (frontend poll, watchdog) poked them.
                try:
                    queued = session.execute(
                        select(Book.id).where(
                            Book.theory_status == "pending",
                            Book.id != book_uuid,
                        ).order_by(Book.created_at.asc()).limit(1)
                    ).scalars().first()
                    if queued is not None:
                        dispatch("coordinate_extraction", str(queued))
                        logger.info(
                            "extract_book: fair-queue wake — poked "
                            "queued book=%s after book=%s finished",
                            queued, book_uuid,
                        )
                except Exception as _qe:
                    logger.warning(
                        "extract_book: fair-queue wake failed (non-fatal): %s",
                        _qe,
                    )
                logger.info(
                    "extract_book: theory finalized for book=%s status=%s "
                    "— dispatched coordinator",
                    book_uuid, _derived_theory_status,
                )
            except Exception as e:
                logger.warning(
                    "extract_book: coordinator dispatch failed (continuing): %s",
                    e,
                )

            _update_job(
                session,
                job_uuid,
                status="succeeded",
                progress=100,
                message=(
                    f"Extracted {total} sections; {len(failed_section_ids)} need review"
                    if failed_section_ids
                    else f"Extracted {total} sections"
                ),
                finished_at=datetime.utcnow(),
            )
            return {
                "ok": True,
                "book_id": str(book_uuid),
                "total": total,
                "failed": failed_section_ids,
            }

        except Exception as e:
            logger.exception("extract_book_task failed")
            session.rollback()
            _update_job(
                session,
                job_uuid,
                status="failed",
                error=str(e)[:2000],
                finished_at=datetime.utcnow(),
            )
            book = session.get(Book, book_uuid)
            if book is not None:
                # Atomic CAS — same protection as analyse failure tail.
                from app.workers.orchestrator import cas_set_stage
                if cas_set_stage(
                    session, book_uuid, "theory", "failed",
                    from_states=("running",),
                ):
                    book.status = "failed"
                    session.commit()
                else:
                    logger.info(
                        "extract_book: dropping failure tail — "
                        "theory_status no longer 'running' book=%s",
                        book_uuid,
                    )
            return {"ok": False, "error": str(e)}


# ── re_extract_section (Sprint 2) ───────────────────────────────────────

@celery_app.task(name="re_extract_section", bind=True)
def re_extract_section_task(self, section_id: str, job_id: str) -> dict:
    """Re-run Gemini OCR on one section using the stored page range."""
    section_uuid = UUID(section_id)
    job_uuid = UUID(job_id)

    with SyncSession() as session:
        _update_job(
            session,
            job_uuid,
            status="running",
            started_at=datetime.utcnow(),
            message="Re-extracting section",
            progress=10,
        )

        sec = session.get(Section, section_uuid)
        if sec is None:
            _update_job(
                session,
                job_uuid,
                status="failed",
                error="Section not found",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "section_missing"}

        try:
            book = session.get(Book, sec.book_id)
            if book is None or not book.pdf_url:
                raise RuntimeError("Book or PDF URL missing")

            pdf_bytes = download_pdf(book.pdf_url)

            # Look up next section title AND page_start from schema for
            # Unit 1 — Compute deterministic SliceSpec via the single
            # source-of-truth function. Locates this section in the schema
            # and resolves page_start, page_end, and STOP anchor (title+page)
            # with no silent fallbacks. Failures surface as status='failed'
            # with a clear diagnostic instead of silently extracting
            # wrong content.
            if not book.schema:
                raise RuntimeError("book.schema missing — cannot re-extract")
            book_schema_obj = BookSchema(**book.schema)

            # Find the section in the schema (walks both sections + their
            # children — re-extract may target any depth).
            def _find_section_in_schema(parent, target_id):
                if parent.id == target_id:
                    return parent
                for child in parent.subsections or []:
                    found = _find_section_in_schema(child, target_id)
                    if found is not None:
                        return found
                return None

            target_sec = None
            for top in book_schema_obj.sections or []:
                target_sec = _find_section_in_schema(top, sec.section_id)
                if target_sec is not None:
                    break
            if target_sec is None:
                raise RuntimeError(
                    f"section_id={sec.section_id} not found in book.schema"
                )

            # Compute pdf_total_pages from the actual PDF.
            import pymupdf as _pymupdf
            try:
                _doc = _pymupdf.open(stream=pdf_bytes, filetype="pdf")
                pdf_total_pages = len(_doc)
                _doc.close()
            except Exception:
                pdf_total_pages = book_schema_obj.total_pages or 0

            try:
                slice_spec = compute_extraction_slice(
                    target_sec, book_schema_obj, pdf_total_pages
                )
            except SliceComputationError as e:
                logger.warning(
                    "Re-extract slice failed for section=%s: %s",
                    sec.section_id, e.reason,
                )
                sec.status = "failed"
                sec.qc_local = {
                    "pass": False,
                    "failures": [f"slice_unresolvable: {e.reason}"],
                }
                sec.attempts = (sec.attempts or 0) + 1
                session.commit()
                _update_job(
                    session, job_uuid,
                    status="failed",
                    error=f"slice_unresolvable: {e.reason}",
                    finished_at=datetime.utcnow(),
                )
                return {"ok": False, "section_id": sec.section_id, "error": e.reason}

            if slice_spec.diagnostics:
                logger.info(
                    "Re-extract slice diagnostics section=%s: %s",
                    sec.section_id, "; ".join(slice_spec.diagnostics),
                )

            result: ExtractionResult = asyncio.run(
                re_extract_with_fix(
                    section_id=sec.section_id,
                    title=sec.title,
                    level=sec.level or 1,
                    pdf_bytes=pdf_bytes,
                    slice_spec=slice_spec,
                )
            )

            sec.blocks = result.blocks
            sec.qc_local = result.qc.to_dict()
            sec.attempts = (sec.attempts or 0) + 1
            sec.status = "passed" if result.qc.pass_ else "failed"
            session.commit()

            # Re-link example placeholder chips into parent theory after a
            # single-section re-extract — keeps inline chips in sync.
            try:
                from app.services.example_linker import link_examples_to_theory_sync
                link_examples_to_theory_sync(session, sec.book_id)
            except Exception as e:
                logger.warning("example_linker failed after re_extract (section=%s): %s", section_uuid, e)

            # Re-run figure embedder so anchor matches against the freshly
            # re-extracted theory blocks land in this section. Without
            # this, the figure_references for this section's figures stay
            # stale pointing at the OLD blocks. Best-effort.
            try:
                from app.services.figure_embedder import embed_figures_for_book_sync
                embed_figures_for_book_sync(session, sec.book_id)
            except Exception as e:
                logger.warning(
                    "figure_embedder failed after theory re_extract "
                    "(section=%s): %s", section_uuid, e,
                )

            _update_job(
                session,
                job_uuid,
                status="succeeded",
                progress=100,
                message="Re-extraction complete",
                finished_at=datetime.utcnow(),
            )
            return {"ok": True, "section_id": str(section_uuid), "passed": result.qc.pass_}

        except Exception as e:
            logger.exception("re_extract_section_task failed")
            session.rollback()
            _update_job(
                session,
                job_uuid,
                status="failed",
                error=str(e)[:2000],
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "error": str(e)}


# ── regenerate_book (Sprint 3) ──────────────────────────────────────────

@celery_app.task(name="regenerate_book", bind=True)
def regenerate_book_task(
    self,
    book_id: str,
    job_id: str,
    regeneration_id: str,
    params: dict,
    section_ids: list[str] | None = None,
) -> dict:
    """Run P5 regeneration across sections with invariant split + post-regen QC.

    If ``section_ids`` is None → regenerate every leaf section in the book.
    If ``section_ids`` is provided → regenerate only those sections.
    Container sections (any schema section with non-excluded subsections) are
    always skipped: their content is fully covered by their children, so
    regenerating them would duplicate every paragraph and waste a slow,
    flaky high-token Gemini call.
    """
    book_uuid = UUID(book_id)
    job_uuid = UUID(job_id)
    regen_uuid = UUID(regeneration_id)

    with SyncSession() as session:
        _update_job(
            session,
            job_uuid,
            status="running",
            started_at=datetime.utcnow(),
            message="Loading sections",
            progress=2,
        )

        regen_row = session.get(Regeneration, regen_uuid)
        if regen_row is None:
            _update_job(
                session,
                job_uuid,
                status="failed",
                error=f"Regeneration row {regen_uuid} not found — transaction visibility issue",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "regen_row_missing"}

        all_sections = session.execute(
            select(Section).where(Section.book_id == book_uuid).order_by(Section.section_id)
        ).scalars().all()

        # ─── CONTENT-PRESENCE SCOPE — NEVER MISS A THEORY SECTION ───────────
        # Goal (locked): every extracted theory (Cat B) section gets
        # regenerated. The old logic dropped sections on TWO wrong signals:
        #   • "has children" (container) — but nested parents carry their own
        #     theory (Ch5 §basic-concepts = 37 own blocks + a child → dropped).
        #   • title prefix "Illustration/Example/…" — but Illustrations ARE
        #     Cat B theory and must regenerate.
        #
        # The ONLY correct test is: does the section have ANY extracted block?
        #   • Cat B theory sections (prose, definitions, illustrations, nested
        #     parents with own intro) → have blocks → INCLUDED.
        #   • Cat A questions sections were never theory-extracted → 0 blocks
        #     → naturally excluded (no title heuristic needed).
        #   • Empty structural wrappers (0 blocks, e.g. the bare chapter node)
        #     → nothing to rewrite → excluded.
        # Sections whose blocks are all-invariant (pure figure/equation) are
        # still INCLUDED — the regenerator no-ops them (returns originals, no
        # Gemini call), so they appear in the regen view exactly as extracted.
        # Nothing with content is ever dropped.
        if section_ids is None:
            # "Regen all" — every section that has at least one extracted block.
            sections = [s for s in all_sections if bool(s.blocks)]
            dropped = len(all_sections) - len(sections)
            logger.info(
                "regenerate_book_task: 'all' scope — %d/%d sections have "
                "extracted blocks (skipped %d empty structural wrappers)",
                len(sections), len(all_sections), dropped,
            )
        else:
            # Explicit selection — honor whatever the user picked verbatim.
            wanted = set(section_ids)
            sections = [s for s in all_sections if s.section_id in wanted]
            unknown = wanted - {s.section_id for s in all_sections}
            if unknown:
                logger.warning(
                    "regenerate_book_task: ignoring unknown section_ids: %s",
                    sorted(unknown),
                )

        if not sections:
            _update_job(
                session,
                job_uuid,
                status="failed",
                error="No sections to regenerate (no sections with extracted blocks)",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "no_sections"}

        try:
            # Normalize deprecated tone/language values from older stored regen
            # rows (intern's regen-param overhaul renamed the tone enum +
            # narrowed languages). Covers normal dispatch + startup orphan
            # recovery — without this, old rows 422 against the new schema.
            from app.schemas.regen import normalize_legacy_params

            rp = RegenParams(**normalize_legacy_params(params))
        except Exception as e:
            _update_job(
                session,
                job_uuid,
                status="failed",
                error=f"Invalid params: {e}",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "error": str(e)}

        blocks_by_section: dict[str, list[dict]] = {}
        qc_drift: dict[str, dict] = {}

        # ─── RECAP pre-loop (v3 only, opt-in) ─────────────────────────
        # Detect chapter-end Points-to-Remember / Summary / Key Takeaways
        # sections, extract their bullets, assign each bullet to its best
        # matching topic via deterministic Jaccard match (no extra LLM
        # call, no double-assignment), then SKIP those source sections
        # from the per-section regen output. Orphan bullets get appended
        # at chapter end as a fallback "Key Takeaways" subsection.
        per_section_keypoints: dict[str, list[str]] = {}
        orphan_keypoints: list[str] = []
        ptr_source_section_ids: set[str] = set()
        try:
            from app.services.recap_config import (
                active_redistribute_rules,
                assign_bullets_to_sections,
                detect_redistribute_source_sections,
            )
            # Worker recap pre-loop fires whenever the request opts in via
            # recap_rule_ids. We no longer gate on the prompt-version env
            # var: the live regenerator.txt is the source of truth (the
            # operator can swap it for v3 content to enable recap-aware
            # LLM behavior). If the live prompt happens to be v1, recap
            # rule ids will still be processed by the worker but the LLM
            # will not honor them — harmless, just no recap blocks emitted.
            if rp.recap_rule_ids:
                section_tuples = [
                    (s.section_id, s.title or "", list(s.blocks or []))
                    for s in sections
                ]
                src_ids, bullets = detect_redistribute_source_sections(
                    section_tuples, rp.recap_rule_ids
                )
                ptr_source_section_ids = set(src_ids)
                if bullets:
                    # Only match against sections that are NOT the source.
                    target_pool = [
                        (sid, title, blocks)
                        for sid, title, blocks in section_tuples
                        if sid not in ptr_source_section_ids
                    ]
                    per_section_keypoints, orphan_keypoints = (
                        assign_bullets_to_sections(bullets, target_pool)
                    )
                    logger.info(
                        "recap redistribute: %d bullets from %d source sections; "
                        "assigned to %d sections, %d orphans",
                        len(bullets),
                        len(src_ids),
                        sum(1 for v in per_section_keypoints.values() if v),
                        len(orphan_keypoints),
                    )
        except Exception as e:
            # Recap is best-effort — never block the regen if config fails.
            logger.warning("recap pre-loop skipped: %s", e)

        total = len(sections)

        try:
            for i, sec in enumerate(sections, start=1):
                progress = 5 + int(85 * (i - 1) / max(total, 1))
                _update_job(
                    session,
                    job_uuid,
                    message=f"Regenerating {sec.section_id} ({i}/{total})",
                    progress=progress,
                )
                # Suppress PTR source sections — their content has been
                # redistributed into other sections via per_section_keypoints.
                # Write [] as the SENTINEL so the final-merge layer skips
                # this section entirely instead of falling back to the
                # original Section.blocks (without sentinel the merger
                # would see "no regen for this sid" and serve original).
                if sec.section_id in ptr_source_section_ids:
                    logger.info(
                        "recap: suppressing PTR source section %s (bullets redistributed)",
                        sec.section_id,
                    )
                    blocks_by_section[sec.section_id] = []
                    qc_drift[sec.section_id] = {
                        "pass": True,
                        "drifted": [],
                        "note": "section bullets redistributed via recap",
                    }
                    continue
                original = list(sec.blocks or [])
                if not original:
                    blocks_by_section[sec.section_id] = []
                    qc_drift[sec.section_id] = {"pass": True, "drifted": []}
                    continue
                try:
                    regenerated = asyncio.run(
                        regenerate_section(
                            section_id=sec.section_id,
                            section_title=sec.title,
                            blocks=original,
                            params=rp,
                            assigned_keypoints=per_section_keypoints.get(
                                sec.section_id, []
                            ),
                        )
                    )
                except Exception as e:
                    logger.warning(
                        "regenerate_section failed for %s: %s", sec.section_id, e
                    )
                    # Fall back to originals for this section — guarantees no data loss
                    regenerated = [dict(b) for b in original]

                qc = post_regen_qc(original, regenerated)
                blocks_by_section[sec.section_id] = regenerated
                qc_drift[sec.section_id] = {
                    "pass": qc.pass_,
                    "drifted": qc.drifted_values,
                    "original_number_count": qc.original_number_count,
                }

            # ─── RECAP post-loop: standalone-section RENAME promotion ──
            # Some textbooks extract Konnect / Note / Info Edge / Info Bytes
            # as SIBLING SECTIONS instead of inline callouts. For these,
            # take the regenerated section's content, fold it as a renamed
            # subsection (Fun Fact / Remember / Food for Thought / …) at
            # the END of the preceding topic, and drop the source section.
            # Pure post-processing — no extra LLM call, no prompt directive.
            promote_skip_ids: list[str] = []
            try:
                from app.services.recap_config import active_rename_rules

                active_renames = active_rename_rules(rp.recap_rule_ids or [])
                if active_renames:
                    # Build a lowercase source-label → target-label map.
                    label_to_target: dict[str, str] = {}
                    for r in active_renames:
                        for src in r["source_labels"]:
                            label_to_target[src.lower()] = r["label"]

                    # Document-order sid list + title lookup
                    section_order = [s.section_id for s in sections]
                    sid_to_title = {
                        s.section_id: (s.title or "").strip() for s in sections
                    }

                    for idx, sid in enumerate(section_order):
                        title = sid_to_title.get(sid, "")
                        target_label = label_to_target.get(title.lower())
                        if not target_label:
                            continue
                        # Find preceding "real" topic — skip other promote
                        # candidates and PTR sources.
                        prev_idx = idx - 1
                        while prev_idx >= 0:
                            prev_sid = section_order[prev_idx]
                            prev_title = sid_to_title.get(prev_sid, "")
                            is_rename_source = (
                                prev_title.lower() in label_to_target
                            )
                            is_ptr_source = prev_sid in ptr_source_section_ids
                            if not is_rename_source and not is_ptr_source:
                                break
                            prev_idx -= 1
                        if prev_idx < 0:
                            # No preceding topic — leave as-is (rare; first
                            # section being a Konnect would be unusual).
                            continue
                        target_sid = section_order[prev_idx]

                        # Extract bullets from THIS section's regenerated
                        # blocks. Lists → items; p/kp → bullets verbatim.
                        src_blocks = blocks_by_section.get(sid, []) or []
                        bullets: list[str] = []
                        for b in src_blocks:
                            bt = b.get("t")
                            if bt == "list":
                                for item in (b.get("items") or []):
                                    if item and str(item).strip():
                                        bullets.append(str(item).strip())
                            elif bt in ("p", "kp"):
                                c = (b.get("c") or "").strip()
                                if c:
                                    bullets.append(c)
                        if not bullets:
                            continue

                        # Append renamed subsection to target topic's blocks
                        target_blocks = list(blocks_by_section.get(target_sid, []) or [])
                        target_blocks.append({"t": "h3", "c": target_label})
                        target_blocks.append({"t": "list", "items": bullets})
                        blocks_by_section[target_sid] = target_blocks

                        # Mark source for removal
                        promote_skip_ids.append(sid)
                        logger.info(
                            "recap: promoted standalone %s → %s subsection in %s",
                            sid,
                            target_label,
                            target_sid,
                        )

                # SUPPRESS (not delete) promoted source sections.
                # Writing [] as sentinel — the final-merge layer treats
                # "key present with empty list" as intentionally suppressed
                # and skips the section entirely (no fall-back to
                # Section.blocks original). Without this sentinel,
                # Composer/Preview/DOCX export would still show the source
                # section by falling back to the original extraction.
                for sid in promote_skip_ids:
                    blocks_by_section[sid] = []
                    qc_drift[sid] = {
                        "pass": True,
                        "drifted": [],
                        "note": "section promoted into preceding topic",
                    }
            except Exception as e:
                logger.warning("recap promote post-loop skipped: %s", e)

            # ─── RECAP post-loop: orphan bullet fallback ──────────
            # If any chapter-end bullets did not match any section above
            # the threshold, append them as a synthetic chapter-end
            # "Key Takeaways" section so nothing is silently dropped.
            if orphan_keypoints:
                fallback_blocks = [
                    {"t": "h3", "c": "Key Takeaways"},
                    {"t": "list", "items": list(orphan_keypoints)},
                ]
                # Use a stable synthetic id that sorts to the very end.
                blocks_by_section["zzz-key-takeaways-orphan-fallback"] = fallback_blocks
                qc_drift["zzz-key-takeaways-orphan-fallback"] = {
                    "pass": True,
                    "drifted": [],
                    "note": "synthetic orphan-fallback from recap redistribute",
                }
                logger.info(
                    "recap: appended %d orphan bullets under fallback Key Takeaways section",
                    len(orphan_keypoints),
                )

            if regen_row is not None:
                # MERGE (don't replace) — the regen row was seeded by the
                # API with the prior regen's blocks_by_section, so sections
                # the user previously regenerated keep their saved output.
                # Only the sections in THIS run's scope get overwritten.
                # Without flag_modified, SQLAlchemy doesn't notice the JSON
                # dict mutated in place and skips the UPDATE.
                from sqlalchemy.orm.attributes import flag_modified
                existing_blocks = dict(regen_row.blocks_by_section or {})
                # PTR REDISTRIBUTE + RENAME PROMOTE FIX: when a source
                # section is suppressed in THIS run, also suppress its
                # carried-forward copy from a prior regen. Write [] as
                # the sentinel (same shape as the worker's in-run write)
                # so the final-merge layer can detect explicit
                # suppression and not fall back to Section.blocks
                # originals.
                suppress_ids = set(ptr_source_section_ids) | set(promote_skip_ids)
                # blocks_by_section already contains [] sentinels for these
                # ids from the worker's pre/post-loop. The .update() below
                # will overwrite any prior carried-forward content with
                # those sentinels.
                existing_blocks.update(blocks_by_section)
                regen_row.blocks_by_section = existing_blocks
                flag_modified(regen_row, "blocks_by_section")

                existing_qc = dict(regen_row.qc_drift or {})
                existing_qc.update(qc_drift)
                regen_row.qc_drift = existing_qc
                flag_modified(regen_row, "qc_drift")

                session.commit()

            fail_count = sum(1 for r in qc_drift.values() if not r.get("pass"))
            _update_job(
                session,
                job_uuid,
                status="succeeded",
                progress=100,
                message=(
                    f"Regenerated {total} sections; {fail_count} flagged for drift"
                    if fail_count
                    else f"Regenerated {total} sections — no drift"
                ),
                finished_at=datetime.utcnow(),
            )
            return {
                "ok": True,
                "regeneration_id": str(regen_uuid),
                "total": total,
                "fail_count": fail_count,
            }

        except Exception as e:
            logger.exception("regenerate_book_task failed")
            session.rollback()
            _update_job(
                session,
                job_uuid,
                status="failed",
                error=str(e)[:2000],
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "error": str(e)}


# ── Inline-mode wrappers ────────────────────────────────────────────────
# The Celery-bound functions take `self` as first arg. For inline dispatch
# we need no-self wrappers; both modes share the same underlying logic.

def _analyse_book(book_id: str, job_id: str) -> dict:
    return analyse_book_task(None, book_id, job_id)  # type: ignore[arg-type]


def _extract_book(book_id: str, job_id: str) -> dict:
    return extract_book_task(None, book_id, job_id)  # type: ignore[arg-type]


def _re_extract_section(section_id: str, job_id: str) -> dict:
    return re_extract_section_task(None, section_id, job_id)  # type: ignore[arg-type]


def _regenerate_book(
    book_id: str,
    job_id: str,
    regeneration_id: str,
    params: dict,
    section_ids: list[str] | None = None,
) -> dict:
    return regenerate_book_task(None, book_id, job_id, regeneration_id, params, section_ids)  # type: ignore[arg-type]


register_task("analyse_book", _analyse_book)
register_task("extract_book", _extract_book)
register_task("re_extract_section", _re_extract_section)
register_task("regenerate_book", _regenerate_book)


# ── Figure extraction task ──────────────────────────────────────────────

@celery_app.task(bind=True, name="extract_figures")
def extract_figures_task(self, book_id: str, job_id: str) -> dict:
    from app.core.storage import download_pdf, upload_figure
    from app.models.figure import Figure
    from app.services.figure_extractor import extract_figures_for_section

    book_uuid = UUID(book_id)
    job_uuid = UUID(job_id)

    with SyncSession() as session:
        book = session.get(Book, book_uuid)
        if book is None:
            return {"ok": False, "reason": "book_not_found"}

        _update_job(session, job_uuid, status="running", progress=2, message="Loading PDF")

        try:
            pdf_bytes = download_pdf(book.pdf_url)
        except Exception as exc:
            _update_job(session, job_uuid, status="failed", error=str(exc), finished_at=datetime.utcnow())
            return {"ok": False, "error": str(exc)}

        sections = session.execute(
            select(Section)
            .where(Section.book_id == book_uuid)
            .where(Section.status == "ready")
            .order_by(Section.section_id)
        ).scalars().all()

        if not sections:
            _update_job(session, job_uuid, status="failed", error="No ready sections found", finished_at=datetime.utcnow())
            return {"ok": False, "reason": "no_sections"}

        total = len(sections)
        total_figures = 0

        try:
            for i, sec in enumerate(sections, start=1):
                progress = 5 + int(88 * (i - 1) / max(total, 1))
                _update_job(session, job_uuid, message=f"Extracting figures from {sec.section_id} ({i}/{total})", progress=progress)

                page_start = sec.page_start or 1
                page_end = sec.page_end or page_start

                try:
                    figures = asyncio.run(
                        extract_figures_for_section(
                            pdf_bytes=pdf_bytes,
                            section_id=sec.section_id,
                            page_start=page_start,
                            page_end=page_end,
                        )
                    )
                except Exception as exc:
                    logger.warning("Figure extraction failed for section %s: %s", sec.section_id, exc)
                    continue

                # Pre-load all theory sections once for the resolver (anchor
                # post-pass below). Cheap — same session, same book.
                _all_theory_sections = session.execute(
                    select(Section).where(Section.book_id == book_uuid)
                ).scalars().all()
                from app.services.figure_section_resolver import (
                    resolve_section_by_anchor,
                )

                for fig_data in figures:
                    img_bytes = fig_data.pop("image_bytes", None)

                    # Anchor-text-driven section resolution (post-Gemini,
                    # code-only). Overrides Gemini's visual-proximity guess
                    # when the figure's caption/description appears uniquely
                    # in a different section's blocks. Same logic as v2
                    # figures_tasks; applied here for the legacy v1 worker.
                    # Captions in v1 path are the natural anchor signal.
                    anchor_text = fig_data.get("caption") or fig_data.get("description") or ""
                    gemini_section_ref = fig_data.get("section_id") or sec.section_id
                    gemini_section_uuid = sec.id
                    if anchor_text and not fig_data.get("figure_number"):
                        resolved = resolve_section_by_anchor(
                            anchor_text=anchor_text,
                            page_number=fig_data.get("page_number"),
                            fallback_section_ref=gemini_section_ref,
                            fallback_section_uuid=gemini_section_uuid,
                            theory_sections=list(_all_theory_sections),
                        )
                        if (
                            resolved.resolved_section_ref
                            and resolved.resolved_section_ref != gemini_section_ref
                        ):
                            logger.info(
                                "figure_section_resolver (v1): book=%s anchor=%r "
                                "%r → %r (reason=%s)",
                                book_uuid,
                                anchor_text[:60],
                                gemini_section_ref,
                                resolved.resolved_section_ref,
                                resolved.reason,
                            )
                            gemini_section_ref = resolved.resolved_section_ref
                            gemini_section_uuid = resolved.resolved_section_uuid

                    figure = Figure(
                        book_id=book_uuid,
                        section_id=gemini_section_ref,
                        section_uuid=gemini_section_uuid,
                        figure_number=fig_data.get("figure_number"),
                        caption=fig_data.get("caption"),
                        description=fig_data.get("description"),
                        semantic_type=fig_data.get("semantic_type", "other"),
                        tags=fig_data.get("tags") or [],
                        page_number=fig_data.get("page_number"),
                        bounding_box=fig_data.get("bounding_box"),
                        status="extracted" if img_bytes else "no_image",
                    )
                    session.add(figure)
                    session.flush()  # get the UUID

                    if img_bytes:
                        filename = f"orig_{figure.id}.png"
                        key = upload_figure(img_bytes, str(book_uuid), sec.section_id, filename)
                        figure.image_url = key
                        session.commit()
                    else:
                        session.commit()

                    total_figures += 1

            _update_job(
                session, job_uuid,
                status="succeeded", progress=100,
                message=f"Extracted {total_figures} figures from {total} sections",
                finished_at=datetime.utcnow(),
            )
            return {"ok": True, "total_figures": total_figures}

        except Exception as exc:
            logger.exception("extract_figures_task failed")
            session.rollback()
            _update_job(session, job_uuid, status="failed", error=str(exc)[:2000], finished_at=datetime.utcnow())
            return {"ok": False, "error": str(exc)}


# ── Figure regeneration task ────────────────────────────────────────────

@celery_app.task(bind=True, name="regenerate_figures")
def regenerate_figures_task(self, book_id: str, job_id: str) -> dict:
    from app.core.storage import download_figure, upload_figure
    from app.models.figure import Figure
    from app.models.figure_regeneration import FigureRegeneration
    from app.services.figure_regenerator import REGEN_MODEL, redraw_figure

    book_uuid = UUID(book_id)
    job_uuid = UUID(job_id)

    with SyncSession() as session:
        book = session.get(Book, book_uuid)
        if book is None:
            return {"ok": False, "reason": "book_not_found"}

        figures = session.execute(
            select(Figure)
            .where(Figure.book_id == book_uuid)
            .where(Figure.image_url.isnot(None))
            .order_by(Figure.section_id, Figure.page_number)
        ).scalars().all()

        if not figures:
            _update_job(session, job_uuid, status="failed", error="No figures with images found", finished_at=datetime.utcnow())
            return {"ok": False, "reason": "no_figures"}

        total = len(figures)
        succeeded = 0

        _update_job(session, job_uuid, status="running", progress=2, message=f"Redrawing {total} figures")

        try:
            for i, fig in enumerate(figures, start=1):
                progress = 5 + int(88 * (i - 1) / max(total, 1))
                _update_job(session, job_uuid, message=f"Redrawing figure {i}/{total}", progress=progress)

                try:
                    img_bytes = download_figure(fig.image_url)
                    redrawn = asyncio.run(
                        redraw_figure(
                            image_bytes=img_bytes,
                            figure_number=fig.figure_number,
                            caption=fig.caption,
                            description=fig.description,
                            semantic_type=fig.semantic_type,
                        )
                    )
                except Exception as exc:
                    logger.warning("Redraw failed for figure %s: %s", fig.id, exc)
                    regen = FigureRegeneration(
                        book_id=book_uuid,
                        figure_id=fig.id,
                        section_id=fig.section_id,
                        status="failed",
                        model_used=REGEN_MODEL,
                    )
                    session.add(regen)
                    session.commit()
                    continue

                if redrawn:
                    filename = f"regen_{fig.id}.png"
                    key = upload_figure(redrawn, str(book_uuid), fig.section_id, filename)
                    regen = FigureRegeneration(
                        book_id=book_uuid,
                        figure_id=fig.id,
                        section_id=fig.section_id,
                        image_url=key,
                        model_used=REGEN_MODEL,
                        status="completed",
                    )
                    session.add(regen)
                    session.commit()
                    succeeded += 1

            _update_job(
                session, job_uuid,
                status="succeeded", progress=100,
                message=f"Redrawn {succeeded}/{total} figures",
                finished_at=datetime.utcnow(),
            )
            return {"ok": True, "succeeded": succeeded, "total": total}

        except Exception as exc:
            logger.exception("regenerate_figures_task failed")
            session.rollback()
            _update_job(session, job_uuid, status="failed", error=str(exc)[:2000], finished_at=datetime.utcnow())
            return {"ok": False, "error": str(exc)}


def _extract_figures(book_id: str, job_id: str) -> dict:
    return extract_figures_task(None, book_id, job_id)  # type: ignore[arg-type]


def _regenerate_figures(book_id: str, job_id: str) -> dict:
    return regenerate_figures_task(None, book_id, job_id)  # type: ignore[arg-type]


# ─── DECOMMISSIONED ──────────────────────────────────────────────────────
# The v1 figure worker (extract_figures) is no longer dispatched anywhere.
# All callers (orchestrator's _dispatch_figures, main.py orphan recovery)
# route to extract_figures_v2 (registered in workers/figures_tasks.py).
#
# The v2 worker carries all today's figure improvements: PATH 0
# (placeholder match), PATH A (anchor_text via pylatexenc normalizer),
# PATH B (question_no PRIORITY 1, section_uuid PRIORITY 2), PATH C
# (body_type append), label_pattern.search() canonical matcher,
# diff/upsert into figure_references, section-end fallback, orphan
# section recovery via nearest-section page lookup. Keeping v1
# registered would silently bypass all of that if any caller
# accidentally dispatched it.
#
# The v1 code is kept in this file for archaeology only — function bodies
# remain importable so historical tests can still construct rows, but
# `register_task` is deliberately commented out so the dispatcher cannot
# reach v1 via the runner.
# register_task("extract_figures", _extract_figures)   # DEAD CODE — do not re-enable
register_task("regenerate_figures", _regenerate_figures)
