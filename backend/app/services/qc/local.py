"""Local QC engine — 7 checks, zero API cost.

Ported verbatim from 03_QC_LOGIC.md. Any single failure = FAIL.
"""

from __future__ import annotations

import re

from app.schemas.qc import QCResult
from app.services.qc.helpers import (
    extract_equations,
    extract_key_terms,
    extract_numbers,
    paragraphs_to_plain_text,
    split_sentences,
)

_WHITESPACE_RE = re.compile(r"\s+")

# Leading "5.1 Introduction\n" or "1 Foo\n" — a numbered section heading at
# the start of the chunk. We strip this before QC so extraction (which treats
# the heading as an `h3` block, not body text) isn't penalised for "missing"
# the section number or first-sentence start.
_LEADING_HEADER_RE = re.compile(
    r"\A\s*\d+(?:\.\d+){0,2}[\.\):\-\s]+[^\n]{1,120}\n+"
)


def local_qc(chunk_text: str, extracted_paragraphs: list[dict]) -> QCResult:
    """Run all 7 checks. Returns QCResult with pass=True only if ALL pass."""
    failures: list[str] = []
    src_text = (chunk_text or "").strip()
    ext_text = paragraphs_to_plain_text(extracted_paragraphs or []).strip()

    if not src_text:
        return QCResult(pass_=False, score=0.0, failures=["No source text"], stats={})
    if not ext_text:
        return QCResult(pass_=False, score=0.0, failures=["Empty extraction"], stats={})

    # Strip a leading "N Title" heading from src_text so QC doesn't flag the
    # section number as a "missing number" or the heading line as "missing
    # opening content" — extractions correctly treat the heading as metadata.
    header_match = _LEADING_HEADER_RE.match(src_text)
    if header_match:
        src_text = src_text[header_match.end() :].strip()

    src_words = len(src_text.split())
    ext_words = len(ext_text.split())
    word_ratio = ext_words / max(src_words, 1)
    # Normalise whitespace for substring comparisons so content that merely
    # wraps differently (e.g. PDF text extractors inserting \n mid-sentence)
    # doesn't trip truncation / hallucination checks.
    src_lower_flat = _WHITESPACE_RE.sub(" ", src_text).lower()
    ext_lower_flat = _WHITESPACE_RE.sub(" ", ext_text).lower()
    ext_lower = ext_text.lower()
    src_lower = src_text.lower()

    # ── CHECK 1: word count ratio ≥ 70% ────────────────────────────
    if word_ratio < 0.70:
        failures.append(
            f"Word count: source={src_words}, extracted={ext_words} "
            f"({round(word_ratio * 100)}% — need 70%+)"
        )

    # ── CHECK 2: every number must appear exactly ───────────────────
    src_numbers = extract_numbers(src_text)
    missing_numbers = [n for n in src_numbers if n not in ext_text]
    if missing_numbers:
        failures.append(f"Numbers missing: {', '.join(missing_numbers[:5])}")

    # CHECK 2b: ignore bare single-digit section numbers that come from the
    # heading line (e.g. "1", "2") — they're metadata, not content values.

    # ── CHECK 3: every equation must appear (whitespace-normalised) ─
    src_equations = extract_equations(src_text)
    norm_ext = _WHITESPACE_RE.sub(" ", ext_text).lower()
    missing_eqs = [
        eq for eq in src_equations if _WHITESPACE_RE.sub(" ", eq).lower() not in norm_ext
    ]
    if missing_eqs:
        failures.append(f"Equations missing: {' | '.join(missing_eqs[:3])}")

    # ── CHECK 4: key terms must appear ───────────────────────────────
    src_terms = extract_key_terms(src_text)
    missing_terms = [t for t in src_terms if t.lower() not in ext_lower]
    if missing_terms:
        failures.append(f"Key terms missing: {', '.join(missing_terms[:5])}")

    # ── CHECK 5 & 6: last and first sentence present ─────────────────
    src_sentences = split_sentences(src_text)
    if src_sentences:
        last = src_sentences[-1]
        last_chunk = _WHITESPACE_RE.sub(" ", last[:50]).lower()
        if len(last_chunk) > 15 and last_chunk not in ext_lower_flat:
            failures.append("Last sentence missing — content truncated")

        first = src_sentences[0]
        first_chunk = _WHITESPACE_RE.sub(" ", first[:40]).lower()
        if len(first_chunk) > 15 and first_chunk not in ext_lower_flat:
            failures.append("Opening content missing")

    # ── CHECK 7: hallucination — sample sentences trace to source ───
    ext_sentences = split_sentences(ext_text)
    long_sentences = [s for s in ext_sentences if len(s.split()) >= 8]
    sample_step = max(1, len(long_sentences) // 5) if long_sentences else 1
    hallucinated: list[str] = []
    for i in range(0, len(long_sentences), sample_step):
        if len(hallucinated) >= 2:
            break
        sent = long_sentences[i]
        fingerprint = " ".join(sent.split()[:5]).lower().strip()
        if len(fingerprint) > 10 and fingerprint not in src_lower_flat:
            hallucinated.append(sent[:80])
    if hallucinated:
        failures.append(
            f'Possible hallucination — sentence not in source: "{hallucinated[0][:60]}"'
        )

    score = 1.0 if not failures else max(0.0, 1.0 - len(failures) * 0.2)
    return QCResult(
        pass_=(len(failures) == 0),
        score=score,
        word_ratio=word_ratio,
        failures=failures,
        stats={
            "src_words": src_words,
            "ext_words": ext_words,
            "numbers_checked": len(src_numbers),
            "numbers_missing": len(missing_numbers),
            "equations_checked": len(src_equations),
            "equations_missing": len(missing_eqs),
            "terms_checked": len(src_terms),
            "terms_missing": len(missing_terms),
            "hallucination": "clean" if not hallucinated else f"{len(hallucinated)} suspect",
        },
    )
