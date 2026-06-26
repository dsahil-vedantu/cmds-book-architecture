"""Mock Claude responses — lets the whole extraction flow run end-to-end
without an Anthropic API key.

**Real PDF aware.** When the incoming messages contain a base64 PDF (as P1, P2,
and P3 all do), we extract the actual text and build the schema / extraction
from it.

Extraction chain:
  1. pypdf (fast, text-based PDFs)
  2. pdfplumber (slower, handles more messy PDFs)
  3. empty → friendly error

Schema detection chain:
  1. Numbered / "Chapter N" headings
  2. Title Case standalone lines (e.g. "Introduction", "Methods")
  3. ALL-CAPS standalone lines
  4. Fallback: one section per page

Per-section P4 responses are verbatim slices of the real chunk so local QC
passes cleanly.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import random
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class _MockBlock:
    type: str
    text: str


@dataclass
class _MockResponse:
    content: list[_MockBlock]


def _text(s: str) -> _MockResponse:
    return _MockResponse(content=[_MockBlock(type="text", text=s)])


# ── PDF extraction ───────────────────────────────────────────────────────

def _pdf_bytes_from_messages(messages: list[dict]) -> bytes | None:
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "document"
                    and block.get("source", {}).get("type") == "base64"
                ):
                    try:
                        return base64.b64decode(block["source"]["data"])
                    except Exception as e:
                        logger.warning("Failed to decode PDF base64: %s", e)
                        return None
    return None


def _extract_pages_pypdf(pdf_bytes: bytes) -> list[str]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        out: list[str] = []
        for page in reader.pages:
            try:
                out.append((page.extract_text() or "").strip())
            except Exception:
                out.append("")
        return out
    except Exception as e:
        logger.warning("pypdf page extract failed: %s", e)
        return []


def _extract_pages_pdfplumber(pdf_bytes: bytes) -> list[str]:
    try:
        import pdfplumber

        out: list[str] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                try:
                    out.append((page.extract_text() or "").strip())
                except Exception:
                    out.append("")
        return out
    except Exception as e:
        logger.warning("pdfplumber page extract failed: %s", e)
        return []


def _extract_pages_pypdfium(pdf_bytes: bytes) -> list[str]:
    """Third fallback using pypdfium2 — often succeeds on PDFs the other two
    libraries stumble on (subsetted fonts, complex layouts, rotated text)."""
    try:
        import pypdfium2 as pdfium

        out: list[str] = []
        pdf = pdfium.PdfDocument(pdf_bytes)
        for i in range(len(pdf)):
            try:
                textpage = pdf[i].get_textpage()
                out.append((textpage.get_text_range() or "").strip())
                textpage.close()
            except Exception:
                out.append("")
        pdf.close()
        return out
    except Exception as e:
        logger.warning("pypdfium2 page extract failed: %s", e)
        return []


def _extract_pages(pdf_bytes: bytes) -> list[str]:
    """Return per-page text. Tries pypdf → pdfplumber → pypdfium2 in order,
    keeping whichever produces the most text."""
    candidates = [
        ("pypdf", _extract_pages_pypdf(pdf_bytes)),
        ("pdfplumber", _extract_pages_pdfplumber(pdf_bytes)),
        ("pypdfium2", _extract_pages_pypdfium(pdf_bytes)),
    ]
    # Pick the candidate with the most characters of text
    best = max(candidates, key=lambda kv: len("\n".join(kv[1]).strip()))
    chars = len("\n".join(best[1]).strip())
    if chars > 0:
        logger.info("PDF extraction winner: %s (%d chars)", best[0], chars)
    return best[1]


def _full_text(pages: list[str]) -> str:
    return "\n\n".join(p for p in pages if p.strip())


# ── Heading detection ────────────────────────────────────────────────────

# Numbered headings: "1. Foo", "1.1 Bar", "Chapter 3", "Section 2"
_NUM_HEADING_RE = re.compile(
    r"^\s*(?:chapter\s+(?P<chnum>\d+)|section\s+(?P<snum>\d+)|(?P<num>\d+(?:\.\d+){0,2}))[\.\)\:\-\s]+(?P<title>.{3,120})\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Unnumbered standalone headings — short line (not ending in a full stop), followed by a blank line.
_LINE_TOKENS_RE = re.compile(r"\s+")


def _is_titlecase_heading(line: str) -> bool:
    """Heuristic: short Title Case or ALL CAPS line, no sentence punctuation."""
    line = line.strip()
    if not (3 <= len(line) <= 80):
        return False
    if line.endswith((".", "?", "!", ",", ";", ":")):
        return False
    words = _LINE_TOKENS_RE.split(line)
    if not (1 <= len(words) <= 10):
        return False
    # ALL CAPS
    letters = [c for c in line if c.isalpha()]
    if len(letters) >= 3 and all(c.isupper() for c in letters):
        return True
    # Title Case: majority of alphabetic words start with uppercase
    cap_words = sum(
        1 for w in words if w and w[0].isupper() and any(ch.isalpha() for ch in w)
    )
    content_words = sum(1 for w in words if any(ch.isalpha() for ch in w))
    if content_words >= 1 and cap_words / max(content_words, 1) >= 0.6:
        return True
    return False


_EXERCISE_TITLE_RE = re.compile(
    r"^(exercise|exercises|problem|problems|practice|practice\s+problems|mcqs?|multiple\s+choice|answer\s+keys?|review\s+questions?|self\s*[\-]?\s*assessment|solved\s+examples?)$",
    re.IGNORECASE,
)


def _is_exercise_title(title: str) -> bool:
    return bool(_EXERCISE_TITLE_RE.match(title.strip()))


def _detect_sections_numbered(raw_text: str) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for m in _NUM_HEADING_RE.finditer(raw_text):
        num = m.group("num") or m.group("chnum") or m.group("snum")
        title = (m.group("title") or "").strip().rstrip(".-:")
        if not num or not title or len(title) < 3 or num in seen_ids:
            continue
        seen_ids.add(num)
        level = num.count(".") + 1
        excluded = _is_exercise_title(title)
        flat.append(
            {
                "id": num,
                "level": level,
                "title": title,
                "type": (
                    "excluded"
                    if excluded
                    else "chapter"
                    if level == 1
                    else "section"
                    if level == 2
                    else "subsection"
                ),
                "content_types": ["exercise"] if excluded else ["theory"],
                "subsections": [],
            }
        )
    if not flat:
        return []
    root: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []
    for sec in flat:
        while stack and stack[-1]["level"] >= sec["level"]:
            stack.pop()
        if stack:
            stack[-1]["subsections"].append(sec)
        else:
            root.append(sec)
        stack.append(sec)
    return root


def _detect_sections_unnumbered(raw_text: str) -> list[dict[str, Any]]:
    """Find Title-Case / ALL-CAPS standalone headings in the text."""
    lines = raw_text.split("\n")
    sections: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        if not _is_titlecase_heading(line):
            continue
        # Previous or next line must be blank-ish to reduce false positives
        prev_blank = i == 0 or not lines[i - 1].strip()
        next_blank = i == len(lines) - 1 or not lines[i + 1].strip()
        if not (prev_blank or next_blank):
            continue
        sections.append(
            {
                "id": str(len(sections) + 1),
                "level": 1,
                "title": line.strip(),
                "type": "chapter" if len(sections) == 0 else "section",
                "content_types": ["theory"],
                "subsections": [],
            }
        )
    # Need at least 2 sections for this to be useful
    return sections if len(sections) >= 2 else []


def _per_page_schema(pages: list[str]) -> list[dict[str, Any]]:
    """Fallback: one section per page that actually has content.

    - Skips pages with < 15 words (they're likely blank separator pages or
      image-only scans).
    - Derives each title from the first short line of the page when possible.
    - If no page has meaningful content, returns an empty list so the caller
      can show an "image-based PDF" placeholder instead of faking sections.
    """
    sections: list[dict[str, Any]] = []
    for i, page_text in enumerate(pages, start=1):
        words = page_text.split()
        if len(words) < 15:
            continue
        title = f"Page {i}"
        first_lines = [ln.strip() for ln in page_text.split("\n") if ln.strip()]
        for ln in first_lines[:3]:
            if 4 <= len(ln) <= 80:
                title = f"{i}. {ln}"
                break
        sections.append(
            {
                "id": str(len(sections) + 1),
                "level": 1,
                "title": title,
                "type": "chapter" if not sections else "section",
                "content_types": ["theory"],
                "subsections": [],
            }
        )
    return sections


def _empty_pdf_placeholder(title_hint: str) -> list[dict[str, Any]]:
    return [
        {
            "id": "1",
            "level": 1,
            "title": title_hint or "Image-based PDF — needs real OCR",
            "type": "chapter",
            "content_types": ["theory"],
            "subsections": [],
        }
    ]


def _build_schema(pages: list[str]) -> tuple[list[dict], str]:
    """Return (sections, title_hint) from the page text.

    Strategy (in order):
      1. Numbered / "Chapter N" headings
      2. Title Case / ALL CAPS standalone lines
      3. One section per page *that actually has content*
      4. If none of the above found anything meaningful, return a single
         honest placeholder so the UI can tell the user their PDF needs OCR.
    """
    raw_text = _full_text(pages)
    first_lines = [ln.strip() for ln in raw_text.split("\n") if ln.strip()][:5]
    title_hint = first_lines[0][:80] if first_lines else "Uploaded Document"

    sections = _detect_sections_numbered(raw_text)
    if sections:
        return sections, title_hint

    sections = _detect_sections_unnumbered(raw_text)
    if sections:
        return sections, title_hint

    sections = _per_page_schema(pages)
    if sections:
        return sections, title_hint

    # No extractable content at all — be honest.
    return _empty_pdf_placeholder(title_hint), title_hint


# ── P4 section extraction ────────────────────────────────────────────────

_EQ_LINE_RE = re.compile(r"^[A-Za-zα-ωΑ-Ω_]+\s*[=<>≤≥±].+$", re.MULTILINE)


def _paragraphs_from_chunk(chunk_text: str) -> list[dict]:
    paragraphs: list[dict] = []
    lines = chunk_text.split("\n")
    buffer: list[str] = []

    def flush_buffer() -> None:
        if buffer:
            text = " ".join(buffer).strip()
            if text:
                paragraphs.append({"type": "body", "content": text})
            buffer.clear()

    heading_re = re.compile(r"^\s*\d+(?:\.\d+){0,2}[\.\):\-\s]+\S")
    first = True
    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_buffer()
            first = False
            continue
        if first and (heading_re.match(stripped) or len(stripped.split()) < 10):
            flush_buffer()
            paragraphs.append({"type": "heading", "content": stripped})
            first = False
            continue
        first = False
        if _EQ_LINE_RE.match(stripped) and len(stripped) < 80 and "=" in stripped:
            flush_buffer()
            paragraphs.append({"type": "equation", "content": stripped})
            continue
        buffer.append(stripped)

    flush_buffer()
    if not paragraphs and chunk_text.strip():
        paragraphs.append({"type": "body", "content": chunk_text.strip()})
    return paragraphs


# ── Prompt-kind detector ─────────────────────────────────────────────────

def _detect_kind(system: str, messages: list[dict]) -> str:
    s = (system or "").lower()[:200]
    if "pdf analyser" in s:
        return "analyser"
    if "educational content architect" in s and "schema" in s:
        return "schema"
    if "verbatim text extraction engine" in s:
        return "raw_text"
    if "expert educational content extractor" in s or "transcription engine" in s:
        return "p4"
    if "academic theory regeneration engine" in s:
        return "p5"
    if "precise qc auditor" in s:
        return "p6"
    for m in messages or []:
        content = m.get("content")
        if isinstance(content, str) and content.strip().lower() == "ping":
            return "ping"
    return "unknown"


def _extract_section_from_user_msg(messages: list[dict]) -> tuple[str, str, str]:
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            sid_match = re.search(r"SECTION ID:\s*(\S+)", content)
            title_match = re.search(r'SECTION TITLE:\s*"([^"]+)"', content)
            source_match = re.search(
                r"SOURCE TEXT[^\n]*\n[─\-]+\n(.+?)\n[─\-]+",
                content,
                re.DOTALL,
            )
            sid = sid_match.group(1).strip() if sid_match else ""
            title = title_match.group(1).strip() if title_match else ""
            source = source_match.group(1).strip() if source_match else ""
            return sid, title, source
    return "", "", ""


def _extract_regen_text(messages: list[dict]) -> str:
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            marker = "REWRITABLE CONTENT TO REGENERATE:"
            if marker in content:
                after = content.split(marker, 1)[1]
                end = after.find("\n\nReturn the regenerated JSON")
                return after[:end].strip() if end >= 0 else after.strip()
    return ""


# ── Dispatcher ───────────────────────────────────────────────────────────

async def mock_messages_create(
    *,
    system: str,
    messages: list[dict],
    max_tokens: int,
    **_kwargs: Any,
) -> Any:
    await asyncio.sleep(0.3 + random.uniform(0.0, 0.3))
    kind = _detect_kind(system, messages)

    if kind == "ping":
        return _text("pong")

    pdf_bytes = _pdf_bytes_from_messages(messages)
    pages = _extract_pages(pdf_bytes) if pdf_bytes else []
    raw_text = _full_text(pages)

    if kind == "analyser":
        words = len(raw_text.split()) if raw_text else 0
        first_lines = [ln.strip() for ln in raw_text.split("\n") if ln.strip()][:5]
        guess_title = first_lines[0][:80] if first_lines else "Uploaded Document"
        out = {
            "pdf_type": "digital" if raw_text else "scanned",
            "estimated_pages": len(pages) or 1,
            "estimated_words": words,
            "document_title": guess_title,
            "subject": "",
            "has_equations": bool(_EQ_LINE_RE.search(raw_text)) if raw_text else False,
            "has_tables": False,
            "has_diagrams": False,
        }
        return _text(json.dumps(out))

    if kind == "schema":
        sections, title_hint = _build_schema(pages)

        # Collect titles of anything marked "excluded" for the summary panel.
        def _collect_excluded(arr: list[dict]) -> list[str]:
            out: list[str] = []
            for s in arr:
                if s.get("type") == "excluded":
                    out.append(s["title"])
                out.extend(_collect_excluded(s.get("subsections") or []))
            return out

        payload = {
            "document_title": title_hint,
            "subject": "",
            "sections": sections,
            "exclusion_summary": _collect_excluded(sections),
        }
        return _text(json.dumps(payload))

    if kind == "raw_text":
        if raw_text:
            return _text(raw_text)
        return _text(
            "(The uploaded PDF appears to be image-based. Set an ANTHROPIC_API_KEY "
            "in .env and restart the backend to use real OCR.)"
        )

    if kind == "p4":
        sid, title, source = _extract_section_from_user_msg(messages)
        paragraphs = _paragraphs_from_chunk(source) if source else []
        if not paragraphs:
            paragraphs = [
                {
                    "type": "body",
                    "content": (
                        "No text could be extracted for this section. This usually "
                        "means the page is image-based — configure a real OCR "
                        "provider (set ANTHROPIC_API_KEY or add Mathpix/Sarvam/"
                        "Google Vision keys) to read it."
                    ),
                }
            ]
        payload = {
            "section_id": sid,
            "section_title": title,
            "word_count": sum(
                len((p.get("content") or "").split()) for p in paragraphs
            ),
            "paragraphs": paragraphs,
            "notes": "",
        }
        return _text(json.dumps(payload))

    if kind == "p5":
        free_text = _extract_regen_text(messages)
        parts = [p.strip() for p in re.split(r"\n\n+", free_text) if p.strip()]
        paragraphs: list[dict] = []
        for p in parts:
            if p.startswith("### "):
                paragraphs.append({"type": "heading", "content": p[4:].strip()})
            elif p.startswith("[KEY POINT:"):
                body = p[len("[KEY POINT:") :].rstrip("]").strip()
                paragraphs.append({"type": "key_point", "content": body})
            else:
                rewritten = (
                    p.replace("first observed", "initially documented")
                    .replace("depends on", "is determined by")
                    .replace("proposed", "suggested")
                    .replace("provides", "offers")
                )
                paragraphs.append({"type": "body", "content": rewritten})
        return _text(
            json.dumps(
                {
                    "paragraphs": paragraphs
                    or [{"type": "body", "content": free_text}],
                    "regen_notes": "Mock regeneration — light rephrasing with values preserved.",
                }
            )
        )

    if kind == "p6":
        return _text(
            json.dumps(
                {
                    "pass": True,
                    "severity": "low",
                    "issues": {
                        "missing_content": [],
                        "value_drift": [],
                        "truncated": [],
                        "added_content": [],
                    },
                    "fix_prompt": "",
                    "verdict": "Mock auditor — no issues detected.",
                }
            )
        )

    logger.warning("Mock Claude: unknown prompt kind — returning empty text")
    return _text("")
