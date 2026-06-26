"""Backend port of the frontend ``latexNormalize`` (frontend-v2/src/lib/
latexNormalize.ts). Wraps bare / partially-delimited LaTeX math fragments in
``$...$`` so the DOCX exporter's OMML path renders them — in paragraphs,
definitions, lists, AND table cells — matching the preview exactly.

CONSERVATIVE by design: only wraps runs carrying a real math signal
(sub/superscript, a LaTeX command, or an operator between operands); plain
prose passes through untouched. Idempotent — already-delimited ``$...$`` /
``\\ce{...}`` segments are preserved verbatim.
"""

from __future__ import annotations

import re

# Charset/signal include Greek (Ͱ-Ͽ) + math-unicode (× ÷ ± ≤ ≥ → ⇌ ∑ ∫ …)
# so well-formed expressions with those symbols are detected as math.
_MATH_CHARSET = re.compile(r"^[\\A-Za-z0-9^_{}()+\-=*/.,|<>\[\]'’°±·×÷Ͱ-Ͽ⁰-₟←-⇿∀-⋿]+$")
_SIGNAL = re.compile(r"[\\^_=+\-*/0-9{}±×÷←-⇿∀-⋿]")
_SHORT_ALPHA = re.compile(r"^[A-Za-z]{2,3}[.,;:]?$")
_OPER_END = re.compile(r"[+\-*/=^_]$")
_FRAG_MATH = re.compile(
    r"[\^_]|\\[a-zA-Z]+|[A-Za-z0-9Ͱ-Ͽ]\s*[+\-*/=]\s*[A-Za-z0-9\\Ͱ-Ͽ]"
    r"|\\(?:frac|sqrt|sum|int)|[±×÷←-⇿∀-⋿]"
)
# existing delimited / chem segments to preserve verbatim
_SEG = re.compile(r"\$\$[\s\S]*?\$\$|\$[^$]*\$|\\ce\{(?:[^{}]|\{[^{}]*\})*\}")


def _is_math_token(tok: str) -> bool:
    if not tok:
        return False
    # 3+ consecutive letters (ignoring \commands) = a word → never math.
    if re.search(r"[A-Za-z]{3,}", re.sub(r"\\[a-zA-Z]+", "", tok)):
        return False
    if re.fullmatch(r"[A-Za-z0-9Ͱ-Ͽ]", tok):  # single var / digit / Greek
        return True
    return bool(_MATH_CHARSET.match(tok)) and bool(_SIGNAL.search(tok))


def _frag_is_math(frag: str) -> bool:
    return bool(_FRAG_MATH.search(frag))


def _buf_ends_with_operator(buf: list[str]) -> bool:
    for t in reversed(buf):
        if t.isspace():
            continue
        return bool(_OPER_END.search(t))
    return False


def _wrap_fragments_in_prose(text: str) -> str:
    tokens = re.split(r"(\s+)", text)
    out: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        frag = "".join(buf)
        tail_m = re.search(r"\s+$", frag)
        tail = tail_m.group(0) if tail_m else ""
        frag_core = frag[: len(frag) - len(tail)]
        if frag_core and _frag_is_math(frag_core):
            out.append(f"${frag_core}$")
        else:
            out.append(frag_core)
        if tail:
            out.append(tail)
        buf = []

    def next_real(i: int) -> str:
        for j in range(i + 1, len(tokens)):
            if not tokens[j].isspace():
                return tokens[j]
        return ""

    for i, tok in enumerate(tokens):
        if tok.isspace() and tok != "":
            if buf:
                buf.append(tok)
            else:
                out.append(tok)
        elif (
            _is_math_token(tok)
            or (buf and _buf_ends_with_operator(buf) and bool(_SHORT_ALPHA.match(tok)))
            or (bool(_SHORT_ALPHA.match(tok)) and re.match(r"^[+\-*/=^_]", next_real(i)))
        ):
            buf.append(tok)
        else:
            flush()
            out.append(tok)
    flush()
    return "".join(out)


def normalize_latex(raw: str) -> str:
    """Wrap bare math fragments + bare ``\\ce{...}`` in ``$...$`` while keeping
    already-delimited segments verbatim. Returns text the OMML renderer can
    pick up via its ``$...$`` scan."""
    if not raw or not isinstance(raw, str):
        return raw or ""
    out: list[str] = []
    last = 0
    for m in _SEG.finditer(raw):
        out.append(_wrap_fragments_in_prose(raw[last:m.start()]))
        seg = m.group(0)
        out.append(f"${seg}$" if seg.startswith("\\ce") else seg)
        last = m.end()
    out.append(_wrap_fragments_in_prose(raw[last:]))
    return "".join(out)
