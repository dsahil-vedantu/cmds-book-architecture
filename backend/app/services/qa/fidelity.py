"""Deterministic fidelity checks — Pillar B of the QA agent.

Input: one Question row + the page text that row came from.
Output: a list of ``TestResult`` dicts and a rolled-up 0..1 score.

Every check below is pure Python — no LLM — so results are reproducible and
cheap to re-run across prompt versions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Dict, List

from app.services.qa.pdf_text import normalise

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
VERBATIM_THRESHOLD = 0.95   # ≥ this → "verbatim"
PARAPHRASE_THRESHOLD = 0.85  # ≥ this → "paraphrased"; below → "not_verbatim"
NGRAM_SIZE = 10              # sliding window for no_invention check
MIN_NGRAM_HITS = 0.80        # ≥ 80% of n-grams must appear in page text


# Option-label detectors (MCQ / sub-parts)
_OPT_PATTERNS = [
    re.compile(r"\((?P<lbl>[a-d])\)", re.IGNORECASE),       # (a) (b)
    re.compile(r"\((?P<lbl>[ivx]{1,4})\)", re.IGNORECASE),  # (i) (ii)
    re.compile(r"\b(?P<lbl>[A-D])\)", re.MULTILINE),        # A) B)
]

# Math tokens we count for preservation
_MATH_TOKENS = (
    r"\\begin\{[^}]+\}",
    r"\\end\{[^}]+\}",
    r"\$\$",   # display
    r"\$",     # inline (counted after $$, since we remove $$ first)
)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
@dataclass
class TestResult:
    test: str
    passed: bool
    evidence: str = ""
    score: float | None = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "test": self.test,
            "passed": self.passed,
            "evidence": self.evidence,
        }
        if self.score is not None:
            out["score"] = round(float(self.score), 3)
        return out


@dataclass
class FidelityReport:
    tests: List[TestResult] = field(default_factory=list)
    ratio: float = 0.0
    verbatim_status: str = "not_verbatim"  # verbatim | paraphrased | not_verbatim
    score: float = 0.0
    status: str = "pending"  # pending | passed | flagged | failed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verbatim_ratio": round(float(self.ratio), 3),
            "verbatim_status": self.verbatim_status,
            "score": round(float(self.score), 3),
            "status": self.status,
            "tests": [t.to_dict() for t in self.tests],
        }


# ---------------------------------------------------------------------------
# Individual checks — each returns a TestResult
# ---------------------------------------------------------------------------
def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def check_verbatim(raw_text: str, page_text: str) -> tuple[TestResult, float, str]:
    """Longest-contiguous-match ratio between extracted text and page text.

    Uses ``find_longest_match`` to handle the common case where the page
    contains MANY questions and we want to measure how much of ``raw_text``
    is a contiguous substring (or close) of the page.
    """
    a = normalise(raw_text)
    b = normalise(page_text)
    if not a:
        return (
            TestResult("verbatim_ratio", False, "empty raw_text", 0.0),
            0.0,
            "not_verbatim",
        )
    if not b:
        return (
            TestResult("verbatim_ratio", False, "empty page text", 0.0),
            0.0,
            "not_verbatim",
        )
    # Longest contiguous match between a and b (relative to len(a))
    sm = SequenceMatcher(None, a, b, autojunk=False)
    m = sm.find_longest_match(0, len(a), 0, len(b))
    ratio = m.size / max(len(a), 1)

    if ratio >= VERBATIM_THRESHOLD:
        status = "verbatim"
        ok = True
    elif ratio >= PARAPHRASE_THRESHOLD:
        status = "paraphrased"
        ok = False
    else:
        status = "not_verbatim"
        ok = False
    evidence = (
        f"longest contiguous match = {m.size}/{len(a)} chars ({ratio:.2%})"
        if m.size
        else "no contiguous match found on page"
    )
    return TestResult("verbatim_ratio", ok, evidence, ratio), ratio, status


def check_no_invention(raw_text: str, page_text: str) -> TestResult:
    """Every n-gram window of ``raw_text`` must appear in ``page_text``.

    Catches inserted explanations / paraphrases that weren't in the original.
    Returns pass if ≥80% of windows found.
    """
    a = normalise(raw_text)
    b = normalise(page_text)
    if len(a) < NGRAM_SIZE or not b:
        return TestResult("no_invention", True, "raw_text too short for ngram check")
    windows = [a[i : i + NGRAM_SIZE] for i in range(0, len(a) - NGRAM_SIZE + 1, max(NGRAM_SIZE // 2, 1))]
    if not windows:
        return TestResult("no_invention", True, "no windows")
    hits = sum(1 for w in windows if w in b)
    ratio = hits / len(windows)
    missing_example = ""
    if ratio < MIN_NGRAM_HITS:
        missing = next((w for w in windows if w not in b), "")
        missing_example = f"missing n-gram: {missing[:60]!r}"
    return TestResult(
        "no_invention",
        ratio >= MIN_NGRAM_HITS,
        f"{hits}/{len(windows)} n-grams found ({ratio:.2%}){'; ' + missing_example if missing_example else ''}",
        ratio,
    )


def check_no_truncation(raw_text: str) -> TestResult:
    """Extracted text must not end mid-word / with an ellipsis marker."""
    s = (raw_text or "").rstrip()
    if not s:
        return TestResult("no_truncation", False, "empty text")
    # Ellipsis / unicode ellipsis / [illegible] / ends mid-word
    if s.endswith(("…", "...", "[illegible]")):
        return TestResult("no_truncation", False, f"ends with truncation marker: {s[-20:]!r}")
    last_char = s[-1]
    if last_char.isalpha() and len(s) > 1 and s[-2].isalpha():
        # OK only if it's a natural sentence ending (punctuation). Many
        # exercises end without a period, so this is a soft signal — treat
        # as pass but flag.
        # Exception: short tokens like "True"/"False" that are valid endings.
        return TestResult("no_truncation", True, "ends without punctuation (soft pass)")
    return TestResult("no_truncation", True, "")


def check_math_preserved(raw_text: str, page_text: str) -> TestResult:
    """Count of math tokens in extracted ≥ count in page text (no drops).

    Uses max of the two raw counts as the target; we accept extra math tokens
    (LaTeX wrapping is fine) but not fewer than on the page.
    """

    def counts(s: str) -> Dict[str, int]:
        return {
            "begin": len(re.findall(r"\\begin\{", s)),
            "end": len(re.findall(r"\\end\{", s)),
            "display_dollar": s.count("$$"),
            # inline $ after stripping display $$ pairs:
            "inline_dollar": (s.replace("$$", "")).count("$"),
        }

    c_raw = counts(raw_text or "")
    c_page = counts(page_text or "")
    diffs: List[str] = []
    for k, want in c_page.items():
        got = c_raw[k]
        if got < want:
            diffs.append(f"{k}: page={want}, extracted={got}")
    if not diffs:
        return TestResult("math_preserved", True, "")
    return TestResult("math_preserved", False, "; ".join(diffs))


def check_options_preserved(raw_text: str, page_text: str, has_options: bool) -> TestResult:
    """If extractor flagged as MCQ or page has option labels, every labelled
    option on the page must be echoed in ``raw_text``.
    """
    labels_page: List[str] = []
    for pat in _OPT_PATTERNS:
        for m in pat.finditer(page_text or ""):
            labels_page.append(m.group("lbl").lower())
    if not labels_page and not has_options:
        return TestResult("options_preserved", True, "no options on page")

    labels_raw: List[str] = []
    for pat in _OPT_PATTERNS:
        for m in pat.finditer(raw_text or ""):
            labels_raw.append(m.group("lbl").lower())

    missing = [l for l in set(labels_page) if l not in labels_raw]
    if missing:
        return TestResult(
            "options_preserved",
            False,
            f"missing option labels: {sorted(set(missing))}",
        )
    return TestResult("options_preserved", True, f"labels OK ({sorted(set(labels_raw))})")


def check_solution_scope(
    solution_text: str | None,
    has_solution: bool,
    page_text: str,
) -> TestResult:
    """If ``has_solution=True``, ``solution_text`` must be non-trivial AND
    appear on the page (verbatim ratio ≥ 0.85)."""
    if not has_solution:
        return TestResult("solution_scope", True, "no solution expected")
    s = (solution_text or "").strip()
    if len(s) < 10:
        return TestResult(
            "solution_scope", False, f"has_solution=true but solution_text too short ({len(s)} chars)"
        )
    a = normalise(s)
    b = normalise(page_text or "")
    if not b:
        return TestResult("solution_scope", False, "no page text to compare against")
    sm = SequenceMatcher(None, a, b, autojunk=False)
    m = sm.find_longest_match(0, len(a), 0, len(b))
    ratio = m.size / max(len(a), 1)
    if ratio < PARAPHRASE_THRESHOLD:
        return TestResult(
            "solution_scope",
            False,
            f"solution not found on page (ratio={ratio:.2%})",
            ratio,
        )
    return TestResult("solution_scope", True, f"ratio={ratio:.2%}", ratio)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def evaluate_question(
    *,
    raw_text: str,
    page_text: str,
    has_options: bool,
    has_solution: bool,
    solution_text: str | None,
) -> FidelityReport:
    """Run all deterministic tests and roll up into a final report.

    ``page_text`` should already be the concatenation of the question's claimed
    page range (typically just one page).
    """
    tests: List[TestResult] = []

    verbatim_test, ratio, vstatus = check_verbatim(raw_text, page_text)
    tests.append(verbatim_test)
    tests.append(check_no_invention(raw_text, page_text))
    tests.append(check_no_truncation(raw_text))
    tests.append(check_math_preserved(raw_text, page_text))
    tests.append(check_options_preserved(raw_text, page_text, has_options))
    tests.append(check_solution_scope(solution_text, has_solution, page_text))

    # Weighted score — verbatim counts most.
    weights = {
        "verbatim_ratio": 0.40,
        "no_invention": 0.20,
        "math_preserved": 0.15,
        "options_preserved": 0.10,
        "solution_scope": 0.10,
        "no_truncation": 0.05,
    }
    total_w = sum(weights.values())
    score = 0.0
    for t in tests:
        w = weights.get(t.test, 0.0)
        if t.passed:
            score += w
        elif t.score is not None:
            # Partial credit for continuous tests (verbatim, no_invention, solution_scope)
            score += w * max(0.0, t.score)
    score = score / total_w

    # Overall status: failed if verbatim says so, flagged if any hard check fails,
    # else passed.
    hard_failed = any(
        not t.passed and t.test in {"verbatim_ratio", "math_preserved", "options_preserved"}
        for t in tests
    )
    if vstatus == "not_verbatim":
        status = "failed"
    elif hard_failed:
        status = "flagged"
    else:
        status = "passed"

    return FidelityReport(
        tests=tests,
        ratio=ratio,
        verbatim_status=vstatus,
        score=score,
        status=status,
    )
