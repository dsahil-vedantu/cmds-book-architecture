"""Section chunking — turns raw_text + schema into per-section text chunks.

Strategy: walk the flattened schema in order. For each section, find the first
occurrence of its title (or section_id header like "5.1") in raw_text at or
after the previous section's end position. The chunk runs until the next
section's match position.

Boundary validation: check that the chunk text begins near the section's title.
If the title isn't found near the start, set ``suspect=True`` so the caller can
surface a warning but still attempt extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.schemas.analyser import BookSchema, SchemaSection

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class Chunk:
    section_id: str
    title: str
    level: int
    text: str
    word_count: int = 0
    start: int = 0
    end: int = 0
    found: bool = True
    suspect: bool = False
    suspect_reason: str | None = None

    def __post_init__(self) -> None:
        if self.word_count == 0 and self.text:
            self.word_count = len(self.text.split())


def flatten_sections(schema: BookSchema) -> list[SchemaSection]:
    """Pre-order traversal of all non-excluded sections. Used for DB upsert."""
    out: list[SchemaSection] = []

    def walk(sections: list[SchemaSection]) -> None:
        for s in sections:
            if s.type != "excluded":
                out.append(s)
            walk(s.subsections)

    walk(schema.sections)
    return out


def extractable_sections(schema: BookSchema) -> list[SchemaSection]:
    """Return only sections that should have content extracted via OCR.

    Rules (applied in order):
    - Top-level sections (depth=0 in schema.sections) → always extract.
      These give a full-chapter combined view.
    - Leaf sections (no subsections at any depth) → always extract.
      These are the actual numbered/named content sections.
    - Intermediate containers (have subsections AND depth > 0) → SKIP.
      Their content is fully covered by their children — extracting them
      would duplicate content already present in the leaf sections.

    The full section list (including containers) is still upserted to the DB
    via flatten_sections so the sidebar hierarchy remains intact.
    """
    out: list[SchemaSection] = []

    def walk(sections: list[SchemaSection], depth: int = 0) -> None:
        for s in sections:
            if s.type == "excluded":
                walk(s.subsections, depth + 1)
                continue
            is_leaf = len(s.subsections) == 0
            is_top_level = depth == 0
            if is_leaf or is_top_level:
                out.append(s)
            walk(s.subsections, depth + 1)

    walk(schema.sections)
    return out


def _normalize(s: str) -> str:
    return _WHITESPACE_RE.sub(" ", s).strip().lower()


def _build_title_patterns(section: SchemaSection) -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    title_escaped = re.escape(section.title.strip())
    sid_escaped = re.escape(section.id)
    # "5.1 Title" or "5.1. Title" or "5.1: Title"
    patterns.append(
        re.compile(
            rf"(?im)^\s*{sid_escaped}[\.\):\-\s]+{title_escaped}\s*$"
        )
    )
    # Title on its own line
    patterns.append(re.compile(rf"(?im)^\s*{title_escaped}\s*$"))
    # "5.1 Title" anywhere in line (header run together)
    patterns.append(re.compile(rf"(?i){sid_escaped}[\.\):\-\s]+{title_escaped}"))
    # Title anywhere (loose fallback)
    patterns.append(re.compile(rf"(?i){title_escaped}"))
    return patterns


def _find_section_start(raw_text: str, section: SchemaSection, search_from: int) -> int | None:
    for pat in _build_title_patterns(section):
        m = pat.search(raw_text, search_from)
        if m:
            return m.start()
    return None


def build_chunks(raw_text: str, schema: BookSchema) -> list[Chunk]:
    """Walk schema sections in order; slice raw_text into per-section chunks."""
    sections = flatten_sections(schema)
    if not sections or not raw_text:
        return []

    positions: list[tuple[SchemaSection, int | None]] = []
    cursor = 0
    for sec in sections:
        # sec.id is the section_id like "1.1"
        pos = _find_section_start(raw_text, sec, cursor)
        positions.append((sec, pos))
        if pos is not None:
            cursor = pos + 1

    chunks: list[Chunk] = []
    for i, (sec, pos) in enumerate(positions):
        if pos is None:
            chunks.append(
                Chunk(
                    section_id=sec.id,
                    title=sec.title,
                    level=sec.level,
                    text="",
                    start=0,
                    end=0,
                    found=False,
                    suspect=True,
                    suspect_reason="Section title not found in raw text",
                )
            )
            continue

        # end = next section's start, or end-of-text
        end = len(raw_text)
        for _, next_pos in positions[i + 1 :]:
            if next_pos is not None and next_pos > pos:
                end = next_pos
                break

        text = raw_text[pos:end].strip()
        chunk = Chunk(
            section_id=sec.id,
            title=sec.title,
            level=sec.level,
            text=text,
            start=pos,
            end=end,
            found=True,
        )
        # Boundary validation: title should appear in the first ~200 chars
        head_norm = _normalize(text[:200])
        if _normalize(sec.title) not in head_norm:
            chunk.suspect = True
            chunk.suspect_reason = "Title not found near chunk start"
        chunks.append(chunk)

    return chunks


def get_token_budget(word_count: int) -> int:
    """Dynamic token budget for P4 based on chunk size (per the brief)."""
    if word_count < 150:
        return 1500
    if word_count < 300:
        return 2500
    if word_count < 500:
        return 3500
    if word_count < 800:
        return 5000
    if word_count < 1200:
        return 6500
    return 7000
