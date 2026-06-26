"""Schemas for P1 Analyser + P2 Schema generator outputs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

_VALID_SECTION_TYPES = {"chapter", "section", "subsection", "excluded"}
_SECTION_TYPE_MAP = {
    "subsubsection": "subsection",
    "unit": "chapter",
    "topic": "subsection",
    "sub-section": "subsection",
    "sub_section": "subsection",
    "part": "chapter",
}


class AnalyserResult(BaseModel):
    pdf_type: Literal["digital", "scanned", "mixed"]
    estimated_pages: int = 0
    estimated_words: int = 0
    document_title: str = ""
    subject: str = ""
    has_equations: bool = False
    has_tables: bool = False
    has_diagrams: bool = False


class SchemaSection(BaseModel):
    id: str
    level: int
    title: str
    type: str = "section"
    content_types: list[str] = Field(default_factory=lambda: ["theory"])
    subsections: list["SchemaSection"] = Field(default_factory=list)
    # New fields from improved prompt (optional for backwards compat)
    page_start: int | None = None
    page_end: int | None = None
    is_numbered: bool = True
    # Expected question count for this section, populated by the schema LLM pass.
    # Used by the v3 question worker as the ground-truth target for completeness
    # validation. None means schema didn't populate (older books / fallback).
    expected_question_count: int | None = None
    # SCHEMA Week 1 Day 2 — canonical section UUID. Assigned by
    # schema_builder.assign_uuids_to_schema() right after Gemini returns,
    # before the schema is committed to book.schema. Stable across
    # re-analyse (same UUID preserved when (title, page_start) matches
    # an existing schema entry). Downstream Section row uses this as
    # its primary-key UUID, replacing the schema_alignment patch.
    #
    # Currently OPTIONAL — populated for new uploads after this commit;
    # legacy schemas have it as None. Both paths must be supported until
    # SCHEMA Week 3 wires this into extract.py (after explicit Celery
    # touch approval).
    uuid: str | None = None

    model_config = {"extra": "ignore"}

    @field_validator("type", mode="before")
    @classmethod
    def normalize_type(cls, v: str) -> str:
        if v in _VALID_SECTION_TYPES:
            return v
        return _SECTION_TYPE_MAP.get(str(v).lower(), "subsection")


class ExcludedSection(BaseModel):
    title: str
    page_start: int | None = None
    page_end: int | None = None
    reason: str = ""
    is_numbered: bool = False
    # End-of-chapter "Exercises" / "Practice Questions" blocks live here. The
    # v3 question worker treats these as section-aligned units too — they get
    # their own count target.
    expected_question_count: int | None = None
    # Sub-categories printed inside a Practice Questions block — e.g.
    # "Very Short Answer Type", "Short Answer Type", "MCQs", "Numerical
    # Problems", "Assertion-Reason". The analyser fills these from visible
    # sub-headings; the worker emits each as its own extraction unit so the
    # bank mirrors the book exactly.
    subsections: list[ExcludedSection] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class BookSchema(BaseModel):
    document_title: str = ""
    # subject/grade_level/board are all "fill from print if visible, else null"
    # per the schema prompt (§1, line 55). Pydantic must accept null for all
    # three uniformly. Downstream consumers fall back to "" via `schema.subject or ""`.
    subject: str | None = None
    grade_level: str | None = None
    board: str | None = None
    total_pages: int | None = None
    sections: list[SchemaSection] = Field(default_factory=list)
    # New prompt returns excluded_sections; old returned exclusion_summary
    excluded_sections: list[ExcludedSection] = Field(default_factory=list)
    exclusion_summary: list[str] = Field(default_factory=list)
    extraction_notes: str = ""

    model_config = {"extra": "ignore"}


SchemaSection.model_rebuild()
ExcludedSection.model_rebuild()
