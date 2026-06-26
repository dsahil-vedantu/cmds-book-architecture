"""Phase 3.5 — Final Draft export pipeline.

Renders a ``final_drafts.items`` array into a polished DOCX:
  1. Walk items in order → build rich markdown
  2. For figure items, write the figure's binary bytes (regen if approved,
     else original) to a temp dir and reference it by relative path
  3. Pipe markdown through pandoc with --standalone so it produces a
     proper Word document with native equations (OMML from $...$), real
     tables (from `| col |` pipes), and embedded image binaries.
  4. Return the DOCX bytes for the API to stream.

Synchronous — local testing scope. If exports get heavy we can move it
into a worker / job queue later.

The same render path also serves the Markdown and JSON exports — those
just skip the pandoc step.
"""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.figure import Figure
from app.models.final_draft import FinalDraft

# Figure-placeholder regex emitted by the question extractor:
#   {{fig: <label> — <caption>}}
# These placeholders mark inline figure positions in raw_text; the
# actual figure renders as a separate image block via
# embedded_figures, so the placeholder gets stripped from the visible
# text to avoid literal "{{fig: ...}}" bleeding into the export.
_FIG_PLACEHOLDER_RE = re.compile(r"\{\{\s*fig\s*:\s*[^}]+?\s*\}\}", re.IGNORECASE)


def _strip_fig_placeholders(text: str | None) -> str:
    if not text:
        return ""
    return _FIG_PLACEHOLDER_RE.sub("", text).rstrip()

logger = logging.getLogger(__name__)

_LIST_PREFIX_RE = re.compile(r"^\s*(?:\(\d+\)|\d+[.)])\s+")


# LaTeX commands that pandoc's OMML (Word equation) converter can't handle
# cleanly. Map them to Unicode equivalents so they render correctly in
# both inline ($...$) and display ($$...$$) math AND in surrounding text.
# Order matters: longer commands first so we don't half-match (e.g.
# \leftarrow before \left, \Rightarrow before \Right).
_TEX_TO_UNICODE: list[tuple[str, str]] = [
    # Therefore / because
    (r"\therefore", "∴"),
    (r"\because", "∵"),
    # Arrows
    (r"\Longleftrightarrow", "⟺"),
    (r"\Longrightarrow", "⟹"),
    (r"\Longleftarrow", "⟸"),
    (r"\Leftrightarrow", "⇔"),
    (r"\Rightarrow", "⇒"),
    (r"\Leftarrow", "⇐"),
    (r"\leftrightarrow", "↔"),
    (r"\rightarrow", "→"),
    (r"\leftarrow", "←"),
    (r"\implies", "⟹"),
    (r"\iff", "⟺"),
    (r"\to", "→"),
    (r"\mapsto", "↦"),
    # Comparison
    (r"\neq", "≠"),
    (r"\geq", "≥"),
    (r"\leq", "≤"),
    (r"\gg", "≫"),
    (r"\ll", "≪"),
    (r"\approx", "≈"),
    (r"\equiv", "≡"),
    (r"\sim", "∼"),
    (r"\propto", "∝"),
    # Set theory
    (r"\subseteq", "⊆"),
    (r"\supseteq", "⊇"),
    (r"\subset", "⊂"),
    (r"\supset", "⊃"),
    (r"\notin", "∉"),
    (r"\in", "∈"),
    (r"\cup", "∪"),
    (r"\cap", "∩"),
    (r"\emptyset", "∅"),
    (r"\varnothing", "∅"),
    # Logic
    (r"\forall", "∀"),
    (r"\exists", "∃"),
    (r"\lnot", "¬"),
    (r"\neg", "¬"),
    (r"\land", "∧"),
    (r"\lor", "∨"),
    # Math operators
    (r"\times", "×"),
    (r"\div", "÷"),
    (r"\pm", "±"),
    (r"\mp", "∓"),
    (r"\cdot", "·"),
    (r"\circ", "∘"),
    (r"\bullet", "•"),
    (r"\ast", "∗"),
    (r"\star", "⋆"),
    # Misc
    (r"\infty", "∞"),
    (r"\partial", "∂"),
    (r"\nabla", "∇"),
    (r"\Delta", "Δ"),
    (r"\alpha", "α"),
    (r"\beta", "β"),
    (r"\gamma", "γ"),
    (r"\delta", "δ"),
    (r"\epsilon", "ε"),
    (r"\theta", "θ"),
    (r"\lambda", "λ"),
    (r"\mu", "μ"),
    (r"\pi", "π"),
    (r"\sigma", "σ"),
    (r"\phi", "φ"),
    (r"\omega", "ω"),
    (r"\Omega", "Ω"),
    (r"\Sigma", "Σ"),
    (r"\Pi", "Π"),
    (r"\Lambda", "Λ"),
    (r"\Theta", "Θ"),
    (r"\Phi", "Φ"),
    (r"\Psi", "Ψ"),
    # Sums / integrals (OMML supports these but the unicode is cleaner)
    (r"\sum", "∑"),
    (r"\prod", "∏"),
    (r"\int", "∫"),
    (r"\oint", "∮"),
    # Misc
    (r"\ldots", "…"),
    (r"\cdots", "⋯"),
    (r"\quad", "  "),
    (r"\qquad", "    "),
]


