"""Block vocabulary + strict validator (Theory Worker Unit 2).

Single source of truth for converting Gemini's per-paragraph output into
the canonical block shape persisted to ``Section.blocks``. Replaces the
silent-drop behaviour in invariant_splitter.paragraphs_to_blocks.

Key architectural moves (locked Q1-Q5):

  Q1 — Unknown block types → coerce to ``"p"`` (preserve content). NO
       silent drops. Telemetry counts every coercion.
  Q2 — Empty-content blocks → drop, but log in NormalizationResult so
       QC can see it. No silent drops.
  Q3 — QC checks dropped-ratio (Unit 3 expands this). >20% dropped = QC
       fail. <=20% = log + continue.
  Q4 — Tables become LaTeX ``\\begin{tabular}`` blocks. Eliminates the
       "Gemini emitted headers as a string → per-character rows" bug
       entirely — no more parsing structured headers/rows.
  Q5 — Replaces invariant_splitter.paragraphs_to_blocks. Single source
       of truth at the seam between Gemini's response and Section.blocks.

Properties:
  - Deterministic: same paragraphs in → same blocks out.
  - Failure-safe: per-block exceptions are caught and counted; one bad
    block never kills the whole section.
  - Observable: every drop is logged with a reason.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ─── Type vocabulary ──────────────────────────────────────────────


# Canonical block types — exactly what gets written to Section.blocks.
# Each one has a dedicated validator below. Adding a new type requires
# adding (a) the canonical name here, (b) a validator function, (c) an
# entry in _VALIDATORS dispatch.
CANONICAL_TYPES = frozenset({
    "p",            # paragraph (body text)
    "h1", "h2", "h3",  # in-section sub-headings
    "kp",           # key point / takeaway / callout
    "eq",           # equation (LaTeX content)
    "def",          # definition (term + body)
    "list",         # bullet / numbered list
    "fig",          # figure (caption + body)
    "table",        # table (rendered as LaTeX tabular in `c`)
    "example",      # worked example (label + prob + eqs)
    # Reference chips inserted by the linker — valid output blocks too.
    "example_ref",
    "exercise_ref",
    "question_ref",
})

# Aliases that map to a canonical type. Lowercase + stripped key.
# Anything not in this map AND not in CANONICAL_TYPES is "unknown" and
# coerced to "p" with telemetry (Q1).
_TYPE_ALIASES: dict[str, str] = {
    # Paragraph family
    "body": "p",
    "paragraph": "p",
    "text": "p",
    "prose": "p",
    # Heading family — collapse all heading variants to h3 unless explicit
    "heading": "h3",
    "subheading": "h3",
    "sub_heading": "h3",
    "section_heading": "h3",
    # Key point / callout family
    "note": "kp",
    "callout": "kp",
    "key_point": "kp",
    "keypoint": "kp",
    "key_points": "kp",
    "remember": "kp",
    "tip": "kp",
    "smart_tip": "kp",
    "did_you_know": "kp",
    "fun_fact": "kp",
    "important": "kp",
    # Equation family
    "equation": "eq",
    "equations": "eq",
    "math": "eq",
    "math_block": "eq",
    "formula": "eq",
    "latex": "eq",
    "equation_inline": "eq",
    "equation_display": "eq",
    # List family — collected separately (see _collect_list_items)
    "list_item": "_list_item_marker_",
    "bullet": "_list_item_marker_",
    "bullet_list": "list",
    "ordered_list": "list",
    "unordered_list": "list",
    # Definition family
    "definition": "def",
    "def_block": "def",
    # Figure family
    "figure": "fig",
    "image": "fig",
    "diagram": "fig",
    "illustration_figure": "fig",
    # Table family
    "tabular": "table",
    # Example family
    "worked_example": "example",
    "solved_example": "example",
    "problem": "example",
    # Reference family (linker outputs)
    "ref": "example_ref",
}


def _resolve_type(raw: Any) -> tuple[str, bool]:
    """Map Gemini's `type`/`t` field to a canonical block type.

    Returns (canonical_type, was_coerced).
    - was_coerced=True means we had to fall back (unknown → "p" coerce).
    - "_list_item_marker_" is special — collected by the list pipeline.
    """
    if not isinstance(raw, str):
        return ("p", True)
    norm = raw.strip().lower()
    if norm in CANONICAL_TYPES:
        return (norm, False)
    if norm in _TYPE_ALIASES:
        canonical = _TYPE_ALIASES[norm]
        return (canonical, False)
    # Unknown — Q1 says coerce to "p" so content is preserved.
    return ("p", True)


# ─── Helpers ──────────────────────────────────────────────────────


_WS_RE = re.compile(r"\s+")


def _norm_text(v: Any) -> str:
    """Normalize text-like input. Strip + collapse internal whitespace
    but preserve original newlines (don't collapse them — important for
    multi-line equations and lists).
    """
    if v is None:
        return ""
    if not isinstance(v, str):
        v = str(v)
    return v.strip()


def _pick_content(block: dict) -> str:
    """Read content from any of the field names Gemini uses (`content`/`c`/`body`)."""
    for key in ("c", "content", "body", "text"):
        v = block.get(key)
        if v is not None and (not isinstance(v, str) or v.strip()):
            return _norm_text(v)
    return ""


def _ensure_list(v: Any) -> list:
    """Coerce a possibly-string value into a list.

    - list → list (unchanged)
    - string with newlines → split by newline (one row per line)
    - string with commas (no newlines) → split by comma
    - scalar → single-element list
    - None → empty list
    """
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        if "\n" in s:
            return [line.strip() for line in s.splitlines() if line.strip()]
        if "," in s:
            return [seg.strip() for seg in s.split(",") if seg.strip()]
        return [s]
    return [v]


# ─── LaTeX table builder ──────────────────────────────────────────


def _table_to_latex(block: dict) -> str:
    """Convert a Gemini table block to a LaTeX `\\begin{tabular}` string.

    Handles every shape Gemini emits in practice:
      - headers as list, rows as list-of-lists (canonical)
      - headers as string (comma- or newline-separated)
      - rows as string (newline-separated rows, comma-separated cells)
      - rows already containing strings (treated as single-cell rows)

    Output renders correctly in any LaTeX-aware frontend (MathJax/KaTeX with
    AMS-tabular extension, or any KaTeX rendering wrapper).
    """
    headers = _ensure_list(block.get("headers"))
    raw_rows = _ensure_list(block.get("rows"))

    # Coerce row cells. If a row is a string, split by comma. If it's a list, keep.
    normalized_rows: list[list[str]] = []
    for r in raw_rows:
        if isinstance(r, list):
            cells = [_norm_text(c) for c in r]
        elif isinstance(r, str):
            cells = _ensure_list(r)
            cells = [_norm_text(c) for c in cells]
        else:
            cells = [_norm_text(r)]
        normalized_rows.append(cells)

    # Determine column count from headers OR widest row
    ncols = max(
        len(headers),
        max((len(r) for r in normalized_rows), default=0),
    )
    if ncols == 0:
        return ""

    # Build the tabular spec — left-aligned columns with vertical bars
    spec = "|" + "|".join(["l"] * ncols) + "|"

    def fmt_row(cells: list[str]) -> str:
        # Pad short rows with empty cells; truncate over-long
        padded = list(cells[:ncols]) + [""] * max(0, ncols - len(cells))
        # Escape LaTeX special chars MINIMALLY (& and \) — caller content
        # may already contain LaTeX (e.g., $x^2$) which we want to preserve.
        escaped = []
        for cell in padded:
            cell = cell.replace("\\", "\\textbackslash{}").replace("&", "\\&")
            escaped.append(_norm_text(cell))
        return " & ".join(escaped) + " \\\\"

    lines: list[str] = []
    lines.append(f"\\begin{{tabular}}{{{spec}}}")
    lines.append("\\hline")
    if headers:
        lines.append(fmt_row([_norm_text(h) for h in headers]))
        lines.append("\\hline")
    for row in normalized_rows:
        lines.append(fmt_row(row))
    if normalized_rows:
        lines.append("\\hline")
    lines.append("\\end{tabular}")

    return "\n".join(lines)


# ─── Per-type validators ──────────────────────────────────────────


_PROSE_WORD_RE = __import__("re").compile(r"[A-Za-z]{3,}")
# Common math function/identifier names — don't count them as prose.
_MATH_WORDS = frozenset({
    "sin", "cos", "tan", "cot", "sec", "csc",
    "log", "ln", "exp", "lim", "max", "min",
    "sup", "inf", "arg", "det", "dim", "gcd", "lcm",
    "sinh", "cosh", "tanh", "coth",
    # Common LaTeX commands that survive normalization
    "text", "mathrm", "mathbf", "mathit", "frac", "sqrt", "cdot",
})

# Physics/chemistry unit tokens that signal a value statement (not a formula).
# When equation content has "=" + a unit at the end (e.g. "= 1.0078 u" or
# "= 931.5 MeV"), it's reading-out a value rather than describing a math
# relationship → render as prose, not as italic math.
_PHYSICS_UNITS_RE = __import__("re").compile(
    r"(?:^|[\s=\(])"
    r"(?:kg|g|km|cm|mm|nm|"
    r"J|MJ|kJ|eV|keV|MeV|GeV|"
    r"Hz|kHz|MHz|GHz|"
    r"Pa|kPa|MPa|atm|"
    r"°C|°F|"
    r"mV|kV|mA|μA|mW|kW|"
    r"kΩ|MΩ|"
    r"μF|nF|pF|"
    r"mol|amu|"
    r"Gy|Bq|Ci|Sv|"
    r"lm|lx|Wb|"
    r"u|rad|sr"
    r")"
    r"(?:[\s\.,\)/]|$)"
)
# Scientific notation pattern: "× 10^{-19}" or "x 10^N".
_SCI_NOTATION_RE = __import__("re").compile(r"[×x]\s*10\s*[\^\{]")


def _is_prose_heavy_eq(content: str) -> bool:
    """Detect if an `eq` block is actually prose (misclassified by Gemini).

    Three triggers:

    1. 3+ prose words (>=3 letters each, excluding math function names).
       Catches "The mass of a proton = 1.0078 u" (proton, mass, etc.).

    2. Scientific notation + physics unit pattern.
       Catches "1 u = 1.6605402 × 10^{-27} kg" — no prose words, but the
       sci-notation-with-units pattern is unmistakably a value statement.

    3. Equation contains `=` AND ends with (or has) a physics unit.
       Catches "E = 931.5 MeV" — short value statements that wouldn't
       trigger the prose-word heuristic.

    Any trigger → downgrade to `p` block so KaTeX doesn't render English
    letters as concatenated italic math vars ("Themassofaproton").
    """
    # Strip LaTeX delimiters for word counting
    stripped = content.strip().lstrip("$").rstrip("$").strip()

    # Trigger 1 — prose word count
    words = _PROSE_WORD_RE.findall(stripped)
    prose_words = [w for w in words if w.lower() not in _MATH_WORDS]
    if len(prose_words) >= 3:
        return True

    # Trigger 2 — scientific notation with units
    if _SCI_NOTATION_RE.search(stripped) and _PHYSICS_UNITS_RE.search(stripped):
        return True

    # Trigger 3 — equation with units AND a numeric value
    # Need both: "=" sign somewhere AND a physics unit AND a digit run.
    # This avoids triggering on pure-formula equations like "F = ma"
    # which lack numeric values and units.
    if "=" in stripped and _PHYSICS_UNITS_RE.search(stripped):
        if __import__("re").search(r"\d+\.\d+|\d{2,}", stripped):
            return True

    return False


def _validate_text_block(block: dict, canonical: str) -> dict | None:
    """For p / h1 / h2 / h3 / kp / eq — non-empty content required.

    Unit 4: ``eq`` blocks get LaTeX normalization via latex_normalizer
    (Unicode → LaTeX commands, $$...$$ display wrap, brace repair).
    Prose blocks (p/h*/kp) pass through unchanged.

    Unit 6: ``p`` and ``kp`` blocks are sniffed for solution / question
    bleed (start-of-content markers like "Soln.", "Step 1:", "Q.1") —
    those are misclassified Cat A content leaking into theory and are
    handled by the caller (see normalize_blocks for the counter + drop).

    Followup: prose-heavy `eq` blocks (e.g. "The mass of a proton = 1.0078 u")
    are downgraded to `p` blocks. KaTeX renders English letters in eq
    context as concatenated italic math vars — "Themassofaproton" — which
    is unreadable. Detected by counting 3+ letter English words.
    """
    content = _pick_content(block)
    if not content:
        return None
    if canonical == "eq":
        # Downgrade prose-heavy eq blocks to p (keep content readable in UI).
        if _is_prose_heavy_eq(content):
            # Strip the $$ wrapping; let KaTeX render any inline math via
            # the standard $...$ markers in the prose. MathMarkdown on the
            # frontend handles inline math in p blocks.
            cleaned = content.strip()
            if cleaned.startswith("$$") and cleaned.endswith("$$"):
                cleaned = cleaned[2:-2].strip()
            return {"t": "p", "c": cleaned}
        from app.services.latex_normalizer import normalize_latex
        normalized, report = normalize_latex(content, mode="display")
        if not normalized:
            return None
        out: dict = {"t": "eq", "c": normalized}
        # Attach per-block latex telemetry only when something interesting
        # happened (keeps the stored JSON small for the common case).
        if report.unicode_converted or report.braces_repaired or report.braces_repair_failed:
            out["_latex"] = {
                "u": report.unicode_converted,
                "b": report.braces_repaired,
                "bf": report.braces_repair_failed,
            }
        return out
    return {"t": canonical, "c": content}


def _validate_def(block: dict) -> dict | None:
    term = _norm_text(block.get("term"))
    content = _pick_content(block)
    if not term and not content:
        return None
    return {"t": "def", "term": term, "c": content}


def _validate_list(block: dict) -> dict | None:
    items = _ensure_list(block.get("items"))
    cleaned = [_norm_text(it) for it in items if _norm_text(it)]
    if not cleaned:
        return None
    return {"t": "list", "items": cleaned}


# Matches a "Figure X.Y" / "Fig 5.1a" / "Table 3.2" prefix at the start
# of a caption. Used to recover the label field when Gemini emitted the
# caption with the label inlined but forgot the separate "label" field.
_FIG_LABEL_PREFIX_RE = re.compile(
    r'^\s*((?:Figure|Fig\.?|Diagram|Plate|Table)\s+\d+(?:\.\d+)*[a-zA-Z]?)\b'
    r'\s*[:.\-—–]?\s*',
    re.IGNORECASE,
)


def _extract_label_from_caption(caption: str) -> tuple[str, str]:
    """Split 'Figure 6.5 Illustration of larynx...' into
    ('Figure 6.5', 'Illustration of larynx...').

    Returns ('', caption) if no label prefix detected.
    """
    if not caption:
        return ("", caption)
    m = _FIG_LABEL_PREFIX_RE.match(caption)
    if not m:
        return ("", caption)
    label = m.group(1).strip()
    rest = caption[m.end():].strip()
    return (label, rest)


def _validate_fig(block: dict) -> dict | None:
    """Validate a figure block.

    Output shape: {"t": "fig", "label"?: str, "caption"?: str, "c"?: str}.

    Defensive `label` recovery: the prompt was changed to ask for separate
    `label` and `caption` fields, but Gemini sometimes omits the `label`
    field even when the caption text begins with "Figure X.Y". Without
    `label`, the renderer can't link the inline placeholder to its Figure
    row → "Figure not available inline". This validator now:
      1. Preserves any `label` Gemini did emit (was being silently dropped
         — `_validate_fig` previously never read block.get("label")).
      2. If `label` is missing but caption starts with "Figure X.Y",
         splits the prefix off as the label.
    """
    label = _norm_text(block.get("label"))
    caption = _norm_text(block.get("caption"))
    content = _pick_content(block)

    # Recover missing label from caption prefix.
    if not label and caption:
        extracted, rest = _extract_label_from_caption(caption)
        if extracted:
            label = extracted
            caption = rest

    if not caption and not content and not label:
        return None
    out: dict = {"t": "fig"}
    if label:
        out["label"] = label
    if caption:
        out["caption"] = caption
    if content:
        out["c"] = content
    return out


def _validate_table(block: dict) -> dict | None:
    """Output a LaTeX tabular block (Q4 decision)."""
    latex = _table_to_latex(block)
    if not latex:
        return None
    caption = _norm_text(block.get("caption"))
    out: dict = {"t": "table", "c": latex}
    if caption:
        out["caption"] = caption
    return out


def _validate_example(block: dict) -> dict | None:
    label = _norm_text(block.get("label"))
    prob = _norm_text(block.get("prob") or block.get("problem"))
    eqs_raw = _ensure_list(block.get("eqs"))
    eqs = [_norm_text(e) for e in eqs_raw if _norm_text(e)]
    if not prob and not label and not eqs:
        return None
    return {
        "t": "example",
        "label": label,
        "prob": prob,
        "eqs": eqs,
    }


def _validate_ref(block: dict, canonical: str) -> dict | None:
    section_id = _norm_text(block.get("section_id"))
    label = _norm_text(block.get("label"))
    if not section_id and not label:
        return None
    out: dict = {"t": canonical}
    if section_id:
        out["section_id"] = section_id
    if label:
        out["label"] = label
    # Preserve any extra fields linker may attach (question_id, page, etc.)
    for k in ("question_id", "page", "kind"):
        v = block.get(k)
        if v is not None:
            out[k] = v
    return out


_VALIDATORS = {
    "p": lambda b: _validate_text_block(b, "p"),
    "h1": lambda b: _validate_text_block(b, "h1"),
    "h2": lambda b: _validate_text_block(b, "h2"),
    "h3": lambda b: _validate_text_block(b, "h3"),
    "kp": lambda b: _validate_text_block(b, "kp"),
    "eq": lambda b: _validate_text_block(b, "eq"),
    "def": _validate_def,
    "list": _validate_list,
    "fig": _validate_fig,
    "table": _validate_table,
    "example": _validate_example,
    "example_ref": lambda b: _validate_ref(b, "example_ref"),
    "exercise_ref": lambda b: _validate_ref(b, "exercise_ref"),
    "question_ref": lambda b: _validate_ref(b, "question_ref"),
}


# ─── Public API ───────────────────────────────────────────────────


@dataclass
class NormalizationResult:
    """Telemetry of a normalize_blocks() run."""
    blocks: list[dict] = field(default_factory=list)
    total_in: int = 0
    valid_out: int = 0
    type_coerced: int = 0           # unknown type → coerced to "p" (Q1)
    empty_dropped: int = 0           # block had no extractable content (Q2)
    malformed_dropped: int = 0       # per-type validator returned None
    # Unit 6 — solution / question bleed counters. Theory worker only
    # runs on Cat B sections; any solution or question marker at the
    # start of a p/kp body is Gemini misclassifying Cat A content as theory.
    dropped_solution_bleed: int = 0
    dropped_question_bleed: int = 0
    # Tracks how many individual `list_item` paragraphs were merged into
    # consolidated `list` blocks. NOT a drop — structural collapse.
    list_items_collapsed: int = 0
    unknowns_seen: dict[str, int] = field(default_factory=dict)
    drop_details: list[str] = field(default_factory=list)

    def dropped_ratio(self) -> float:
        """True drop ratio — excludes structural list-item collapse, which
        is normal consolidation, not loss."""
        if self.total_in == 0:
            return 0.0
        # Effective output = valid blocks + items collapsed into lists.
        # A list_item that becomes part of a `list` block is preserved,
        # not dropped, even though valid_out went down by len(items)-1.
        effective_out = self.valid_out + self.list_items_collapsed
        if effective_out > self.total_in:
            effective_out = self.total_in
        return (self.total_in - effective_out) / self.total_in

    def summary(self) -> str:
        parts = [
            f"in={self.total_in}",
            f"out={self.valid_out}",
        ]
        if self.type_coerced:
            parts.append(f"coerced={self.type_coerced}")
        if self.empty_dropped:
            parts.append(f"empty_dropped={self.empty_dropped}")
        if self.malformed_dropped:
            parts.append(f"malformed_dropped={self.malformed_dropped}")
        if self.dropped_solution_bleed:
            parts.append(f"solution_bleed={self.dropped_solution_bleed}")
        if self.dropped_question_bleed:
            parts.append(f"question_bleed={self.dropped_question_bleed}")
        if self.unknowns_seen:
            unk = ",".join(f"{k}:{v}" for k, v in self.unknowns_seen.items())
            parts.append(f"unknowns={{{unk}}}")
        return " ".join(parts)


def normalize_blocks(
    paragraphs: list[dict],
    *,
    section_title: str | None = None,
    section_id: str | None = None,
) -> NormalizationResult:
    """Convert Gemini's per-paragraph output to canonical Section.blocks.

    See module docstring. Telemetry is returned alongside the cleaned
    blocks so QC and logs can see exactly what happened.

    ``section_title`` (optional) is used by the Unit 6 solution-bleed
    sniffer for context-aware filtering: in Activity / ICT / Practical
    sections, "Step N:" prose is legitimate theory body (per prompt §4.4)
    and is NOT treated as bleed. Passing None falls back to strict mode
    (Step N → bleed everywhere).

    ``section_id`` (optional) is used to detect when normalize_blocks is
    being called on a Cat A bank section (Exercise N.M / Example N / etc.).
    When the section IS Cat A, any *_ref chip blocks Gemini emitted INSIDE
    the section are dropped — they're misclassified theory-extraction
    artefacts (chips belong in the PARENT theory section, inserted by
    the linker, NOT inside the Cat A bank itself).
    """
    result = NormalizationResult()

    if not isinstance(paragraphs, list):
        # Malformed at the top level — empty out.
        logger.warning(
            "normalize_blocks: expected list, got %s — returning empty",
            type(paragraphs).__name__,
        )
        return result

    # Detect whether THIS section being normalized is itself a Cat A bank
    # (Exercise N.M, Example N, Problem N, etc.). When yes, drop all *_ref
    # chip blocks that appear inside — they're Gemini OCR artefacts from
    # a misclassified theory extraction. The proper chips live in the
    # parent theory section, inserted by example_linker.
    section_is_cat_a = False
    if section_id:
        try:
            from app.services.example_linker import _split_question_id
            section_is_cat_a = _split_question_id(section_id) is not None
        except Exception:
            section_is_cat_a = False

    # First pass — collect list items separately (they collapse into one
    # `list` block per consecutive run, matching the old paragraphs_to_blocks
    # behaviour at invariant_splitter.py:55-58).
    pending_list: list[str] = []

    def flush_list():
        nonlocal pending_list
        if pending_list:
            result.blocks.append({"t": "list", "items": list(pending_list)})
            result.valid_out += 1
            pending_list = []

    for raw_block in paragraphs:
        result.total_in += 1
        if not isinstance(raw_block, dict):
            result.malformed_dropped += 1
            result.drop_details.append(
                f"non-dict paragraph at index {result.total_in - 1}: "
                f"{type(raw_block).__name__}"
            )
            continue

        raw_type_str = (raw_block.get("type") or raw_block.get("t") or "").strip()
        canonical, was_coerced = _resolve_type(raw_type_str)

        if was_coerced:
            result.type_coerced += 1
            if raw_type_str:
                result.unknowns_seen[raw_type_str] = (
                    result.unknowns_seen.get(raw_type_str, 0) + 1
                )

        # list_item marker — accumulate; don't emit yet. Collapsed list_items
        # ARE preserved in the final `list` block (just consolidated). The
        # ratio metric subtracts them from "drops" so the QC doesn't false-alarm.
        if canonical == "_list_item_marker_":
            item_text = _pick_content(raw_block)
            if item_text:
                pending_list.append(item_text)
                result.list_items_collapsed += 1
            else:
                result.empty_dropped += 1
                result.drop_details.append("empty list_item")
            continue

        # Any non-list block flushes the pending list buffer first
        if canonical != "list":
            flush_list()

        # Unit 6 — solution / question bleed filter. Only applies to
        # prose blocks (p, kp). Theory worker only runs on Cat B sections,
        # so any solution / question marker at start = Gemini misclassifying
        # Cat A content as theory body. Drop with telemetry.
        # Context-aware: "Step N:" in Activity/ICT/Practical sections is
        # legitimate theory body (per prompt §4.4), not bleed — handled
        # inside detect_solution_bleed via section_title.
        if canonical in ("p", "kp"):
            from app.services.solution_sniffer import (
                detect_solution_bleed,
                detect_question_bleed,
            )
            content_preview = _pick_content(raw_block)
            if content_preview:
                if detect_solution_bleed(content_preview, section_title=section_title):
                    result.dropped_solution_bleed += 1
                    result.drop_details.append(
                        f"solution_bleed: {content_preview[:60]!r}"
                    )
                    continue
                if detect_question_bleed(content_preview):
                    result.dropped_question_bleed += 1
                    result.drop_details.append(
                        f"question_bleed: {content_preview[:60]!r}"
                    )
                    continue

        # Defensive Cat A chip filter: when we're normalizing blocks for a
        # Cat A bank section, drop any *_ref chip blocks Gemini emitted
        # inside. They belong in the parent theory section (via the
        # example_linker), NOT inside the Cat A bank itself.
        if section_is_cat_a and canonical in ("question_ref", "exercise_ref", "example_ref"):
            result.malformed_dropped += 1
            result.drop_details.append(
                f"chip_in_cat_a: dropped {canonical} block inside Cat A section"
            )
            continue

        validator = _VALIDATORS.get(canonical)
        if validator is None:
            # Defensive — shouldn't happen because _resolve_type only
            # returns canonical types (or "_list_item_marker_" handled above).
            result.malformed_dropped += 1
            result.drop_details.append(
                f"no validator for canonical type {canonical!r}"
            )
            continue

        try:
            out = validator(raw_block)
        except Exception as e:
            logger.warning(
                "block_normalizer: validator %s raised on block %r: %s",
                canonical, raw_block, e,
            )
            result.malformed_dropped += 1
            result.drop_details.append(
                f"{canonical} validator raised: {e}"
            )
            continue

        if out is None:
            # Validator rejected — typically empty content
            result.empty_dropped += 1
            result.drop_details.append(f"empty {canonical} block")
            continue

        result.blocks.append(out)
        result.valid_out += 1

    # Final flush
    flush_list()

    # Post-pass: detect consecutive `p` blocks starting with "N." or "N)"
    # and collapse them into a single `list` block. Gemini sometimes emits
    # numbered points as separate paragraphs instead of `list_item`s —
    # this preserves the list structure.
    result.blocks = _collapse_numbered_paragraphs(result, result.blocks)

    return result


_NUM_PREFIX_RE = re.compile(r"^\s*(?P<num>\d+)[\.\)]\s+(?P<rest>\S.*)$", re.DOTALL)


def _collapse_numbered_paragraphs(
    result: NormalizationResult, blocks: list[dict]
) -> list[dict]:
    """Collapse runs of consecutive `p` blocks that look like a numbered
    list ("1. ...", "2. ...", "3. ...") into a single `list` block.

    Triggers only when:
      - 2+ consecutive p-blocks all match the numbered-prefix pattern
      - Their numbers form a strictly increasing sequence (1,2,3 or 2,3,4)
        — prevents collapsing unrelated "1. foo" / "1. bar" pairs

    Side effect: bumps result.list_items_collapsed by the number of items
    pulled into the new list (so QC drop-ratio stays correct).
    """
    out: list[dict] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if not (isinstance(b, dict) and b.get("t") == "p"):
            out.append(b)
            i += 1
            continue
        m = _NUM_PREFIX_RE.match(b.get("c") or "")
        if not m:
            out.append(b)
            i += 1
            continue
        # Try to grow a numbered run starting here
        items: list[str] = []
        nums: list[int] = []
        j = i
        while j < len(blocks):
            bb = blocks[j]
            if not (isinstance(bb, dict) and bb.get("t") == "p"):
                break
            mm = _NUM_PREFIX_RE.match(bb.get("c") or "")
            if not mm:
                break
            num = int(mm.group("num"))
            rest = mm.group("rest").strip()
            # Require strictly increasing sequence
            if nums and num != nums[-1] + 1:
                break
            items.append(rest)
            nums.append(num)
            j += 1
        # Only collapse if we found ≥2 numbered items in a sequence
        if len(items) >= 2:
            out.append({"t": "list", "items": items})
            # Each collapsed paragraph was a single body block; mark them as
            # "collapsed" so the QC drop-ratio metric stays accurate. The
            # original p blocks were counted in valid_out; subtract them
            # because they're now consolidated into one `list` block.
            result.list_items_collapsed += len(items) - 1
            i = j
        else:
            out.append(b)
            i += 1
    return out
