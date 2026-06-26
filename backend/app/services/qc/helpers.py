"""Helpers used by local and LLM QC.

Ports the JS helpers from the working prototype (documented in 03_QC_LOGIC.md).
"""

from __future__ import annotations

import re

# Match: 2.3, 9.11, 0.01, 1.6, 6.626, 3×10⁸, 10⁻¹⁹, 931.5
_NUMBER_RE = re.compile(r"\b\d+\.?\d*(?:\s*[×x]\s*10[⁻\-]?\d+)?\b")

# word = word, operators between alphanumeric / Greek symbols
_EQ_RE = re.compile(
    r"[A-Za-zα-ωΑ-Ω_]+\s*[=<>≤≥±]\s*[A-Za-z0-9α-ωΑ-Ω\s\+\-\*\/\^\(\)\.]+"
)

# Capitalized multi-word phrases (e.g. "Photoelectric Effect") — use horizontal
# whitespace only so we don't cross line boundaries and pick up stray matches
# like "Heading\nFirstWord".
_CAP_PHRASE_RE = re.compile(r"\b([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+)+)\b")

# Single- or double-quoted terms (3..30 chars)
_QUOTED_RE = re.compile(r"'([^']{3,30})'|\"([^\"]{3,30})\"")

_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]+")


def extract_numbers(text: str) -> list[str]:
    """Return deduped numeric tokens found in ``text``."""
    matches = _NUMBER_RE.findall(text)
    return sorted({m.replace(" ", "") for m in matches if m})


def extract_equations(text: str) -> list[str]:
    """Return deduped equation-like substrings (3..100 chars)."""
    matches = _EQ_RE.findall(text)
    return sorted({m.strip() for m in matches if 3 < len(m.strip()) < 100})


def extract_key_terms(text: str) -> list[str]:
    """Return capitalized multi-word phrases + quoted terms, capped at 20."""
    terms: list[str] = []
    for m in _CAP_PHRASE_RE.finditer(text):
        if len(m.group(1)) > 5:
            terms.append(m.group(1))
    for m in _QUOTED_RE.finditer(text):
        t = m.group(1) or m.group(2)
        if t:
            terms.append(t)
    # Preserve first-occurrence order up to 20
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= 20:
            break
    return out


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.findall(text) if s.strip()]


def paragraphs_to_plain_text(paragraphs: list[dict]) -> str:
    """Flatten the paragraph list returned by P4 into a single plain-text blob."""
    parts: list[str] = []
    for p in paragraphs:
        t = p.get("type") or p.get("t")
        if t == "definition":
            term = p.get("term", "")
            content = p.get("content", "") or p.get("c", "")
            parts.append(f"{term}: {content}" if term else content)
        elif t == "example":
            label = p.get("label", "")
            prob = p.get("prob", "")
            eqs = p.get("eqs", []) or []
            body = [part for part in (label, prob) if part]
            if eqs:
                body.append(" ".join(eqs))
            parts.append(" ".join(body))
        elif t in ("example_ref", "exercise_ref", "question_ref"):
            label = p.get("label", "") or p.get("number", "")
            if label:
                parts.append(label)
        else:
            content = p.get("content", "") or p.get("c", "")
            if content:
                parts.append(content)
    return "\n\n".join(parts).strip()


def blocks_to_plain_text(blocks: list[dict]) -> str:
    """Flatten stored Block dicts (canonical schema) into plain text."""
    parts: list[str] = []
    for b in blocks:
        t = b.get("t")
        if t == "def":
            parts.append(f"{b.get('term', '')}: {b.get('c', '')}".strip(": "))
        elif t == "example":
            label = b.get("label", "")
            prob = b.get("prob", "")
            eqs = b.get("eqs", []) or []
            pieces = [x for x in (label, prob) if x]
            if eqs:
                pieces.append(" ".join(eqs))
            parts.append(" ".join(pieces))
        elif t in ("example_ref", "exercise_ref", "question_ref"):
            label = b.get("label", "") or b.get("number", "")
            if label:
                parts.append(label)
        elif t == "list":
            items = b.get("items", []) or []
            parts.append("\n".join(items))
        else:
            parts.append(b.get("c", "") or "")
    return "\n\n".join(p for p in parts if p).strip()
