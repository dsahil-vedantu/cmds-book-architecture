"""QA agent worker — Pillar A + Pillar B.

Task: ``run_qa_fidelity`` — score every Question row in a bank and decide
whether it is faithful to the source PDF.

Two pillars run sequentially:

  Pillar B (deterministic — pure Python):
      Compare each row against pymupdf's text for the same page. Cheap, no
      network. Unreliable on math-heavy PDFs where pymupdf's text layer comes
      out garbled, so its verdict is a pre-signal, not the final word.

  Pillar A (LLM ground truth):
      For every page touched by the bank, ask Gemini to list every question
      printed on that page verbatim. We then 1-to-1 match stored rows against
      that ground truth. Pillar A is authoritative: it overrides Pillar B and
      is the only source of truth for ``missed`` / ``hallucinated`` counts.

Outputs per Question:
    qc_status   passed | flagged | failed
    qc_score    0..1
    qc_tests    {"pillar_b": {...}, "pillar_a": {...}}

One QARun snapshot per section records extracted/missed/hallucinated and the
verbatim tally so we can trend scores across prompt versions.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.storage import download_pdf
from app.models.book import Book
from app.models.job import Job
from app.models.qa_run import QARun
from app.models.question import Question
from app.models.question_bank import QuestionBank
from app.services.qa.fidelity import evaluate_question
from app.services.qa.pdf_text import extract_pages
from app.services.qa.verifier import get_page_ground_truth, match_page
from app.workers.celery_app import celery_app
from app.workers.runner import register as register_task

logger = logging.getLogger(__name__)

_sync_engine = create_engine(
    settings.SYNC_DATABASE_URL,
    pool_pre_ping=True,
    pool_size=3, max_overflow=4, pool_timeout=20, pool_recycle=900,
)
SyncSession = sessionmaker(bind=_sync_engine, class_=Session, autoflush=False)

PROMPT_VERSION = "v1"
MODEL_TAG = "gemini-2.5-flash+deterministic"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _update_job(session: Session, job_id: UUID, **fields) -> None:
    job = session.get(Job, job_id)
    if job is None:
        return
    for k, v in fields.items():
        setattr(job, k, v)
    from datetime import datetime, timezone
    job.last_heartbeat_at = datetime.now(timezone.utc)
    session.commit()


def _page_range(q: Question) -> tuple[int, int]:
    ps = q.page_start or q.excluded_block_page_start or 1
    pe = q.page_end or q.excluded_block_page_end or ps
    if pe < ps:
        pe = ps
    return ps, pe


def _primary_page(q: Question) -> int:
    return q.page_start or q.excluded_block_page_start or 1


def _reconcile_status(
    pillar_a_verdict: str | None,
    pillar_b_status: str,
    pillar_a_ratio: float | None,
    pillar_b_score: float,
) -> tuple[str, float]:
    """Combine the two pillars into a final (qc_status, qc_score).

    Pillar A wins whenever it has an opinion:
        verbatim     -> passed, score = max(pa_ratio, pb_score)
        paraphrased  -> flagged
        not_verbatim -> failed
        hallucinated -> failed (score = 0)
    If Pillar A was unavailable (LLM call failed), fall back to Pillar B.
    """
    if pillar_a_verdict == "verbatim":
        return "passed", max(pillar_a_ratio or 0.0, pillar_b_score, 0.95)
    if pillar_a_verdict == "paraphrased":
        return "flagged", max(pillar_a_ratio or 0.8, 0.8)
    if pillar_a_verdict == "not_verbatim":
        return "failed", min(pillar_a_ratio or 0.0, 0.5)
    if pillar_a_verdict == "hallucinated":
        return "failed", 0.0
    # Pillar A missing — keep Pillar B
    return pillar_b_status, pillar_b_score


# ---------------------------------------------------------------------------
# Main task
# ---------------------------------------------------------------------------
def run_qa_fidelity_task(bank_id: str, job_id: str) -> dict:
    """Full QA pass over a bank — Pillar B then Pillar A, then reconcile."""
    bank_uuid = UUID(bank_id)
    job_uuid = UUID(job_id)

    with SyncSession() as session:
        _update_job(
            session, job_uuid,
            status="running",
            started_at=datetime.utcnow(),
            message="Loading bank + PDF",
            progress=2,
        )

        bank = session.get(QuestionBank, bank_uuid)
        if bank is None:
            _update_job(
                session, job_uuid,
                status="failed",
                error="QuestionBank not found",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "bank_missing"}

        book = session.get(Book, bank.book_id)
        if book is None or not book.pdf_url:
            _update_job(
                session, job_uuid,
                status="failed",
                error="Book or PDF missing",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "pdf_missing"}

        try:
            pdf_bytes = download_pdf(book.pdf_url)
        except Exception as e:
            _update_job(
                session, job_uuid,
                status="failed",
                error=f"PDF download failed: {e}"[:2000],
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "pdf_download_failed"}

        rows: list[Question] = session.execute(
            select(Question)
            .where(Question.bank_id == bank_uuid)
            .order_by(Question.section_ref, Question.page_start, Question.created_at)
        ).scalars().all()

        if not rows:
            _update_job(
                session, job_uuid,
                status="succeeded", progress=100,
                message="No questions to score",
                finished_at=datetime.utcnow(),
            )
            return {"ok": True, "scored": 0}

        # ------------------------------------------------------------------
        # Phase 1 — Pillar B (deterministic) for every row
        # ------------------------------------------------------------------
        _update_job(session, job_uuid, progress=5, message="Pillar B (deterministic)")

        needed_pages = set()
        for q in rows:
            ps, pe = _page_range(q)
            for p in range(ps, pe + 1):
                needed_pages.add(p)
        if needed_pages:
            page_texts = extract_pages(pdf_bytes, min(needed_pages), max(needed_pages))
        else:
            page_texts = {}

        pillar_b: dict[UUID, dict] = {}  # q.id -> {status, score, report_dict, verbatim_status}
        for q in rows:
            ps, pe = _page_range(q)
            page_text = "\n".join(page_texts.get(p, "") for p in range(ps, pe + 1))
            try:
                report = evaluate_question(
                    raw_text=q.raw_text or "",
                    page_text=page_text,
                    has_options=bool(q.has_options),
                    has_solution=bool(q.has_solution),
                    solution_text=q.solution_text,
                )
                pillar_b[q.id] = {
                    "status": report.status,
                    "score": report.score,
                    "verbatim_status": report.verbatim_status,
                    "report": report.to_dict(),
                }
            except Exception as e:
                logger.exception("Pillar B failed for q=%s", q.id)
                pillar_b[q.id] = {
                    "status": "failed",
                    "score": 0.0,
                    "verbatim_status": "not_verbatim",
                    "report": {"error": str(e)[:500]},
                }

        # ------------------------------------------------------------------
        # Phase 2 — Pillar A (LLM ground truth) per page
        # ------------------------------------------------------------------
        # Wipe prior QARun rows for this bank — we keep only the latest per section
        session.execute(QARun.__table__.delete().where(QARun.bank_id == bank_uuid))
        session.commit()

        # Group rows by primary page for per-page LLM calls
        by_page: dict[int, list[Question]] = defaultdict(list)
        for q in rows:
            by_page[_primary_page(q)].append(q)

        pages_sorted = sorted(by_page.keys())
        total_pages = len(pages_sorted)

        pillar_a: dict[UUID, dict] = {}         # q.id -> {verdict, ratio, gt_idx}
        page_missed: dict[int, list[dict]] = {} # page -> list of missed GT entries
        page_errors: dict[int, str] = {}

        for i, page in enumerate(pages_sorted, start=1):
            progress = 10 + int(60 * i / max(total_pages, 1))
            _update_job(
                session, job_uuid,
                progress=min(70, progress),
                message=f"Pillar A — page {page} ({i}/{total_pages})",
            )

            gt = get_page_ground_truth(pdf_bytes, page)
            if gt is None:
                page_errors[page] = "llm_unavailable"
                continue

            stored = by_page[page]
            stored_texts = [q.raw_text or "" for q in stored]
            match = match_page(gt, stored_texts)

            for s_idx, q in enumerate(stored):
                verdict = match["verdicts"].get(s_idx)
                ratio = match["ratios"].get(s_idx, 0.0)
                pillar_a[q.id] = {
                    "verdict": verdict,
                    "ratio": ratio,
                    "page": page,
                }

            page_missed[page] = [gt[g_idx] for g_idx in match["missed"]]

        # ------------------------------------------------------------------
        # Phase 3 — Reconcile & persist per-question verdicts
        # ------------------------------------------------------------------
        _update_job(session, job_uuid, progress=75, message="Reconciling verdicts")

        by_section: dict[str | None, list[Question]] = defaultdict(list)
        for q in rows:
            by_section[q.section_ref].append(q)

        bank_scores: list[float] = []
        bank_tally = {
            "passed": 0, "flagged": 0, "failed": 0,
            "verbatim": 0, "paraphrased": 0, "not_verbatim": 0, "hallucinated": 0,
        }

        for section_ref, qs in by_section.items():
            section_scores: list[float] = []
            section_tally = {
                "verbatim": 0, "paraphrased": 0, "not_verbatim": 0, "hallucinated": 0,
            }
            section_failures: list[dict] = []

            for q in qs:
                pb = pillar_b[q.id]
                pa = pillar_a.get(q.id)  # may be None if page's LLM call failed

                pa_verdict = pa["verdict"] if pa else None
                pa_ratio = pa["ratio"] if pa else None

                final_status, final_score = _reconcile_status(
                    pa_verdict, pb["status"], pa_ratio, pb["score"],
                )

                # Final verbatim_status (for section tally) — prefer Pillar A
                verbatim_status = pa_verdict or pb["verbatim_status"]

                q.qc_status = final_status
                q.qc_score = final_score
                q.qc_tests = {
                    "pillar_b": pb["report"],
                    "pillar_a": (
                        {
                            "verdict": pa_verdict,
                            "ratio": round(pa_ratio or 0.0, 3),
                            "page": pa["page"],
                        } if pa else {"verdict": None, "error": "no_ground_truth"}
                    ),
                    "final": {
                        "status": final_status,
                        "score": round(final_score, 3),
                        "verbatim_status": verbatim_status,
                    },
                }

                section_scores.append(final_score)
                bank_scores.append(final_score)
                section_tally[verbatim_status] = section_tally.get(verbatim_status, 0) + 1
                bank_tally[verbatim_status] = bank_tally.get(verbatim_status, 0) + 1
                bank_tally[final_status] = bank_tally.get(final_status, 0) + 1

                if final_status != "passed":
                    section_failures.append({
                        "question_id": str(q.id),
                        "question_number": q.question_number,
                        "page": _primary_page(q),
                        "status": final_status,
                        "score": round(final_score, 3),
                        "verbatim_status": verbatim_status,
                        "pillar_b_status": pb["verbatim_status"],
                        "pillar_a_verdict": pa_verdict,
                    })

            session.commit()

            # Aggregate per-section Pillar A counts
            section_pages = {_primary_page(q) for q in qs}
            section_missed_items: list[dict] = []
            section_expected = len(qs)  # extracted count; expected = extracted + missed
            for p in section_pages:
                missed_on_page = page_missed.get(p, [])
                # Filter missed to those "belonging" to this section. Since a page
                # can only map to one section here (we grouped by primary page),
                # and pages aren't perfectly disjoint per section, we attribute
                # page-level misses to the section that owns the majority of its
                # rows. Simple rule: if any row on this page is in this section,
                # count this page's misses under this section once.
                section_missed_items.extend(missed_on_page)
            section_expected += len(section_missed_items)

            hallucinated_count = section_tally["hallucinated"]

            # Bank-level missed rollup happens after loop (sum across sections
            # will double-count pages shared between sections — acceptable v1
            # trade-off, typical textbook sections don't share pages).

            # Add missed entries to failures list so the report surfaces them
            for m in section_missed_items:
                section_failures.append({
                    "question_id": None,
                    "question_number": m.get("question_number"),
                    "page": None,
                    "status": "missed",
                    "score": 0.0,
                    "verbatim_status": "missed",
                    "suggested_text": (m.get("raw_text") or "")[:500],
                })

            section_score = (
                sum(section_scores) / len(section_scores)
                if section_scores else 0.0
            )

            run = QARun(
                bank_id=bank_uuid,
                section_ref=section_ref,
                expected_total=section_expected,
                extracted_total=len(qs),
                missed=len(section_missed_items),
                hallucinated=hallucinated_count,
                verbatim_count=section_tally["verbatim"],
                paraphrased_count=section_tally["paraphrased"],
                not_verbatim_count=section_tally["not_verbatim"] + section_tally["hallucinated"],
                score=section_score,
                failures=section_failures or None,
                model=MODEL_TAG,
                prompt_version=PROMPT_VERSION,
            )
            session.add(run)
            session.commit()

        # ------------------------------------------------------------------
        # Finalise job
        # ------------------------------------------------------------------
        bank_score = sum(bank_scores) / len(bank_scores) if bank_scores else 0.0
        total_missed = sum(len(v) for v in page_missed.values())
        total_hallucinated = bank_tally.get("hallucinated", 0)
        page_fail_count = len(page_errors)
        summary = (
            f"Scored {len(rows)} Qs — "
            f"passed={bank_tally.get('passed', 0)} "
            f"flagged={bank_tally.get('flagged', 0)} "
            f"failed={bank_tally.get('failed', 0)} "
            f"missed={total_missed} hallucinated={total_hallucinated} "
            f"score={bank_score:.2%}"
            + (f" [{page_fail_count} pages w/o LLM GT]" if page_fail_count else "")
        )
        _update_job(
            session, job_uuid,
            status="succeeded", progress=100,
            message=summary,
            finished_at=datetime.utcnow(),
        )
        return {
            "ok": True,
            "bank_id": str(bank_uuid),
            "scored": len(rows),
            "bank_score": round(bank_score, 3),
            "tally": bank_tally,
            "missed": total_missed,
            "hallucinated": total_hallucinated,
            "pages_without_gt": page_fail_count,
        }


def _run_qa_fidelity(bank_id: str, job_id: str) -> dict:
    return run_qa_fidelity_task(bank_id, job_id)


# Celery-mode wrapper. Inline path uses _run_qa_fidelity directly.
@celery_app.task(name="run_qa_fidelity", bind=True)
def run_qa_fidelity_celery_task(self, bank_id: str, job_id: str) -> dict:
    return _run_qa_fidelity(bank_id, job_id)


register_task("run_qa_fidelity", _run_qa_fidelity)
