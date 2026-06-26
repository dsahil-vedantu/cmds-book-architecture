"""Extract per-page plain text from a PDF via pymupdf.

Cached per-bytes so repeated calls within one QA run don't re-open the doc.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Dict

logger = logging.getLogger(__name__)


def extract_pages(pdf_bytes: bytes, page_start: int, page_end: int) -> Dict[int, str]:
    """Return ``{page_number_1_indexed: text}`` for the inclusive range.

    Missing / failed pages are silently skipped so the caller still gets a
    partial map rather than an exception.
    """
    out: Dict[int, str] = {}
    try:
        import pymupdf
    except Exception as e:  # pragma: no cover — pymupdf is a hard dep in prod
        logger.warning("pymupdf unavailable: %s", e)
        return out

    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        logger.warning("Could not open PDF: %s", e)
        return out

    try:
        total = len(doc)
        p0 = max(0, (page_start or 1) - 1)
        p1 = min(total - 1, (page_end or total) - 1)
        if p0 > p1:
            return out
        for p in range(p0, p1 + 1):
            try:
                txt = doc[p].get_text("text") or ""
            except Exception as e:
                logger.warning("page %s text extraction failed: %s", p + 1, e)
                txt = ""
            out[p + 1] = txt
        return out
    finally:
        try:
            doc.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Normalisation — used by all fidelity comparisons so trivial whitespace /
# unicode differences between PDF text and LLM output don't count as diffs.
# ---------------------------------------------------------------------------
_REPLACEMENTS = {
    "\u00a0": " ",   # nbsp
    "\u2009": " ",   # thin space
    "\u200b": "",    # zero-width space
    "\u2013": "-",   # en dash
    "\u2014": "-",   # em dash
    "\u2018": "'",   # curly single quotes
    "\u2019": "'",
    "\u201c": '"',   # curly double quotes
    "\u201d": '"',
    "\ufb01": "fi",  # common ligatures
    "\ufb02": "fl",
    # Math symbols → ascii so PDF unicode (×, ÷, π, …) compares equal to LLM
    # LaTeX (\\times, \\div, \\pi, …)
    "\u00d7": " times ",
    "\u00f7": " div ",
    "\u00b1": " pm ",
    "\u2212": "-",
    "\u2192": " to ",
    "\u21d2": " implies ",
    "\u2264": " leq ",
    "\u2265": " geq ",
    "\u2260": " neq ",
    "\u2248": " approx ",
    "\u221a": " sqrt ",
    "\u221e": " infty ",
    "\u03b1": " alpha ",
    "\u03b2": " beta ",
    "\u03b8": " theta ",
    "\u03c0": " pi ",
    "\u03bc": " mu ",
    "\u03bb": " lambda ",
    "\u03a3": " sum ",
    "\u2211": " sum ",
    "\u222b": " int ",
    "\u00b0": " deg ",
}


# LaTeX command names we collapse to a single ascii word so they match the
# unicode glyph after NFKC normalisation.
_LATEX_WORDS = {
    "times", "div", "pm", "to", "implies", "leq", "geq", "neq", "approx",
    "sqrt", "infty", "alpha", "beta", "theta", "pi", "mu", "lambda", "sum",
    "int", "deg", "frac", "cdot", "ldots", "dots", "left", "right",
    "begin", "end", "text", "mathrm",
}

_LATEX_CMD_RE = re.compile(r"\\([a-zA-Z]+)\s*")
_MATH_DELIM_RE = re.compile(r"\${1,2}")
_BRACE_RE = re.compile(r"[{}]")
_CARET_UNDERSCORE_RE = re.compile(r"[\^_]")


def _strip_latex(s: str) -> str:
    """Reduce LaTeX-flavoured math to a comparable ascii form."""

    def _cmd(m: re.Match[str]) -> str:
        name = m.group(1).lower()
        if name in _LATEX_WORDS:
            return f" {name} "
        return " "

    s = _MATH_DELIM_RE.sub(" ", s)
    s = _LATEX_CMD_RE.sub(_cmd, s)
    s = _BRACE_RE.sub(" ", s)
    # Drop ^ / _ without inserting whitespace so x^2 collapses to x2 and
    # matches the NFKC form of x² on the page.
    s = _CARET_UNDERSCORE_RE.sub("", s)
    return s


def normalise(s: str) -> str:
    """Lower-case, NFKC, strip LaTeX, collapse whitespace.

    Used for substring/ratio comparisons — not for storage. PDF text uses
    unicode math glyphs (×, ², π) while extracted text uses LaTeX (\\times,
    ^2, \\pi); both sides are reduced to a common ascii form here so the
    comparison reflects content equivalence, not encoding.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    for k, v in _REPLACEMENTS.items():
        s = s.replace(k, v)
    s = _strip_latex(s)
    s = s.lower()
    # collapse whitespace
    s = " ".join(s.split())
    return s
