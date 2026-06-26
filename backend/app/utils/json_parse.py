"""Robust JSON extractor for Claude/Gemini responses that may include stray
prose, markdown fences, trailing commas, or — most importantly — raw LaTeX
inside string values.

Why this module is LaTeX-aware
------------------------------
Gemini Flash routinely emits LaTeX commands inside JSON string values, e.g.
``{"c": "$\\beta$ and \\frac{1}{2}"}``. The trouble is that ``\b \f \t \n \r``
are *valid* JSON escapes, so ``json.loads`` SUCCEEDS on ``"\beta"`` and silently
turns it into ``\x08eta`` (backspace + "eta"). The corruption is invisible to a
naive ``try json.loads`` because no exception is raised.

The fix here repairs the backslash escapes BEFORE ``json.loads`` runs, using a
LaTeX-command-aware scanner that distinguishes a LaTeX command from a genuine
control-char escape. See ``repair_json_latex_escapes`` for the policy.

The known-command sets are exported as module-level constants so the DB backfill
script can reverse already-corrupted data using the exact same source of truth.
"""

from __future__ import annotations

import json
import re
from typing import Any

# ---------------------------------------------------------------------------
# Known LaTeX command sets (single source of truth, shared with the backfill).
#
# Keyed by the FIRST letter after the backslash, because that first letter is
# what collides with a JSON control-char escape (\t \n \r \b \f). The maximal
# [a-zA-Z]+ run after the backslash is matched *exactly* against these sets.
# ---------------------------------------------------------------------------

# Commands beginning with t — collide with \t (tab, 0x09)
LATEX_T_COMMANDS: frozenset[str] = frozenset({
    "theta", "times", "to", "tau", "text", "textbf", "textit", "tan", "tanh",
    "tilde", "triangle", "top", "tfrac", "therefore", "trianglerighteq",
    "textrm", "textstyle", "Theta",
})

# Commands beginning with n — collide with \n (newline, 0x0a)
LATEX_N_COMMANDS: frozenset[str] = frozenset({
    "nu", "nabla", "neq", "ne", "nleq", "ngeq", "nmid", "nparallel",
    "nsubseteq", "not", "notin", "nwarrow", "nearrow", "ni", "nonumber", "Nu",
})

# Commands beginning with r — collide with \r (carriage return, 0x0d)
LATEX_R_COMMANDS: frozenset[str] = frozenset({
    "rho", "rightarrow", "rangle", "rfloor", "rceil", "rtimes", "rbrace",
    "rbrack", "rightleftharpoons", "varrho", "Rightarrow", "Rho",
})

# Words that are ALSO common English words (or non-commands) and therefore
# unsafe to treat as LaTeX when REVERSING a control char during backfill: a
# real newline before the English word "to"/"not" must stay a newline. These
# are excluded from the backfill reversal predicate. Verified against cmds.db:
# \x0a+"to" occurs 50x, \x0a+"not" 1x, \x09/\x0a+"as" 8x — all real whitespace.
LATEX_ENGLISH_AMBIGUOUS: frozenset[str] = frozenset({
    "to", "not", "ne", "ni", "as",
})

# Control char -> the backslash escape letter it was (wrongly) decoded from.
# Split into "always corruption" (these bytes are never legitimately present in
# educational text) and "ambiguous whitespace" (real tabs/newlines/CRs exist).
CTRL_ALWAYS_REVERSE: dict[str, str] = {
    "\x08": "b",   # backspace  -> \b   (\beta, \boxed, ...)
    "\x0c": "f",   # formfeed    -> \f   (\frac, \forall, ...)
    "\x0b": "v",   # vtab        -> \v   (\vec, \varphi, ...)
    "\x07": "a",   # bell        -> \a   (\alpha, \angle, ...)
}
CTRL_AMBIGUOUS_REVERSE: dict[str, tuple[str, frozenset[str]]] = {
    "\x09": ("t", LATEX_T_COMMANDS),   # tab     -> \t  only if t-command
    "\x0a": ("n", LATEX_N_COMMANDS),   # newline -> \n  only if n-command
    "\x0d": ("r", LATEX_R_COMMANDS),   # CR      -> \r  only if r-command
}