def _normalise_latex(text: str) -> str:
    """Replace LaTeX commands that pandoc's OMML converter can't handle
    with Unicode equivalents. Applied to ALL text (inline + math) so the
    same character renders in body prose AND inside equations.

    Two passes:
      1) literal `\\command` → Unicode (the standard case)
      2) JSON-escape-collision recovery: in some questions the OCR
         JSON parse converted `\\t`/`\\n`/`\\r`/`\\b`/`\\f`/`\\v` to their
         control-character equivalents, leaving fragments like
         <TAB>herefore where `\\therefore` should be. Map those back.
    """
    if not text:
        return text
    # Pass 1 — literal backslash-command form.
    # The trailing group consumes an OPTIONAL EMPTY-BRACE PAIR `{}` (e.g.
    # `\theta{}` → θ) but MUST NOT swallow a lone `}` — otherwise a symbol
    # command sitting just before an enclosing brace, like the \theta in
    # `\frac{180-2\theta}{2}`, would eat \frac's closing brace and corrupt the
    # whole fraction (\frac{180-2θ{2} → OMML fails → lossy raw fallback).
    for cmd, unicode_char in _TEX_TO_UNICODE:
        pattern = re.escape(cmd) + r"(?![A-Za-z])\s?(?:\{\})?"
        text = re.sub(pattern, unicode_char, text)
    # Pass 2 — JSON-escape collision recovery. Each tuple is
    # (control_char + tail, replacement). Control chars come from:
    #   \t=09 \n=0A \r=0D \b=08 \f=0C \v=0B
    _COLLISIONS: list[tuple[str, str]] = [
        # \t-prefixed
        ("\therefore", "∴"),
        ("\times", "×"),
        ("\theta", "θ"),
        ("\to", "→"),
        # \n-prefixed (we lose newlines but keep math semantic)
        ("\notin", "∉"),
        ("\neq", "≠"),
        ("\nabla", "∇"),
        # \r-prefixed
        ("\rightarrow", "→"),
        ("\rho", "ρ"),
        # \b-prefixed
        ("\beta", "β"),
        ("\because", "∵"),
        # \f-prefixed  (NOTE: \frac is too rich to handle here; leave for pandoc)
        ("\forall", "∀"),
        # \v-prefixed
        ("\varnothing", "∅"),
    ]
    for needle, replacement in _COLLISIONS:
        if needle in text:
            text = text.replace(needle, replacement)
    return text


# ---------------------------------------------------------------------------
# Block / figure / question → Markdown
# ---------------------------------------------------------------------------

