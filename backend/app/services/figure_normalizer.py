"""Text normalization for figure-anchor matching.

Uses pylatexenc to convert LaTeX source → rendered Unicode text. Anchors
(captured from Gemini as rendered prose) and theory blocks (stored as
LaTeX source like `$\\overline{AB}$`) both pass through the same renderer,
so cross-format matching is automatic — no hand-curated Unicode↔LaTeX
mapping table needed.

Pipeline:
    LaTeX render → NFKD normalize → strip combining marks →
    strip sub/super markers (l_1 → l1) → strip narrative punctuation →
    lowercase → collapse whitespace.

The rendered form is the same form humans see in the textbook. Two
representations of "∠AOB is acute" (Unicode-glyph anchor + LaTeX-source
section block) both canonicalize to "angle aob is acute" (or "∠ aob is
acute" depending on pylatexenc's preference). Either way, both sides
agree.
"""

from __future__ import annotations

import re
import unicodedata

from pylatexenc.latex2text import LatexNodes2Text

# Singleton renderer — pylatexenc's LatexNodes2Text is thread-safe for
# the read-only methods we use. Constructed once at import.
_LATEX_RENDERER = LatexNodes2Text()

# Post-render Unicode math symbol → text-name mapping. pylatexenc renders
# `\angle` to `∠` and `\perp` to `⊥`, but the anchor side (captured from
# Gemini as visual prose) may have Unicode glyphs with different spacing
# than pylatexenc's spaced rendering (`∠AOB` anchor vs `∠ AOB` rendered).
# Mapping both sides to spaced-out text names ("angle", "perp") makes
# spacing/glyph differences moot.
_UNICODE_TO_NAME: dict[str, str] = {
    # Geometric / set
    "∠": "angle", "∆": "triangle", "△": "triangle",
    "∥": "parallel", "⊥": "perp",
    "∈": "in", "∉": "notin", "⊂": "subset", "⊃": "supset",
    "⊆": "subseteq", "⊇": "supseteq",
    "∪": "cup", "∩": "cap", "∀": "forall", "∃": "exists",
    "∅": "emptyset",
    # Greek lowercase
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta",
    "ε": "epsilon", "ζ": "zeta", "η": "eta", "θ": "theta",
    "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu",
    "ν": "nu", "ξ": "xi", "π": "pi", "ρ": "rho",
    "σ": "sigma", "τ": "tau", "υ": "upsilon", "φ": "phi",
    "χ": "chi", "ψ": "psi", "ω": "omega",
    # Greek uppercase
    "Γ": "Gamma", "Δ": "Delta", "Θ": "Theta", "Λ": "Lambda",
    "Ξ": "Xi", "Π": "Pi", "Σ": "Sigma", "Φ": "Phi", "Ψ": "Psi",
    "Ω": "Omega",
    # Comparison
    "≤": "leq", "≥": "geq", "≠": "neq", "≡": "equiv",
    "≈": "approx", "∼": "sim", "≅": "cong", "∝": "propto",
    # Arithmetic
    "×": "times", "÷": "div", "±": "pm", "∓": "mp",
    "⋅": "cdot", "·": "cdot",
    # Functions / operators
    "∑": "sum", "∏": "prod", "∫": "int", "∮": "oint",
    "√": "sqrt", "∂": "partial", "∇": "nabla",
    # Arrows
    "→": "to", "←": "gets", "↔": "leftrightarrow",
    "⇒": "Rightarrow", "⇐": "Leftarrow", "⇔": "Leftrightarrow",
    # Misc
    "∞": "infty", "°": "degree",
    # \circ LaTeX command renders to U+2218 ∘ (RING OPERATOR), distinct
    # from the U+00B0 ° (DEGREE SIGN) Gemini uses for the visual rendering.
    # Both must canonicalize to "degree" so "90°" anchor matches
    # "90^{\circ}" block.
    "∘": "degree",
}
_UNICODE_REPLACE = {ord(k): f" {v} " for k, v in _UNICODE_TO_NAME.items()}

# Multi-char ASCII patterns → text names. Applied BEFORE single-char
# mapping. Catches Gemini's ASCII renderings of LaTeX symbols (||, <=).
_ASCII_PATTERN_MAP: list[tuple[str, str]] = [
    ("\\|", " parallel "),       # LaTeX \| literal
    ("||", " parallel "),        # ASCII parallel
    ("<=", " leq "), (">=", " geq "), ("!=", " neq "),
    ("=>", " Rightarrow "), ("<->", " leftrightarrow "),
    ("->", " to "), ("<-", " gets "), ("~=", " approx "),
]

# Strip remaining subscript/superscript markers (l_1 → l1).
_SUBSUP_RE = re.compile(r"[_^]+")
# Narrative noise punctuation.
_NOISE_PUNCT_RE = re.compile(r"[:;,.!?\"'`‘’“”—–\-()\[\]*]+")
# Leading list-item numbering ("1. ", "(2) ", "3) ") at the start of the
# string. Figure anchors often include the visually-rendered numbering
# ("1. If x ≤ 90° ...") but the section's list block stores items without
# numbers ("If x ≤ 90° ..."). Stripping the leading prefix lets substring
# matching succeed.
_LIST_NUM_PREFIX_RE = re.compile(r"^\s*(?:\(\s*\d+\s*\)|\d+[.)])\s+")


def normalize_for_match(s: str) -> str:
    """Normalize text for anchor-to-section substring matching.

    Both LaTeX-source (theory block text) and Unicode-rendered (Gemini
    anchor) converge to the same canonical form.

    Examples:
        "Fig. 4.7"                   → "fig 4 7"
        "Line: A line is..."         → "line a line is..."
        "$\\overline{AB}$"           → "ab"
        "$l_1 \\perp l_2$"           → "l1 ⊥ l2"
        "$\\pi \\times r^2$"         → "π× r2"
        "l₁ ∥ l₂"                    → "l1 ∥ l2"
        "résumé"                     → "resume"
        ""                           → ""
        None                         → ""
    """
    if not s:
        return ""
    # Strip leading list-item numbering ("1. ", "(2) "). Anchors capture
    # numbered list rendering; section blocks store items without numbers.
    s = _LIST_NUM_PREFIX_RE.sub("", s)
    # Multi-char ASCII patterns first so "||" → "parallel" before single-char.
    for pat, name in _ASCII_PATTERN_MAP:
        s = s.replace(pat, name)
    # Render LaTeX → Unicode. \overline{X} → X, \pi → π, \perp → ⊥, etc.
    try:
        s = _LATEX_RENDERER.latex_to_text(s)
    except Exception:
        pass
    # Map any remaining Unicode math/Greek symbols to text names so
    # spacing/glyph differences between anchor and section don't matter.
    s = s.translate(_UNICODE_REPLACE)
    # NFKD: decomposes compatibility chars (² → 2 with super marker, etc.).
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    # Strip subscript/superscript markers — drop `l_1` → `l1`.
    s = _SUBSUP_RE.sub("", s)
    # Strip narrative noise punctuation.
    s = _NOISE_PUNCT_RE.sub(" ", s)
    return " ".join(s.lower().split())


__all__ = ["normalize_for_match"]