# JSON only recognises these escape characters after a backslash.
_VALID_JSON_ESCAPES = set('"\\/bfnrtu')

_HEX_RE = re.compile(r"[0-9a-fA-F]{4}")
_LETTERS_RE = re.compile(r"[a-zA-Z]+")


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", text)
        if text.endswith("```"):
            text = text[: -len("```")]
    return text.strip()


def _remove_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def repair_json_latex_escapes(text: str) -> str:
    """Repair invalid/ambiguous backslash escapes inside JSON string values so
    raw LaTeX survives ``json.loads`` as literal text.

    Policy, applied to every backslash inside a JSON string value:

    * ``\\\\`` (already-doubled) -> left untouched, advance past both (idempotent).
    * ``\\"`` ``\\/`` -> valid JSON escapes, kept as-is.
    * ``\\u`` + exactly 4 hex digits -> valid unicode escape, kept as-is.
    * ``\\u`` + letters NOT a hex escape (``\\underset``, ``\\uparrow``) -> LaTeX,
      backslash doubled.
    * ``\\b`` / ``\\f`` followed by a LETTER -> ALWAYS LaTeX (backspace/formfeed
      never legitimately precede a letter in text) -> backslash doubled.
    * ``\\t`` / ``\\n`` / ``\\r`` followed by letters -> AMBIGUOUS. The maximal
      ``[a-zA-Z]+`` run is matched *exactly* against the curated t/n/r command
      sets. On a match -> LaTeX -> backslash doubled. Otherwise the genuine
      control-char escape (real tab/newline/CR + following word) is preserved.
    * any other char after ``\\`` that is not a valid JSON escape (``\\p``,
      ``\\a``, ``\\,`` …) -> invalid JSON / LaTeX -> backslash doubled.

    Operates only inside string literals; structural backslashes do not occur
    outside strings in JSON, but the string-state tracking keeps us safe anyway.
    Idempotent: an already-doubled ``\\\\beta`` is skipped as a pair.
    """
    out: list[str] = []
    in_str = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if not in_str:
            if ch == '"':
                in_str = True
            out.append(ch)
            i += 1
            continue

        # inside a string
        if ch == '"':
            in_str = False
            out.append(ch)
            i += 1
            continue

        if ch != "\\":
            out.append(ch)
            i += 1
            continue

        # ch == "\\" — inspect the following char
        if i + 1 >= n:
            # dangling backslash at EOF — double it defensively
            out.append("\\\\")
            i += 1
            continue

        nxt = text[i + 1]

        # Already-escaped pair: keep both, advance past both (idempotency).
        if nxt == "\\":
            out.append("\\\\")
            i += 2
            continue

        # Always-valid simple escapes.
        if nxt in ('"', "/"):
            out.append("\\")
            out.append(nxt)
            i += 2
            continue

        # Unicode escape: \u + 4 hex digits is valid; otherwise it's LaTeX (\u…).
        if nxt == "u":
            if _HEX_RE.match(text, i + 2):
                out.append("\\u")
                i += 2
                continue
            # \underset, \uparrow, \upsilon … -> LaTeX
            out.append("\\\\")
            i += 1
            continue

        # \b / \f before a letter -> always LaTeX.
        if nxt in ("b", "f"):
            after = text[i + 2] if i + 2 < n else ""
            if after.isalpha():
                out.append("\\\\")
                i += 1
                continue
            # genuine \b / \f escape (rare in real data) — keep as valid escape
            out.append("\\")
            out.append(nxt)
            i += 2
            continue

        # \t / \n / \r -> ambiguous: maximal letter run must EXACTLY match a
        # known LaTeX command for us to treat it as LaTeX. Note the command
        # itself starts with t/n/r, so the run includes that first letter.
        if nxt in ("t", "n", "r"):
            m = _LETTERS_RE.match(text, i + 1)
            word = m.group(0) if m else ""
            cmd_set = {
                "t": LATEX_T_COMMANDS,
                "n": LATEX_N_COMMANDS,
                "r": LATEX_R_COMMANDS,
            }[nxt]
            if word in cmd_set:
                out.append("\\\\")
                i += 1
                continue
            # genuine control-char escape (\tab, \newline, \return) — keep valid
            out.append("\\")
            out.append(nxt)
            i += 2
            continue

        # Any other char after backslash is not a valid JSON escape -> LaTeX /
        # invalid. Double the backslash (\pi, \alpha, \ce, \Delta, \, …).
        out.append("\\\\")
        i += 1

    return "".join(out)


