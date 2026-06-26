"""Pillar A — LLM-based per-page ground truth + diff against stored questions.

For each page of the bank, we ask Gemini to list every question printed on
that page (the ``qa_verifier`` prompt). Then we 1-to-1 match stored Question
rows against that ground-truth list and emit:

    - matches: (stored_idx, gt_idx, similarity) pairs at threshold
    - missed:  gt indices with no match — extractor missed these
    - hallucinated: stored indices with no match — extractor invented these
    - verdicts: {stored_idx: "verbatim"|"paraphrased"|"not_verbatim"|"hallucinated"}

This is the primary fidelity signal on math-heavy PDFs where pymupdf's text
layer is unreliable. It is also the only completeness signal — pymupdf cannot
tell us what's "missing".
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import Any, Dict, List, Tuple

from app.services.prompt_loader import load_raw
from app.services.qa.pdf_text import normalise
from app.utils.json_parse import parse_json

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.5-flash"
# Match thresholds use longest-contiguous-substring ratio (substring-of-shorter),
# not global SequenceMatcher.ratio(). The substring metric is far more forgiving
# of leading/trailing context drift (e.g. "Q.3" vs "Question 3:") which is the
# dominant source of false-hallucinations on math-heavy PDFs.
MATCH_THRESHOLD = 0.60       # below this → not a match (will be hallucinated)
PARAPHRASE_THRESHOLD = 0.80  # 0.60-0.79 → flagged (paraphrased), 0.80+ → match
VERBATIM_THRESHOLD = 0.95    # 0.95+ → verbatim
MAX_OUTPUT_TOKENS = 32000


# ---------------------------------------------------------------------------
# Gemini plumbing (delegates to app.core.gemini_runtime for real timeouts)
# ---------------------------------------------------------------------------
def _slice_pdf_page(pdf_bytes: bytes, page: int) -> bytes:
    """Return a one-page PDF. Page is 1-indexed."""
    import pymupdf

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        total = len(doc)
        p0 = max(0, min(total - 1, (page or 1) - 1))
        out = pymupdf.open()
        try:
            out.insert_pdf(doc, from_page=p0, to_page=p0)
            return out.tobytes()
        finally:
            out.close()
    finally:
        doc.close()


def _call_gemini(pdf_slice: bytes, system_prompt: str, user_prompt: str) -> str:
    """Real socket-timeout Gemini call (centralised in app.core.gemini_runtime)."""
    from app.core.gemini_runtime import call_gemini_with_pdf

    return call_gemini_with_pdf(
        pdf_bytes=pdf_slice,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=GEMINI_MODEL,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.0,
        display_name="qa-page.pdf",
    )


# ---------------------------------------------------------------------------
# Ground-truth retrieval
# ---------------------------------------------------------------------------
def get_page_ground_truth(pdf_bytes: bytes, page: int) -> List[Dict[str, Any]] | None:
    """Ask Gemini to list every question on ``page``. Returns None on hard failure.

    Each entry is ``{raw_text, question_number, sub_parts_count,
    continues_from_previous_page, continues_to_next_page}``.
    """
    try:
        system_prompt = load_raw("qa_verifier")
    except FileNotFoundError:
        logger.error("qa_verifier prompt missing")
        return None

    try:
        pdf_slice = _slice_pdf_page(pdf_bytes, page)
    except Exception as e:
        logger.warning("page %s slice failed: %s", page, e)
        return None

    user_prompt = (
        f"List every question printed on this single PDF page (page {page} of the "
        "source book). Return strict JSON matching the format in the system prompt."
    )
    try:
        raw = _call_gemini(pdf_slice, system_prompt, user_prompt)
        data = parse_json(raw)
        qs = list(data.get("questions") or [])
        # Normalise
        out = []
        for q in qs:
            if not isinstance(q, dict):
                continue
            text = (q.get("raw_text") or "").strip()
            if len(text) < 5:
                continue
            out.append({
                "raw_text": text,
                "question_number": str(q.get("question_number") or "").strip() or None,
                "sub_parts_count": int(q.get("sub_parts_count") or 0) if str(q.get("sub_parts_count") or "").lstrip("-").isdigit() else 0,
                "continues_from_previous_page": bool(q.get("continues_from_previous_page")),
                "continues_to_next_page": bool(q.get("continues_to_next_page")),
            })
        return out
    except Exception as e:
        logger.warning("qa_verifier page %s failed: %s", page, e)
        return None


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def _similarity(a: str, b: str) -> float:
    """Longest-contiguous-substring ratio relative to the shorter string.

    Returns 1.0 when one string contains the other verbatim. Far more
    tolerant of "Q.3" vs "Question 3:" style prefix drift than global
    SequenceMatcher.ratio(), which was producing 30+ false hallucinations
    per bank on math-heavy PDFs.
    """
    if not a or not b:
        return 0.0
    na = normalise(a)
    nb = normalise(b)
    if not na or not nb:
        return 0.0
    sm = SequenceMatcher(None, na, nb, autojunk=False)
    m = sm.find_longest_match(0, len(na), 0, len(nb))
    denom = min(len(na), len(nb)) or 1
    return m.size / denom


def _classify(ratio: float) -> str:
    if ratio >= VERBATIM_THRESHOLD:
        return "verbatim"
    if ratio >= PARAPHRASE_THRESHOLD:
        return "paraphrased"
    return "not_verbatim"


def match_page(
    gt_questions: List[Dict[str, Any]],
    stored_texts: List[str],
) -> Dict[str, Any]:
    """Greedy 1-to-1 matching between stored rows and GT questions.

    Returns a dict with:
        matches:       [(stored_idx, gt_idx, ratio)]
        missed:        [gt_idx, ...]      — GT entries with no stored counterpart
        hallucinated:  [stored_idx, ...]  — stored rows with no GT counterpart
        verdicts:      {stored_idx: verdict}
        ratios:        {stored_idx: float}
    """
    gt_texts = [g.get("raw_text", "") for g in gt_questions]
    # Score all pairs above the match threshold
    scored: List[Tuple[float, int, int]] = []
    for s_idx, s in enumerate(stored_texts):
        for g_idx, g in enumerate(gt_texts):
            r = _similarity(s, g)
            if r >= MATCH_THRESHOLD:
                scored.append((r, s_idx, g_idx))
    scored.sort(reverse=True)

    used_s: set[int] = set()
    used_g: set[int] = set()
    matches: List[Tuple[int, int, float]] = []
    for r, s_idx, g_idx in scored:
        if s_idx in used_s or g_idx in used_g:
            continue
        used_s.add(s_idx)
        used_g.add(g_idx)
        matches.append((s_idx, g_idx, r))

    missed = [i for i in range(len(gt_texts)) if i not in used_g]
    hallucinated = [i for i in range(len(stored_texts)) if i not in used_s]

    verdicts: Dict[int, str] = {}
    ratios: Dict[int, float] = {}
    for s_idx, g_idx, r in matches:
        verdicts[s_idx] = _classify(r)
        ratios[s_idx] = r
    for s_idx in hallucinated:
        verdicts[s_idx] = "hallucinated"
        ratios[s_idx] = 0.0

    return {
        "matches": matches,
        "missed": missed,
        "hallucinated": hallucinated,
        "verdicts": verdicts,
        "ratios": ratios,
    }
