"""Normalize hallucinated content_types into the validator's vocabulary.

Background
----------
The schema validator (Schema Week 2) allows only three content_type values:
``{"theory", "questions", "figures"}``. Gemini, however, regularly invents
adjacent values that don't match — singular forms, aliases, or new categories
it observes in real-world books. Running schema generation on 38 distinct
books surfaced 10 hallucinated values across 200+ section instances:

    'question'              (singular)
    'table' / 'tables'      (not in vocabulary)
    'solved_examples', 'worked_examples', 'examples'
    'summary', 'activity', 'source_documents',
    'biography', 'fun_fact', 'learning_objectives'

Each was real text Gemini saw in a section header. Without normalization the
validator rejects the schema and the corrective-retry path either patches it
inconsistently or burns retry budget. With normalization the validator sees
only the three canonical values and accepts on first pass.

Policy decisions baked in
-------------------------
- Aliases and typos collapse to their canonical name (singular → plural,
  alias → primary).
- "Examples" sections (solved/worked/plain) are treated as Cat A
  ``questions`` — this matches existing pipeline behavior where worked
  examples in textbooks are routed through the questions extractor.
- Tables become ``theory`` — they're embedded content, not a separate flow.
- Real-but-unknown categories (summary, activity, biography, fun_fact,
  learning_objectives, source_documents) all become ``theory``. User
  policy: "excluded is reserved for chapter-end question banks / hints /
  answers — boilerplate sidebar content is real content."
- Anything not explicitly mapped also defaults to ``theory`` (safe — the
  worst case is the theory extractor processes a section that turns out
  to be uninteresting, whereas dropping a section silently loses content).

This normalization is IDEMPOTENT — running it twice yields the same
result, because canonical values map to themselves.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# Canonical vocabulary the validator accepts.
_CANONICAL = frozenset({"theory", "questions", "figures"})

# Explicit mapping of every value we've seen Gemini produce that isn't
# canonical. The default for anything not in this map is "theory".
_ALIAS_MAP: dict[str, str] = {
    # Pluralization
    "question": "questions",
    "table": "theory",            # tables are embedded theory content
    "tables": "theory",
    # Example aliases — examples in textbooks are worked questions
    "example": "questions",
    "examples": "questions",
    "solved_examples": "questions",
    "worked_examples": "questions",
    "solved_example": "questions",
    "worked_example": "questions",
    # Real-but-unknown categories — all real content, treat as theory
    "summary": "theory",
    "activity": "theory",
    "activities": "theory",
    "source_documents": "theory",
    "source_document": "theory",
    "biography": "theory",
    "biographies": "theory",
    "fun_fact": "theory",
    "fun_facts": "theory",
    "learning_objectives": "theory",
    "learning_objective": "theory",
}


def _normalize_one(value: Any) -> str | None:
    """Normalize a single content_type value.

    Returns the canonical value, or None if the input wasn't a string we
    can normalize (caller should drop None values from the resulting list).
    """
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    if not v:
        return None
    if v in _CANONICAL:
        return v
    if v in _ALIAS_MAP:
        return _ALIAS_MAP[v]
    # Unknown value — default to theory (per policy: extract by default).
    logger.info(
        "schema_content_type_normalizer: unknown content_type %r → defaulting to 'theory'",
        value,
    )
    return "theory"


def _normalize_list(values: Any) -> list[str]:
    """Normalize a content_types list. Deduplicates while preserving order."""
    if values is None:
        return []
    if not isinstance(values, list):
        # Defensive: wrap a scalar in a list so we don't crash on bad input.
        values = [values]
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        n = _normalize_one(v)
        if n is None:
            continue
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def normalize_schema_content_types(data: dict[str, Any]) -> dict[str, Any]:
    """Walk the schema dict and normalize content_types in every section.

    Mutates ``data`` in-place AND returns it (callers can use either style).
    Idempotent — running twice yields the same result.

    Counts the number of replacements made and logs once per schema for
    observability without spamming.
    """
    if not isinstance(data, dict):
        return data

    replacements = 0

    def walk(nodes: Any) -> None:
        nonlocal replacements
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            ct_in = node.get("content_types")
            if ct_in is not None:
                ct_out = _normalize_list(ct_in)
                if ct_out != ct_in:
                    replacements += 1
                # Always write — even when the list is identical, the
                # normalized form may have been a no-op coincidence.
                node["content_types"] = ct_out
            subs = node.get("subsections")
            if subs:
                walk(subs)

    walk(data.get("sections"))

    if replacements:
        logger.info(
            "schema_content_type_normalizer: normalized content_types in %d section(s)",
            replacements,
        )
    return data
