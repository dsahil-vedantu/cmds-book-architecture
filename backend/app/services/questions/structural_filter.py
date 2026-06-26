"""Post-extraction structural filter — drop only obvious junk.

The Gemini extractor's prompt is already strict (RULE A/B in
question_extractor_v3.txt: never fabricate, must be anchored to a printed
marker). Anything it emits as a question has already passed that bar.

So this filter is intentionally permissive: it does NOT try to re-validate
"is this a question" via heuristics — those heuristics produced false
rejections on legitimate worked examples (e.g. "In a Coolidge tube, …
Find the minimum wavelength" — verb at word 17, missed by an early-window
check). Instead we only drop:

    - empty / single-fragment items (length < MIN_RAW_TEXT_LEN)
    - items whose body literally contains a known callout phrase
      ("Did You Know", "Answer Key", crossword markers, …)

Anything else is kept. If a downstream user wants to drop more (legitimate
human review), they do that in the UI with a Reject button — not here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Minimum length below which "questions" are almost always headings, page
# numbers, or fragments. "1." or "Q.3" alone is not a question.
MIN_RAW_TEXT_LEN = 15

# Items that pass through the LLM but are almost never real questions.
_EXCLUDE_PHRASES = (
    "did you know",
    "fun fact",
    "remember box",
    "learning objective",
    "after this section",
    "vocabulary list",
    "glossary",
    "answer key",
    "answers to exercises",
    # Crossword / puzzle indicators that the prompt should have excluded but
    # might slip through if the LLM ignores its instructions.
    "across:",
    "down:",
    "1 across",
    "1 down",
)

# Numbered-question prefixes the OCR commonly emits.
_NUMBER_PREFIX = re.compile(
    r"""^\s*
        (?:
            q[\s\.]*\d              # Q.3, Q 3, Q3
          | question\s+\d           # Question 5
          | exercise\s+\d           # Exercise 8.2
          | example\s+\d            # Example 4
          | problem\s+\d            # Problem 3
          | \d+\s*[\.\):]           # 1.  1)  1:
          | \(\s*[a-z0-9]+\s*\)     # (a) (1) (iii)
          | [a-z]\s*[\.\):]         # a.  a)
        )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Imperative verbs that strongly indicate a question/prompt.
_IMPERATIVE_VERB = re.compile(
    r"\b(find|calculate|show|prove|determine|evaluate|solve|state|"
    r"explain|describe|derive|compute|estimate|deduce|verify|"
    r"identify|match|name|define|list|sketch|draw|plot|"
    r"choose|select|which|what|why|how|where|when|who|whom|"
    r"is|are|do|does|did|can|could|would|will)\b",
    re.IGNORECASE,
)


@dataclass
class FilterResult:
    kept: list[dict[str, Any]]
    rejected: list[dict[str, Any]]  # original item + "_reject_reason"


def _looks_like_question(raw_text: str) -> tuple[bool, str]:
    """Return (keep, reason_if_dropped).

    Permissive — trusts the upstream Gemini prompt's anti-fabrication guards.
    Drops ONLY:
      - too short to be a real question stem
      - body literally contains a known non-question callout phrase
    Everything else is kept; if it's noise, a human can reject it in the UI.
    """
    text = (raw_text or "").strip()
    if len(text) < MIN_RAW_TEXT_LEN:
        return False, f"too short ({len(text)} chars, need ≥{MIN_RAW_TEXT_LEN})"

    lower = text.lower()
    for phrase in _EXCLUDE_PHRASES:
        if phrase in lower:
            return False, f"contains excluded phrase: '{phrase}'"

    return True, ""


def filter_items(items: list[dict[str, Any]]) -> FilterResult:
    """Split LLM-emitted items into kept vs rejected with reasons.

    The contract: kept items keep their original shape; rejected items get an
    extra ``_reject_reason`` key so the UI can explain the drop.
    """
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in items:
        raw = (item or {}).get("raw_text", "")
        ok, reason = _looks_like_question(raw)
        if ok:
            kept.append(item)
        else:
            rejected.append({**item, "_reject_reason": reason})
    return FilterResult(kept=kept, rejected=rejected)