def _block_to_md(block: dict[str, Any]) -> str:
    t = block.get("t")
    c = block.get("c") or ""
    if t == "p":
        return c
    if t == "h3":
        return f"### {c}"
    if t == "eq":
        return f"$${c}$$"
    if t == "def":
        term = block.get("term") or ""
        return f"**Definition — {term}**\n\n{c}"
    if t == "kp":
        return f"> **Key Point**\n>\n> {c}"
    if t == "list":
        items = block.get("items") or []
        lines: list[str] = []
        for i, it in enumerate(items):
            clean = _LIST_PREFIX_RE.sub("", str(it))
            lines.append(f"{i+1}. {clean}")
        return "\n".join(lines)
    if t == "table":
        headers = block.get("headers") or []
        rows = block.get("rows") or []
        caption = block.get("caption") or ""
        parts: list[str] = []
        if caption:
            parts.append(f"*{caption}*")
        if headers:
            parts.append("| " + " | ".join(headers) + " |")
            parts.append("|" + "|".join(["---"] * len(headers)) + "|")
        for row in rows:
            parts.append("| " + " | ".join(str(x) for x in row) + " |")
        return "\n".join(parts)
    if t == "example":
        label = block.get("label") or "Example"
        prob = block.get("prob") or ""
        eqs = block.get("eqs") or []
        out = [f"**{label}**"]
        if prob:
            out.append(prob)
        for e in eqs:
            out.append(f"$${e}$$")
        return "\n\n".join(out)
    if t == "fig":
        # Seeder drops fig BLOCKS when a matching figure ITEM exists at
        # the same position. A fig block that reaches this renderer
        # means the embedder couldn't link a figure here — emit a muted
        # placeholder line so the markdown export shows that a figure
        # was expected at this spot, rather than a silent gap.
        label = block.get("label") or ""
        if label or c:
            return f"*[Figure placeholder: {label or c}]*"
        return ""
    if t in ("example_ref", "exercise_ref", "question_ref"):
        # A3 fix — see docx_export.py: suppress unmatched chips in the
        # exported document. Matched chips already get their question
        # inlined upstream; orphans / over-extraction garbage clutter
        # the output (e.g. Shortcuts polluted with 74 empty chips).
        return ""
    return c or ""


def _figure_to_md(label: str, caption: str, relative_path: str) -> str:
    alt = label or caption or "figure"
    parts = [f"![{alt}]({relative_path})"]
    if caption:
        parts.append(f"*{caption}*")
    return "\n\n".join(parts)


