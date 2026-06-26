"""Regeneration parameter schema + human-readable maps used in P5 prompt."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RegenParams(BaseModel):
    # Defaulting to "heavy" as requested
    intensity: Literal["light", "moderate", "heavy"] = "heavy"
    # All three modes are now calibrated around the "academic" register
    tone: Literal["academic_rigorous", "academic_pedagogical", "academic_interactive"] = "academic_pedagogical"
    equations_handling: Literal["preserve", "explain"] = "preserve"
    diagrams_handling: Literal["preserve", "describe"] = "preserve"
    analogies: Literal["none", "add_one", "add_multiple"] = "none"
    structure: Literal["identical", "reorganize"] = "identical"
    # Restricted to English ("en") and Hindi ("hi")
    language: Literal["en", "hi"] = "en"
    target_audience: str | None = None
    custom_instructions: str | None = None
    recap_rule_ids: list[str] = Field(default_factory=list)


INTENSITY_MAP: dict[str, str] = {
    "light": (
        "LIGHT — Minimum 30-40% surface change. Swap basic vocabulary and restructure "
        "minor clauses while preserving core sentence flows. Must meet plagiarism-avoidance floors."
    ),
    "moderate": (
        "MODERATE — 40-60% structural change. Sentence paths, passive/active voice, and transition "
        "structures are completely rewritten. Fact-retention remains absolute, but syntax is novel."
    ),
    "heavy": (
        "HEAVY (Default) — 70-90% change. Complete re-architecting of sentence structures. "
        "No original sentence rhythm remains. Highly effective for preventing plagiarism, using entirely "
        "independent academic prose while keeping facts, formulas, and laws perfectly aligned."
    ),
}

# 3 Academic-centric tone variations replacing the legacy modes
TONE_MAP: dict[str, str] = {
    "academic_rigorous": (
        "ACADEMIC RIGOROUS — Highly formal, precise, and authoritative third-person register. "
        "Uses precise technical nomenclature, passive voice where customary, and a dense, standard "
        "scientific layout ideal for competitive exam preparation."
    ),
    "academic_pedagogical": (
        "ACADEMIC PEDAGOGICAL (Default) — Balanced academic tone optimized for comprehension. "
        "Maintains complete technical correctness and formal terminology, but delivers explanations "
        "with optimal structural transitions to help students build clear mental models."
    ),
    "academic_interactive": (
        "ACADEMIC INTERACTIVE — A formal academic register that strategically introduces "
        "professional active voice, directed transitions, and rhetorical anchoring to sustain engagement "
        "without sacrificing any technical accuracy or exam-level precision."
    ),
}

EQ_HANDLING_MAP: dict[str, str] = {
    "preserve": "keep equations character-for-character identical",
    "explain": "keep equations identical; add one brief explanation line above",
}

DIAG_HANDLING_MAP: dict[str, str] = {
    "preserve": "keep figure captions exactly as given",
    "describe": "keep figure captions exactly; may add one-sentence description",
}

ANALOGY_MAP: dict[str, str] = {
    "none": "do not add analogies",
    "add_one": "add at most one analogy per section if it improves clarity",
    "add_multiple": "add analogies liberally where they help understanding",
}

STRUCTURE_MAP: dict[str, str] = {
    "identical": (
        "IDENTICAL — preserve heading hierarchy and section ordering. "
        "EXCEPT for the B2 mandated renames (source 'Examples' / "
        "'Worked Examples' / 'Sample Problems' / 'Solved Examples' → "
        "'Let's Solve'; source 'Concepts' → 'Concepts'). All other "
        "headings stay verbatim."
    ),
    "reorganize": "minor reorganization allowed for better pedagogical flow",
}

# Narrowed down to English and Hindi only as requested
LANGUAGE_MAP: dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
}


# Legacy → current value maps. The tone enum was renamed to three academic
# variants and language was narrowed to en/hi; Regeneration rows created
# before that change still carry the previous values in their params JSON.
# These maps keep such rows re-runnable instead of failing validation.
_LEGACY_TONE_MAP: dict[str, str] = {
    "academic": "academic_rigorous",
    "conversational": "academic_interactive",
    "simplified": "academic_pedagogical",
}
_CURRENT_LANGS = set(LANGUAGE_MAP)


def normalize_legacy_params(params: dict) -> dict:
    """Map deprecated tone/language values to current schema-valid ones.

    Returns a new dict; does not mutate the input. Current-value inputs and
    unknown keys pass through unchanged. Applied wherever a stored params
    blob is rehydrated into RegenParams (per-section rerun + worker execution,
    which also covers startup orphan recovery).
    """
    out = dict(params)
    tone = out.get("tone")
    if tone in _LEGACY_TONE_MAP:
        out["tone"] = _LEGACY_TONE_MAP[tone]
    lang = out.get("language")
    if lang is not None and lang not in _CURRENT_LANGS:
        out["language"] = "en"
    return out


def param_descriptors(
    params: RegenParams,
    assigned_keypoints: list[str] | None = None,
) -> dict[str, str]:
    lang_name = LANGUAGE_MAP.get(params.language, "English")
    audience_line = (
        f"TARGET AUDIENCE: Write specifically for {params.target_audience}. "
        f"Calibrate vocabulary, examples, and depth accordingly."
        if params.target_audience
        else ""
    )
    custom_line = (
        f"{params.custom_instructions}"
        if params.custom_instructions
        else ""
    )
    extra = "\n\n".join(filter(None, [audience_line, custom_line]))

    from app.services.recap_config import (
        render_active_ids,
        render_keypoints_directive,
        render_renames_directive,
    )

    recap_ids = list(params.recap_rule_ids or [])
    return {
        "intensity_description": INTENSITY_MAP[params.intensity],
        "tone_description": TONE_MAP[params.tone],
        "equations_handling": EQ_HANDLING_MAP[params.equations_handling],
        "diagrams_handling": DIAG_HANDLING_MAP[params.diagrams_handling],
        "analogies": ANALOGY_MAP[params.analogies],
        "structure": STRUCTURE_MAP[params.structure],
        "language": f"{lang_name} — write ALL output text in {lang_name} only",
        "extra_instructions": extra,
        "recap_active_ids": render_active_ids(recap_ids),
        "recap_renames_directive": render_renames_directive(recap_ids),
        "recap_keypoints_directive": render_keypoints_directive(
            assigned_keypoints or [],
            label="Key Takeaways",
        ),
    }


class PostRegenQCResult(BaseModel):
    pass_: bool = Field(alias="pass", default=True)
    drifted_values: list[str] = Field(default_factory=list)
    original_number_count: int = 0
    block_count_original: int = 0
    block_count_regenerated: int = 0
    word_count_original: int = 0
    word_count_regenerated: int = 0
    word_ratio: float = 1.0
    warnings: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


class RegenerationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    book_id: UUID
    params: dict[str, Any]
    blocks_by_section: dict[str, Any]
    qc_drift: dict[str, Any] | None
    created_at: datetime
