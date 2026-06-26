"""LaTeX → OMML (Office Math) for native Word equations in DOCX export.

Pipeline: latex2mathml (LaTeX → MathML) → mathml2omml (MathML → OMML) →
lxml element ready to append into a python-docx paragraph's CT_P.

Design rule: NEVER raise. Any conversion failure returns None so the caller
falls back to the legacy unicode-approximation text run — a degraded but
non-crashing render. This keeps DOCX export robust against the long tail of
exotic LaTeX the converters don't support.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"

# Lazy imports so a missing dep degrades gracefully instead of breaking import.
try:
    from latex2mathml.converter import convert as _latex_to_mathml  # type: ignore
    from mathml2omml import convert as _mathml_to_omml  # type: ignore
    from docx.oxml import parse_xml  # type: ignore
    _AVAILABLE = True
except Exception as _e:  # pragma: no cover
    logger.warning("latex_omml: native-equation deps unavailable (%s)", _e)
    _AVAILABLE = False


def latex_to_omml_element(latex: str) -> Any | None:
    """Convert a LaTeX fragment to an OMML ``<m:oMath>`` lxml element, or
    None if it can't be converted (caller renders fallback text)."""
    if not _AVAILABLE:
        return None
    s = (latex or "").strip()
    if not s:
        return None
    # Strip surrounding $ / $$ if a caller passed delimited text.
    if s.startswith("$$") and s.endswith("$$"):
        s = s[2:-2].strip()
    elif s.startswith("$") and s.endswith("$"):
        s = s[1:-1].strip()
    if not s:
        return None
    try:
        mathml = _latex_to_mathml(s)
        omml = _mathml_to_omml(mathml)
        if not omml or "<m:oMath" not in omml:
            return None
        # mathml2omml 0.0.2 bug: the <m:groupChrPr> property element used for
        # accents (\vec, \bar, \overrightarrow, \overleftrightarrow) is emitted
        # closed with the WRONG tag — `</m:groupChr>` instead of
        # `</m:groupChrPr>` — so the XML is malformed, parsing fails, and vector
        # notation (~7% of real math in vector chapters) silently falls back to
        # text. Rewrite that mis-emitted close tag; the lookahead excludes a
        # correct `</m:groupChrPr>` so well-formed output is a no-op.
        omml = re.sub(
            r"(<m:groupChrPr>(?:(?!</m:groupChr>|</m:groupChrPr>).)*?)</m:groupChr>",
            r"\1</m:groupChrPr>",
            omml,
            flags=re.DOTALL,
        )
        # The OMML uses the `m:` prefix; declare it so the fragment parses
        # standalone (python-docx merges it into document.xml where `m` is
        # already declared on the root).
        if omml.lstrip().startswith("<m:oMath>"):
            omml = omml.replace("<m:oMath>", f'<m:oMath xmlns:m="{_M_NS}">', 1)
        return parse_xml(omml)
    except Exception as e:
        logger.debug("latex_to_omml failed for %r: %s", s[:60], e)
        return None
