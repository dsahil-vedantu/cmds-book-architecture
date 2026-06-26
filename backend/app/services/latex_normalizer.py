"""LaTeX normalization for equation blocks (Theory Worker Unit 4).

Single source of truth for "given an `eq` block's content, produce a string
the frontend will reliably render via KaTeX/MathJax."

Architecture (locked Q1-Q5):
  Q1 — eq blocks always wrap as ``$$...$$`` display mode.
  Q2 — Only ``eq`` blocks normalized here. ``p`` / ``kp`` / ``def`` content
       passes through unchanged (avoids false-positive math detection in prose).
  Q3 — Unicode math chars (², α, ∑, ≤, etc.) → LaTeX commands.
  Q4 — Edge brace stripping + interior repair when imbalance ≤2; severe
       imbalance leaves content as-is and flags telemetry.
  Q5 — Called from ``block_normalizer`` at the existing seam.

Properties:
  - Deterministic: same input → same output.
  - Idempotent: already-wrapped ``$$x$$`` input → same ``$$x$$`` output.
  - Safe: never raises; degraded inputs return best-effort + telemetry.

Frontend renders ``$$...$$`` as display math (centered, larger). KaTeX +
mhchem + amsmath cover virtually every textbook expression.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ─── Unicode → LaTeX map ──────────────────────────────────────────


# Superscripts / subscripts (Unicode → LaTeX). Multi-char sequences first.
_SUPERSCRIPTS = {
    "⁰": "^0", "¹": "^1", "²": "^2", "³": "^3", "⁴": "^4",
    "⁵": "^5", "⁶": "^6", "⁷": "^7", "⁸": "^8", "⁹": "^9",
    "⁺": "^+", "⁻": "^-", "⁼": "^=", "⁽": "^(", "⁾": "^)",
    "ⁿ": "^n", "ⁱ": "^i",
}
_SUBSCRIPTS = {
    "₀": "_0", "₁": "_1", "₂": "_2", "₃": "_3", "₄": "_4",
    "₅": "_5", "₆": "_6", "₇": "_7", "₈": "_8", "₉": "_9",
    "₊": "_+", "₋": "_-", "₌": "_=", "₍": "_(", "₎": "_)",
    "ₐ": "_a", "ₑ": "_e", "ₒ": "_o", "ₓ": "_x", "ₕ": "_h",
    "ₖ": "_k", "ₗ": "_l", "ₘ": "_m", "ₙ": "_n", "ₚ": "_p",
    "ₛ": "_s", "ₜ": "_t",
}

# Greek letters
_GREEK = {
    # lowercase
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta",
    "ε": r"\epsilon", "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta",
    "ι": r"\iota", "κ": r"\kappa", "λ": r"\lambda", "μ": r"\mu",
    "ν": r"\nu", "ξ": r"\xi", "ο": "o", "π": r"\pi",
    "ρ": r"\rho", "σ": r"\sigma", "τ": r"\tau", "υ": r"\upsilon",
    "φ": r"\phi", "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega",
    # uppercase
    "Α": "A", "Β": "B", "Γ": r"\Gamma", "Δ": r"\Delta",
    "Ε": "E", "Ζ": "Z", "Η": "H", "Θ": r"\Theta",
    "Ι": "I", "Κ": "K", "Λ": r"\Lambda", "Μ": "M",
    "Ν": "N", "Ξ": r"\Xi", "Ο": "O", "Π": r"\Pi",
    "Ρ": "P", "Σ": r"\Sigma", "Τ": "T", "Υ": r"\Upsilon",
    "Φ": r"\Phi", "Χ": "X", "Ψ": r"\Psi", "Ω": r"\Omega",
    # variants
    "ϵ": r"\varepsilon", "ϕ": r"\varphi", "ϑ": r"\vartheta",
}

# Math operators / symbols
_OPERATORS = {
    "∑": r"\sum", "∫": r"\int", "∏": r"\prod", "∂": r"\partial",
    "∇": r"\nabla", "√": r"\sqrt{}", "∞": r"\infty",
    "≤": r"\leq", "≥": r"\geq", "≠": r"\neq", "≈": r"\approx",
    "≡": r"\equiv", "∝": r"\propto", "∼": r"\sim",
    "±": r"\pm", "∓": r"\mp", "×": r"\times", "÷": r"\div",
    "·": r"\cdot", "⋅": r"\cdot", "∘": r"\circ", "•": r"\bullet",
    "∈": r"\in", "∉": r"\notin", "⊂": r"\subset", "⊃": r"\supset",
    "⊆": r"\subseteq", "⊇": r"\supseteq", "∪": r"\cup", "∩": r"\cap",
    "∅": r"\emptyset", "∀": r"\forall", "∃": r"\exists",
    "∧": r"\land", "∨": r"\lor", "¬": r"\neg",
    "→": r"\to", "←": r"\gets", "↔": r"\leftrightarrow",
    "⇒": r"\Rightarrow", "⇐": r"\Leftarrow", "⇔": r"\Leftrightarrow",
    "↑": r"\uparrow", "↓": r"\downarrow",
    "°": r"^{\circ}", "′": "'", "″": "''",
    "…": r"\ldots", "⋯": r"\cdots", "⋮": r"\vdots", "⋱": r"\ddots",
    # Number sets (blackboard bold)
    "ℝ": r"\mathbb{R}", "ℕ": r"\mathbb{N}", "ℤ": r"\mathbb{Z}",
    "ℚ": r"\mathbb{Q}", "ℂ": r"\mathbb{C}",
    # Geometry / misc
    "∠": r"\angle", "∥": r"\parallel", "⊥": r"\perp",
    "△": r"\triangle", "□": r"\square",
}

_UNICODE_MAP = {**_SUPERSCRIPTS, **_SUBSCRIPTS, **_GREEK, **_OPERATORS}


# ─── Wrapping detection / unwrapping ──────────────────────────────


# Match common LaTeX wrappers we want to strip before re-wrapping.
# Order matters: $$..$$ before $..$ (since $$ contains $).
_WRAP_PATTERNS = [
    # $$...$$ display
    re.compile(r"^\$\$(.+?)\$\$$", re.DOTALL),
    # $...$ inline
    re.compile(r"^\$(.+?)\$$", re.DOTALL),
    # \[...\] display
    re.compile(r"^\\\[(.+?)\\\]$", re.DOTALL),
    # \(...\) inline
    re.compile(r"^\\\((.+?)\\\)$", re.DOTALL),
]


def _strip_wrapper(s: str) -> tuple[str, bool]:
    """If s is wrapped in $$..$$ / $..$ / \\[..\\] / \\(..\\), return inner;
    else return (s, was_wrapped=False)."""
    s = s.strip()
    for pat in _WRAP_PATTERNS:
        m = pat.match(s)
        if m:
            return m.group(1).strip(), True
    return s, False


# ─── Unicode conversion ───────────────────────────────────────────


def _convert_unicode(s: str) -> tuple[str, int]:
    """Replace Unicode math chars with LaTeX equivalents.

    Returns (converted, count_of_substitutions).
    """
    count = 0
    out_chars: list[str] = []
    for ch in s:
        if ch in _UNICODE_MAP:
            out_chars.append(_UNICODE_MAP[ch])
            count += 1
        else:
            out_chars.append(ch)
    return ("".join(out_chars), count)


# ─── Brace repair ─────────────────────────────────────────────────


def _repair_braces(s: str) -> tuple[str, int, bool]:
    """Lightweight brace balance repair.

    1. Strip orphan ``{`` at start / orphan ``}`` at end (common Gemini
       artefact: wrapping an entire expression in unnecessary braces).
    2. If interior imbalance is 1 or 2, attempt to balance by appending/
       prepending the missing char. Larger imbalance → leave as-is.

    Returns (repaired, repair_count, repair_failed).
    """
    if not s:
        return s, 0, False

    repair_count = 0

    # Edge repair: strip orphan braces at the very edges only when they
    # don't have a matching counterpart in the body.
    while s.startswith("{") and s.count("{") > s.count("}"):
        s = s[1:].lstrip()
        repair_count += 1
    while s.endswith("}") and s.count("}") > s.count("{"):
        s = s[:-1].rstrip()
        repair_count += 1

    opens = s.count("{")
    closes = s.count("}")
    diff = abs(opens - closes)
    if diff == 0:
        return s, repair_count, False
    if diff > 2:
        # Too broken to safely repair — leave as-is, flag telemetry.
        return s, repair_count, True
    # Small imbalance — pad on the deficient side.
    if opens > closes:
        s = s + ("}" * (opens - closes))
    else:
        s = ("{" * (closes - opens)) + s
    repair_count += diff
    return s, repair_count, False


# ─── Public API ───────────────────────────────────────────────────


@dataclass
class LatexNormalizeReport:
    """Per-block telemetry from one normalize_latex call."""
    was_already_wrapped: bool = False
    unicode_converted: int = 0
    braces_repaired: int = 0
    braces_repair_failed: bool = False
    original_empty: bool = False


def normalize_latex(content: str, *, mode: str = "display") -> tuple[str, LatexNormalizeReport]:
    """Normalize an ``eq`` block's content for reliable rendering.

    Parameters
    ----------
    content : str
        Whatever Gemini emitted in ``eq.c`` — may already be wrapped, may
        contain Unicode math, may have stray braces.
    mode : str
        ``"display"`` (default) → wrap output in ``$$...$$`` for KaTeX
        display math. ``"inline"`` → wrap in ``$...$``. Other values are
        treated as display.

    Returns
    -------
    (normalized_content, LatexNormalizeReport)
    """
    report = LatexNormalizeReport()

    if not isinstance(content, str) or not content.strip():
        report.original_empty = True
        return ("", report)

    # 1) Strip any existing wrapper so we work on raw math content.
    inner, was_wrapped = _strip_wrapper(content.strip())
    report.was_already_wrapped = was_wrapped

    # 2) Convert Unicode math chars to LaTeX.
    inner, unicode_count = _convert_unicode(inner)
    report.unicode_converted = unicode_count

    # 3) Repair braces (edge strip + small interior balance).
    inner, repair_count, repair_failed = _repair_braces(inner)
    report.braces_repaired = repair_count
    report.braces_repair_failed = repair_failed

    inner = inner.strip()
    if not inner:
        # Content was effectively empty after stripping wrappers/braces.
        return ("", report)

    # 4) Re-wrap based on mode.
    delim = "$$" if mode != "inline" else "$"
    return (f"{delim}{inner}{delim}", report)
