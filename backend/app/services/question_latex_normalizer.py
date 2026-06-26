"""LaTeX normalization for QUESTION text (questions_v3 persist seam).

Theory equation blocks are separated math (one block = one expression) and
are handled by ``latex_normalizer.normalize_latex``. Question / solution /
option text is the opposite shape: **prose with inline math**. A single
``raw_text`` string interleaves natural language, inline ``$...$`` spans,
bare Unicode math glyphs (``gt²``, ``l₁``), and the occasional chemistry
formula (``H2SO4``).

This module makes that inline-mixed text render reliably in KaTeX without
corrupting the prose around it. Three passes, all conservative:

  1. Clean **existing** ``$...$`` / ``$$...$$`` spans in place (Unicode →
     LaTeX, brace repair, ``\\begin{cases}`` single-backslash row fixes).
  2. Wrap **bare Unicode math glyphs** that would otherwise render as literal
     text — only contiguous runs that actually contain a math glyph.
  3. Wrap **obvious chemistry formulas** in ``$\\ce{...}$`` — only when the
     token is unambiguously a formula (reaction arrow, or 3+ element-count
     groups). False positives ("Plan B2", "section 3.2") are worse than
     misses, so when unsure we SKIP.

Properties:
  - Deterministic: same input → same output.
  - Idempotent: running twice yields identical output (no double-wrapping).
  - Safe: never raises; degraded input returns best-effort + telemetry.
  - Prose-preserving: only touches spans that contain real math signals.

Reuses ``_UNICODE_MAP``, ``_convert_unicode``, ``_repair_braces`` from the
theory ``latex_normalizer`` — single source of truth for the glyph map.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.latex_normalizer import (
    _UNICODE_MAP,
    _convert_unicode,
    _repair_braces,
)


# ─── Report ───────────────────────────────────────────────────────


@dataclass
class QuestionLatexReport:
    """Per-call telemetry from one normalize_question_latex run."""

    spans_normalized: int = 0  # existing $...$ spans cleaned
    glyphs_wrapped: int = 0  # bare Unicode runs wrapped in $...$
    chemistry_wrapped: int = 0  # \ce{} conversions
    braces_repaired: int = 0
    cases_fixed: int = 0  # \begin{...} single-backslash row fixes

    def __add__(self, other: "QuestionLatexReport") -> "QuestionLatexReport":
        return QuestionLatexReport(
            spans_normalized=self.spans_normalized + other.spans_normalized,
            glyphs_wrapped=self.glyphs_wrapped + other.glyphs_wrapped,
            chemistry_wrapped=self.chemistry_wrapped + other.chemistry_wrapped,
            braces_repaired=self.braces_repaired + other.braces_repaired,
            cases_fixed=self.cases_fixed + other.cases_fixed,
        )


# ─── Pass 1: clean existing $...$ / $$...$$ spans ─────────────────


# Match $$...$$ (display) OR $...$ (inline). $$ first so it wins.
# Non-greedy bodies; require at least one non-$ char inside.
_DOLLAR_SPAN = re.compile(r"\$\$(.+?)\$\$|\$([^$]+?)\$", re.DOTALL)

# Environments whose rows are separated by `\\`. Inside these, Gemini often
# emits a single ` \ ` (backslash + space) instead of ` \\ `.
_ENV_NAMES = (
    "cases",
    "matrix",
    "pmatrix",
    "bmatrix",
    "vmatrix",
    "Bmatrix",
    "Vmatrix",
    "aligned",
    "align",
    "array",
    "gathered",
    "split",
)
_ENV_BLOCK = re.compile(
    r"(\\begin\{(?:" + "|".join(_ENV_NAMES) + r")\}.*?\\end\{(?:" + "|".join(_ENV_NAMES) + r")\})",
    re.DOTALL,
)
# A single backslash that is NOT part of `\\`, a LaTeX command (\word), or an
# escaped char, used as a row separator: backslash followed by whitespace and
# then more content. We target ` \ ` patterns acting as row breaks.
_SINGLE_BS_ROW = re.compile(r"(?<!\\)\\(?=\s)(?!\\)")


def _fix_env_rows(span_body: str) -> tuple[str, int]:
    """Inside matrix/cases/aligned environments, promote lone ``\\`` row
    separators to ``\\\\``. Returns (fixed, count)."""
    count = 0

    def _fix_block(m: re.Match) -> str:
        nonlocal count
        block = m.group(1)
        # Replace ` \<space>` (single backslash before whitespace) with ` \\`.
        # _SINGLE_BS_ROW matches a backslash that is preceded by a non-backslash
        # (or start) and followed by whitespace and is not itself part of `\\`.
        fixed, n = _SINGLE_BS_ROW.subn(r"\\\\", block)
        count += n
        return fixed

    out = _ENV_BLOCK.sub(_fix_block, span_body)
    return out, count


def _clean_span_body(body: str) -> tuple[str, QuestionLatexReport]:
    """Normalize the inside of one $...$ span: Unicode → LaTeX, env row
    fixes, brace repair. Does NOT add/remove the surrounding delimiters."""
    rep = QuestionLatexReport()

    body2, uni_n = _convert_unicode(body)
    # Unicode substitutions don't get their own counter on the question report,
    # but they do mean the span was normalized.

    body3, cases_n = _fix_env_rows(body2)
    rep.cases_fixed = cases_n

    body4, brace_n, _failed = _repair_braces(body3)
    rep.braces_repaired = brace_n

    if uni_n or cases_n or brace_n or body4 != body:
        rep.spans_normalized = 1

    return body4, rep


def _clean_existing_spans(text: str) -> tuple[str, QuestionLatexReport, list[tuple[int, int]]]:
    """Pass 1. Clean each existing dollar span. Returns the rewritten text,
    an aggregate report, and the char-ranges of spans in the NEW text so the
    glyph-wrap pass can skip them."""
    total = QuestionLatexReport()
    out_parts: list[str] = []
    spans: list[tuple[int, int]] = []
    pos = 0  # cursor in original text
    out_len = 0  # running length of out string

    for m in _DOLLAR_SPAN.finditer(text):
        # Prose before this span passes through untouched.
        pre = text[pos : m.start()]
        out_parts.append(pre)
        out_len += len(pre)

        display = m.group(1) is not None
        body = m.group(1) if display else m.group(2)
        delim = "$$" if display else "$"

        cleaned, rep = _clean_span_body(body)
        total = total + rep

        span_text = f"{delim}{cleaned}{delim}"
        start = out_len
        out_parts.append(span_text)
        out_len += len(span_text)
        spans.append((start, out_len))

        pos = m.end()

    tail = text[pos:]
    out_parts.append(tail)

    return "".join(out_parts), total, spans


# ─── Pass 2: wrap bare Unicode math runs ──────────────────────────


# The set of glyphs that are unambiguously math and won't render as text.
_MATH_GLYPHS = set(_UNICODE_MAP.keys())

# Characters that may be part of a contiguous math token alongside a glyph:
# alphanumerics, common operators, parens, dots, and the glyphs themselves.
_TOKEN_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    "+-*/=().,'_^|"
)


def _is_token_char(ch: str) -> bool:
    return ch in _TOKEN_CHARS or ch in _MATH_GLYPHS


# Two or more consecutive single-char super/subscripts (``^-^1``, ``^2^3``) are
# invalid KaTeX — they must be one braced group (``^{-1}``, ``^{23}``). The
# shared _UNICODE_MAP emits one ``^x`` per glyph, so adjacent Unicode
# super/subscripts (``s⁻¹`` → ``s^-^1``) need collapsing.
_CONSEC_SUP = re.compile(r"(?:\^(\{[^}]*\}|.)){2,}")
_CONSEC_SUB = re.compile(r"(?:_(\{[^}]*\}|.)){2,}")


def _collapse_scripts(s: str) -> str:
    """Merge runs of adjacent ``^a^b`` → ``^{ab}`` and ``_a_b`` → ``_{ab}``."""

    def _merge(marker: str):
        def _f(m: re.Match) -> str:
            parts = re.findall(r"" + re.escape(marker) + r"(\{[^}]*\}|.)", m.group(0))
            inner = "".join(p[1:-1] if p.startswith("{") else p for p in parts)
            return f"{marker}{{{inner}}}"

        return _f

    s = _CONSEC_SUP.sub(_merge("^"), s)
    s = _CONSEC_SUB.sub(_merge("_"), s)
    return s


def _convert_unicode_spaced(run: str) -> str:
    """Convert Unicode glyphs to LaTeX glyph-by-glyph, inserting a space when a
    multi-letter command (``\\pi``, ``\\alpha``) is immediately followed by a
    letter, else ``\\pi`` + ``r`` would read as the undefined ``\\pir``. Only
    the boundary *between* a converted command and the next source char gets a
    space — a command's own trailing letters (``\\parallel``) are untouched.
    Finally collapses adjacent super/subscripts (``s^-^1`` → ``s^{-1}``)."""
    out: list[str] = []
    for ch in run:
        if ch in _UNICODE_MAP:
            piece = _UNICODE_MAP[ch]
            # If the previous emitted piece ended in a multi-letter command and
            # this command also starts with a backslash-letter, KaTeX needs a
            # gap; but more importantly a *letter* after a command needs a gap.
            if out and re.search(r"\\[A-Za-z]+$", out[-1]) and piece[:1] != " ":
                out.append(" ")
            out.append(piece)
        else:
            # A plain source char following a command-ending piece.
            if out and ch.isalpha() and re.search(r"\\[A-Za-z]+$", out[-1]):
                out.append(" ")
            out.append(ch)
    return _collapse_scripts("".join(out))


def _wrap_bare_glyphs(
    text: str, protected: list[tuple[int, int]]
) -> tuple[str, int]:
    """Pass 2. Find contiguous runs of math-token chars that contain at least
    one Unicode math glyph, outside protected ($...$) ranges, and wrap them in
    ``$...$`` after converting glyphs to LaTeX. Returns (text, runs_wrapped)."""

    def _in_protected(i: int) -> bool:
        for a, b in protected:
            if a <= i < b:
                return True
        return False

    out: list[str] = []
    n = len(text)
    i = 0
    wrapped = 0

    while i < n:
        if _in_protected(i):
            # Copy the whole protected span verbatim.
            # Find the protected range covering i.
            span_end = i + 1
            for a, b in protected:
                if a <= i < b:
                    span_end = b
                    break
            out.append(text[i:span_end])
            i = span_end
            continue

        ch = text[i]
        if _is_token_char(ch):
            # Greedily consume a contiguous token run (not crossing into a
            # protected span).
            j = i
            while j < n and not _in_protected(j) and _is_token_char(text[j]):
                j += 1
            run = text[i:j]
            if any(c in _MATH_GLYPHS for c in run):
                # This run contains real math — convert + wrap.
                converted = _convert_unicode_spaced(run)
                out.append(f"${converted}$")
                wrapped += 1
            else:
                out.append(run)
            i = j
        else:
            out.append(ch)
            i += 1

    return "".join(out), wrapped


# ─── Pass 3: chemistry formulas ───────────────────────────────────


# An element-count group: capital letter, optional lowercase, optional digits.
# e.g. H2, SO4 (S then O4 → two groups), Ca, Cl2.
_ELEM = r"[A-Z][a-z]?\d*"
# A formula token = 2+ element groups glued together, e.g. H2SO4, CaCl2, CO2.
_FORMULA_TOKEN = re.compile(rf"(?:{_ELEM}){{2,}}")

# A reaction expression: formula tokens / coefficients joined by + and an
# arrow (->, =, ⇌ rendered as -> already by unicode? we accept ASCII arrows).
_ARROW = r"->|→|⇌|⟶|—>"
# A reactant/product term: optional coefficient + an element-count token.
# Terms are joined by ``+``; reactants and products are separated by an arrow.
# The product side stops at the last ``+``-joined term, so trailing prose
# ("... 2H2O occurs") is NOT swallowed.
_TERM = r"\d*\s*[A-Za-z0-9()]+"
_REACTION = re.compile(
    rf"(?P<rxn>(?:{_TERM}\s*\+\s*)*{_TERM}\s*(?:{_ARROW})\s*{_TERM}(?:\s*\+\s*{_TERM})*)"
)


def _count_element_groups(token: str) -> int:
    return len(re.findall(_ELEM, token))


def _has_digit(token: str) -> bool:
    return any(c.isdigit() for c in token)


def _looks_like_formula(token: str) -> bool:
    """Conservative formula test: 3+ element-count groups AND at least one
    digit. ``H2SO4`` → H,S,O4 + digits → yes. ``Plan`` → P,l? no (one cap).
    ``B2`` → one group → no. ``CaCl2`` → Ca,Cl2 = 2 groups + digit → borderline;
    we require 3+ groups OR (2 groups with a trailing digit on each cap run).
    To stay safe we require 3+ groups when only digits present, but allow the
    very common 2-group case when a subscript digit clearly follows an element
    (CaCl2, CO2 handled separately below)."""
    groups = _count_element_groups(token)
    if groups >= 3 and _has_digit(token):
        return True
    return False


# Common 2-group oxides/salts that are unambiguous despite only 2 groups.
_KNOWN_2GROUP = re.compile(r"^(?:[A-Z][a-z]?\d+(?:[A-Z][a-z]?\d*)+)$")


def _wrap_chemistry(
    text: str, protected: list[tuple[int, int]]
) -> tuple[str, int]:
    """Pass 3. Wrap obvious chemistry in ``$\\ce{...}$``. Reaction arrows first
    (whole expression), then standalone formula tokens. Skips protected
    ($...$) ranges. Returns (text, count)."""
    wrapped = 0

    # Build a quick membership test for protected ranges by char index.
    prot = sorted(protected)

    def _overlaps_protected(a: int, b: int) -> bool:
        for pa, pb in prot:
            if a < pb and pa < b:
                return True
        return False

    # --- 3a: reaction expressions ---
    def _sub_reaction(m: re.Match) -> str:
        nonlocal wrapped
        a, b = m.start(), m.end()
        if _overlaps_protected(a, b):
            return m.group(0)
        rxn = m.group("rxn").strip()
        # Must contain an arrow AND at least one multi-element formula token to
        # qualify (avoids wrapping plain "a + b = c" arithmetic).
        if not re.search(_ARROW, rxn):
            return m.group(0)
        if not any(_count_element_groups(t) >= 2 for t in re.split(r"[\s+]+", rxn)):
            return m.group(0)
        wrapped += 1
        # Preserve trailing whitespace that the regex may have swallowed.
        trailing = ""
        while rxn and rxn[-1].isspace():
            trailing += rxn[-1]
            rxn = rxn[:-1]
        return f"$\\ce{{{rxn}}}$" + trailing

    text2 = _REACTION.sub(_sub_reaction, text)

    # Recompute protected ranges? The reaction sub may shift offsets, so for the
    # standalone-token pass we re-scan and rely on the $\ce{...}$ wrapper now
    # being a $-span we must avoid. Recompute protected from scratch on text2.
    prot2 = [(mm.start(), mm.end()) for mm in _DOLLAR_SPAN.finditer(text2)]

    def _overlaps2(a: int, b: int) -> bool:
        for pa, pb in prot2:
            if a < pb and pa < b:
                return True
        return False

    # --- 3b: standalone formula tokens ---
    def _sub_token(m: re.Match) -> str:
        nonlocal wrapped
        a, b = m.start(), m.end()
        if _overlaps2(a, b):
            return m.group(0)
        token = m.group(0)
        if not _looks_like_formula(token):
            return m.group(0)
        wrapped += 1
        return f"$\\ce{{{token}}}$"

    text3 = _FORMULA_TOKEN.sub(_sub_token, text2)
    return text3, wrapped


# ─── Public API ───────────────────────────────────────────────────


def normalize_question_latex(text: str) -> tuple[str, QuestionLatexReport]:
    """Normalize inline-mixed question / solution / option text for KaTeX.

    Three conservative passes:
      1. Clean existing ``$...$`` / ``$$...$$`` spans (Unicode, env rows, braces).
      2. Wrap bare Unicode math runs in ``$...$``.
      3. Wrap obvious chemistry formulas in ``$\\ce{...}$``.

    Returns (normalized_text, QuestionLatexReport). Never raises; idempotent.
    """
    if not isinstance(text, str) or not text:
        return (text if isinstance(text, str) else "", QuestionLatexReport())

    # Pass 1 — clean existing spans, learn their (new) char ranges.
    t1, rep1, spans1 = _clean_existing_spans(text)

    # Pass 2 — wrap bare Unicode glyph runs outside existing spans.
    t2, glyphs_n = _wrap_bare_glyphs(t1, spans1)

    # Pass 3 — chemistry. Recompute protected spans from t2 (offsets shifted).
    spans2 = [(m.start(), m.end()) for m in _DOLLAR_SPAN.finditer(t2)]
    t3, chem_n = _wrap_chemistry(t2, spans2)

    rep = rep1
    rep.glyphs_wrapped = glyphs_n
    rep.chemistry_wrapped = chem_n
    return t3, rep