def _question_to_md(q: dict[str, Any], figure_paths: dict[str, str]) -> str:
    """Render a question's body + embedded figures + solution. The
    embedded figures' paths come from ``figure_paths`` keyed by figure_id."""
    parts: list[str] = []
    header_bits: list[str] = []
    if q.get("question_number"):
        header_bits.append(f"Q{q['question_number']}")
    if q.get("page_start"):
        header_bits.append(f"p.{q['page_start']}")
    if q.get("question_type"):
        header_bits.append(q["question_type"])
    if header_bits:
        parts.append("**" + " · ".join(header_bits) + "**")
    if q.get("raw_text"):
        parts.append(_strip_fig_placeholders(q["raw_text"]))
    for f in q.get("embedded_figures") or []:
        fp = figure_paths.get(str(f.get("figure_id")))
        if fp:
            parts.append(_figure_to_md(f.get("label") or "", f.get("caption") or "", fp))
    if q.get("has_solution") and q.get("solution_text"):
        parts.append(f"**Solution**\n\n{q['solution_text']}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Figure-binary materialisation (so pandoc can embed images)
# ---------------------------------------------------------------------------

async def _materialise_figures(
    session: AsyncSession,
    items: list[dict[str, Any]],
    work_dir: Path,
) -> dict[str, str]:
    """Collect figure_ids referenced by ``items`` (top-level figure items
    AND embedded_figures inside question items), load their image bytes,
    write each to ``work_dir`` as ``<id>.png``. Returns a {figure_id_str:
    relative_filename} map for use in markdown rendering.

    Variant choice: regen if approved, else original."""
    fig_ids: set[str] = set()
    for it in items:
        t = it.get("type")
        if t == "figure":
            f = it.get("figure") or {}
            if f.get("figure_id"):
                fig_ids.add(str(f["figure_id"]))
        elif t == "question":
            q = it.get("question") or {}
            for f in q.get("embedded_figures") or []:
                if f.get("figure_id"):
                    fig_ids.add(str(f["figure_id"]))
    if not fig_ids:
        return {}

    rows = (
        await session.execute(
            select(Figure).where(Figure.id.in_([UUID(x) for x in fig_ids]))
        )
    ).scalars().all()

    out: dict[str, str] = {}
    for row in rows:
        # variant=regen if approved, else original
        data: bytes | None = None
        if row.regen_image_bytes and row.approved_at is not None:
            data = row.regen_image_bytes
        else:
            data = row.image_bytes
        if not data:
            continue
        fname = f"{row.id.hex}.png"
        (work_dir / fname).write_bytes(data)
        out[str(row.id)] = fname
    return out


# ---------------------------------------------------------------------------
# Top-level: items → markdown
# ---------------------------------------------------------------------------

def items_to_markdown(
    book_title: str,
    items: list[dict[str, Any]],
    figure_paths: dict[str, str],
) -> str:
    """Walk items in order, render each to markdown. Section headings get
    `#`-prefix sized by the item's level (capped at h6)."""
    parts: list[str] = [f"# {book_title or 'Book'}"]
    for it in items:
        t = it.get("type")
        if t == "section_heading":
            level = max(1, min(6, int(it.get("level") or 1) + 1))
            parts.append(f"{'#' * level} {it.get('title') or it.get('section_id') or ''}")
        elif t == "block":
            parts.append(_block_to_md(it.get("block") or {}))
        elif t == "figure":
            f = it.get("figure") or {}
            fp = figure_paths.get(str(f.get("figure_id")))
            if fp:
                parts.append(_figure_to_md(f.get("label") or "", f.get("caption") or "", fp))
        elif t == "question":
            parts.append(_question_to_md(it.get("question") or {}, figure_paths))
        elif t == "custom_text":
            parts.append(it.get("content") or "")
    return "\n\n".join(p for p in parts if p) + "\n"


# ---------------------------------------------------------------------------
# Pandoc plumbing (mirrors final_merge.py)
# ---------------------------------------------------------------------------

class ExportError(RuntimeError):
    pass


def _ensure_pandoc_on_path() -> str:
    found = shutil.which("pandoc")
    if found:
        return found
    for p in [
        Path.home() / ".local/bin/pandoc",
        Path("/opt/homebrew/bin/pandoc"),
        Path("/usr/local/bin/pandoc"),
    ]:
        if p.exists() and p.is_file():
            return str(p)
    raise ExportError("pandoc binary not found — install pandoc to enable DOCX export")


def _run_pandoc_to_docx(md: str, work_dir: Path) -> bytes:
    _ensure_pandoc_on_path()  # raises with a clear message if missing
    try:
        import pypandoc
    except ImportError as e:
        raise ExportError(
            "pypandoc not installed — install pypandoc to enable DOCX export"
        ) from e

    md_path = work_dir / "draft.md"
    md_path.write_text(md, encoding="utf-8")
    out_path = work_dir / "draft.docx"
    pypandoc.convert_file(
        str(md_path),
        "docx",
        format="md",
        outputfile=str(out_path),
        extra_args=[
            "--standalone",
            f"--resource-path={work_dir}",
        ],
    )
    return out_path.read_bytes()


# ---------------------------------------------------------------------------
# Public API: build the three export payloads
# ---------------------------------------------------------------------------

async def build_draft_json(draft: FinalDraft, book_title: str) -> bytes:
    """JSON export — the draft items as-is for downstream tools."""
    payload = {
        "book_title": book_title,
        "draft_id": str(draft.id),
        "exported_at": None,  # caller sets headers etc.
        "items": draft.items or [],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


async def build_draft_markdown(
    session: AsyncSession,
    draft: FinalDraft,
    book_title: str,
) -> bytes:
    """Markdown export — fully rendered, with figure paths pointing at
    the backend image URLs (so the .md is shareable without binaries).
    For DOCX, use ``build_draft_docx`` which embeds binaries."""
    items = draft.items or []
    # For MD-only, reference figure URLs (not local paths)
    figure_paths: dict[str, str] = {}
    for it in items:
        if it.get("type") == "figure":
            f = it.get("figure") or {}
            fid = str(f.get("figure_id") or "")
            url = f.get("image_url") or ""
            if fid:
                figure_paths[fid] = url
        elif it.get("type") == "question":
            for f in (it.get("question") or {}).get("embedded_figures") or []:
                fid = str(f.get("figure_id") or "")
                url = f.get("image_url") or ""
                if fid:
                    figure_paths[fid] = url
    md = items_to_markdown(book_title, items, figure_paths)
    md = _normalise_latex(md)
    return md.encode("utf-8")


async def build_draft_docx(
    session: AsyncSession,
    draft: FinalDraft,
    book_title: str,
) -> bytes:
    """DOCX export — routes draft items through the same polished
    python-docx pipeline that the theory + questions exports use
    (``app.services.docx_export``). Gets us consistent formatting:
    same heading styles, paragraph spacing, MCQ option layout, key-point
    boxes, solution typography. Figures embed as binary (variant=regen
    if approved, else original). Math/symbols get the Unicode
    normalisation the underlying builder already applies in prose; the
    LaTeX-collision recovery still runs here for solution_text fields
    that came in with ``\\therefore`` → TAB+"herefore" corruption.
    """
    items = draft.items or []
    # Load figure binaries keyed by figure_id (str)
    fig_ids: set[str] = set()
    for it in items:
        t = it.get("type")
        if t == "figure":
            fid = (it.get("figure") or {}).get("figure_id")
            if fid:
                fig_ids.add(str(fid))
        elif t == "question":
            for f in (it.get("question") or {}).get("embedded_figures") or []:
                fid = f.get("figure_id")
                if fid:
                    fig_ids.add(str(fid))
    figure_bytes_map: dict[str, bytes] = {}
    if fig_ids:
        from app.models.figure import Figure
        rows = (
            await session.execute(
                select(Figure).where(Figure.id.in_([UUID(x) for x in fig_ids]))
            )
        ).scalars().all()
        for row in rows:
            data = (
                row.regen_image_bytes
                if (row.regen_image_bytes and row.approved_at is not None)
                else row.image_bytes
            )
            if data:
                figure_bytes_map[str(row.id)] = data

    # Repair LaTeX-escape collisions on prose / solution text BEFORE the
    # builder renders them (the builder's own normaliser handles the
    # standard `\\command` forms; the collision recovery is composer-
    # specific because it's tied to how question OCR JSON was parsed).
    normalised_items: list[dict[str, Any]] = []
    for it in items:
        if it.get("type") == "block":
            block = dict(it.get("block") or {})
            for k in ("c", "term"):
                if isinstance(block.get(k), str):
                    block[k] = _normalise_latex(block[k])
            if "items" in block and isinstance(block["items"], list):
                block["items"] = [
                    _normalise_latex(x) if isinstance(x, str) else x
                    for x in block["items"]
                ]
            normalised_items.append({**it, "block": block})
        elif it.get("type") == "question":
            q = dict(it.get("question") or {})
            for k in ("raw_text", "solution_text"):
                if isinstance(q.get(k), str):
                    q[k] = _normalise_latex(q[k])
            normalised_items.append({**it, "question": q})
        elif it.get("type") == "custom_text":
            content = it.get("content") or ""
            normalised_items.append({**it, "content": _normalise_latex(content)})
        else:
            normalised_items.append(it)

    from app.services.docx_export import build_final_draft_docx
    return build_final_draft_docx(book_title, normalised_items, figure_bytes_map)