# Backwards-compatible alias for the old fallback name.
_escape_invalid_backslashes = repair_json_latex_escapes


def extract_json(text: str) -> str:
    """Return the first balanced JSON object or array substring in ``text``."""
    text = _strip_markdown_fences(text)
    start = None
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start is None:
        raise ValueError("No JSON object/array found in text")

    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    raise ValueError("Unbalanced JSON in text")


def parse_json(text: str) -> Any:
    """Best-effort parse of an LLM JSON response.

    Steps: strip fences -> trim to balanced braces -> repair LaTeX backslash
    escapes (so ``\\beta`` survives as literal text instead of decoding to a
    backspace) -> ``json.loads``. Trailing-comma tolerance is retained as a
    fallback.

    The LaTeX repair runs BEFORE ``json.loads`` (not as a post-exception
    fallback) because the corrupting inputs (\\b \\f \\t \\n \\r + LaTeX) parse
    *without* raising — the old fallback-only approach silently dropped them.
    """
    candidate = extract_json(text)
    repaired = repair_json_latex_escapes(candidate)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_remove_trailing_commas(repaired))
    except json.JSONDecodeError:
        pass
    # Last-ditch attempts on the un-repaired candidate (in case the repair
    # itself somehow produced invalid JSON for a pathological input).
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return json.loads(_remove_trailing_commas(candidate))


def reverse_control_char_corruption(text: str) -> tuple[str, int]:
    """Reverse the control-char corruption produced by the old buggy parser,
    turning decoded control characters back into their LaTeX backslash command.

    * ``\\x08``/``\\x0c``/``\\x0b``/``\\x07`` -> ``\\b``/``\\f``/``\\v``/``\\a``
      whenever followed by a letter (these bytes never legitimately appear in
      educational text before a letter).
    * ``\\x09``/``\\x0a``/``\\x0d`` (tab/newline/CR) -> ``\\t``/``\\n``/``\\r``
      ONLY when the maximal following ``[a-zA-Z]+`` run is a known LaTeX command
      AND not an English-ambiguous word (so real newlines before "to"/"not"
      stay newlines). Otherwise the genuine whitespace is left untouched.

    Returns ``(new_text, num_replacements)``. Idempotent: already-reversed text
    contains a literal backslash (not the control char) and is left alone.
    """
    if not text:
        return text, 0

    out: list[str] = []
    i = 0
    n = len(text)
    count = 0
    while i < n:
        ch = text[i]
        if ch in CTRL_ALWAYS_REVERSE:
            after = text[i + 1] if i + 1 < n else ""
            if after.isalpha():
                out.append("\\")
                out.append(CTRL_ALWAYS_REVERSE[ch])
                count += 1
                i += 1
                continue
            out.append(ch)
            i += 1
            continue
        if ch in CTRL_AMBIGUOUS_REVERSE:
            letter, cmd_set = CTRL_AMBIGUOUS_REVERSE[ch]
            m = _LETTERS_RE.match(text, i + 1)
            tail = m.group(0) if m else ""
            # The control char ATE the command's leading letter (\theta -> \t
            # consumed the "t", leaving "heta"). Reconstruct the full command as
            # letter + tail and match that against the set.
            word = letter + tail
            if word in cmd_set and word not in LATEX_ENGLISH_AMBIGUOUS:
                out.append("\\")
                out.append(letter)
                count += 1
                i += 1
                continue
            out.append(ch)
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out), count
