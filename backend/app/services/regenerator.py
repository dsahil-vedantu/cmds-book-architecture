"""P5 Regenerator — invariant split + post-regen value drift check.

Invariant blocks (eq, def, fig, example) are split out and copied verbatim.
Only free blocks (p, h3, kp, list) are sent to Claude. Results are merged
back in original positional order. Post-regen QC scans for numeric values
that drifted.
"""

from __future__ import annotations

import logging
import os

from app.core.gemini_client import extract_text, messages_create
from app.schemas.regen import PostRegenQCResult, RegenParams, param_descriptors
from app.services.invariant_splitter import (
    merge_blocks_in_order,
    paragraphs_to_blocks,
    split_blocks,
)
from app.services.prompt_loader import render


def _regen_prompt_name() -> str:
    """Pick which regenerator prompt file to load.

    Default = "regenerator" (v1, production). Set
    THEORY_REGEN_PROMPT_VERSION=v3 to use regenerator_v3.txt which has
    RECAP DIRECTIVES support (rename + key-points subsections).
    """
    version = (os.getenv("THEORY_REGEN_PROMPT_VERSION") or "v1").strip().lower()
    return "regenerator_v3" if version == "v3" else "regenerator"


def is_recap_enabled() -> bool:
    """Whether the prompt + worker pre-loop should apply recap logic."""
    return _regen_prompt_name() == "regenerator_v3"
from app.services.qc.helpers import blocks_to_plain_text, extract_numbers
from app.utils.json_parse import parse_json

logger = logging.getLogger(__name__)

MAX_TOKENS = 16000

# Block types copied verbatim during regeneration. This is the canonical
# INVARIANT_TYPES MINUS "def": definition bodies ARE rewritten (term kept,
# content rephrased), while equations, figures, examples, tables, and refs
# stay character-for-character identical.
from app.schemas.block import INVARIANT_TYPES as _INVARIANT_TYPES

REGEN_PROTECTED_TYPES: set[str] = _INVARIANT_TYPES - {"def"}


def free_blocks_to_text(free_blocks: list[dict]) -> str:
    """Flatten free blocks into the plain-text body used in the P5 user message.

    Each block is wrapped in explicit [BLOCK N type=X]…[/BLOCK N] markers so
    the LLM knows EXACTLY how many blocks to produce and what type each one
    must be. Without these markers the model frequently collapsed multiple
    body paragraphs into one, which caused the downstream merge to leave
    equation invariants visually clustered at the end of the section.

    Lists are still wrapped in [LIST]...[/LIST] inside the block so the model
    knows to output them back as list_item entries rather than folding into
    a body paragraph.
    """
    parts: list[str] = []
    for idx, b in enumerate(free_blocks, start=1):
        t = b.get("t")
        if t == "p":
            parts.append(
                f"[BLOCK {idx} type=body]\n{b.get('c', '')}\n[/BLOCK {idx}]"
            )
        elif t == "h3":
            parts.append(
                f"[BLOCK {idx} type=heading]\n{b.get('c', '')}\n[/BLOCK {idx}]"
            )
        elif t == "kp":
            parts.append(
                f"[BLOCK {idx} type=key_point]\n{b.get('c', '')}\n[/BLOCK {idx}]"
            )
        elif t == "def":
            # Definition: the term is the defined concept name and must be
            # preserved verbatim; only the body content is rewritten.
            term = b.get("term", "")
            parts.append(
                f'[BLOCK {idx} type=definition term="{term}"]\n'
                f"{b.get('c', '')}\n[/BLOCK {idx}]"
            )
        elif t == "list":
            items = b.get("items", []) or []
            numbered = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(items))
            parts.append(
                f"[BLOCK {idx} type=list]\n[LIST]\n{numbered}\n[/LIST]\n[/BLOCK {idx}]"
            )
    return "\n\n".join(p for p in parts if p.strip())


def build_regen_system_prompt(
    params: RegenParams,
    assigned_keypoints: list[str] | None = None,
) -> str:
    """Build the system prompt for one section's regen call.

    `assigned_keypoints` is the list of chapter-end PTR bullets the worker
    pre-assigned to THIS section. When non-empty (and v3 prompt active),
    the prompt renders a KEY POINTS directive.
    """
    return render(
        _regen_prompt_name(),
        **param_descriptors(params, assigned_keypoints=assigned_keypoints or []),
    )


def build_user_message(section_id: str, section_title: str, free_text: str) -> str:
    # Count the input [BLOCK N ...] markers so we can tell the LLM exactly
    # how many blocks it MUST produce. This stops the silent-omission bug
    # where the model collapsed 5 paragraphs into 2 and equations downstream
    # bunched at the end.
    block_count = free_text.count("[BLOCK ")
    return (
        "Regenerate the theory content for this section according to your system parameters.\n\n"
        f"SECTION ID: {section_id}\n"
        f"SECTION TITLE: {section_title}\n\n"
        "IMPORTANT RULES:\n"
        "- You are only receiving the REWRITABLE blocks (prose, key points, lists).\n"
        "- Equations, definitions, figures, and examples are handled separately — do NOT add or modify them.\n"
        f"- The input below contains EXACTLY {block_count} blocks tagged "
        "[BLOCK 1 type=…] through [BLOCK N type=…]. Your output JSON's \"paragraphs\" array MUST contain "
        f"EXACTLY {block_count} entries (or {block_count} entries with each list expanded into N list_item entries).\n"
        "- Map [BLOCK i type=body]   → output[i].type = \"body\"\n"
        "- Map [BLOCK i type=heading] → output[i].type = \"heading\"\n"
        "- Map [BLOCK i type=key_point] → output[i].type = \"key_point\"\n"
        "- Map [BLOCK i type=definition] → output[i].type = \"definition\" — keep the "
        "\"term\" EXACTLY as given (verbatim), rephrase ONLY the \"content\"; emit both "
        "\"term\" and \"content\" fields.\n"
        "- Map [BLOCK i type=list]   → emit one list_item block per numbered item (preserve count exactly).\n"
        "- DO NOT merge two consecutive body blocks into one. DO NOT skip any block.\n"
        "- Content wrapped in [LIST]...[/LIST] is a numbered list. Output each item as a separate "
        "\"list_item\" block — NEVER merge list items into a body paragraph.\n"
        "- [KEY POINT: ...] blocks must be output as \"key_point\" type.\n\n"
        "REWRITABLE CONTENT TO REGENERATE:\n"
        f"{free_text}\n\n"
        "Return the regenerated JSON now. Preserve block count and order exactly."
    )


async def regenerate_section(
    *,
    section_id: str,
    section_title: str,
    blocks: list[dict],
    params: RegenParams,
    assigned_keypoints: list[str] | None = None,
) -> list[dict]:
    """Regenerate one section with invariant split. Returns merged block list.

    assigned_keypoints: optional list of chapter-end PTR bullets that the
    worker pre-assigned to THIS section via the redistribute mechanism.
    Only consulted when the v3 prompt is active (env-var gated).
    """
    invariant_blocks, free_blocks = split_blocks(
        blocks, protected_types=REGEN_PROTECTED_TYPES
    )

    if not free_blocks:
        # Nothing to rewrite — return originals untouched
        return [dict(b) for b in blocks]

    system = build_regen_system_prompt(params, assigned_keypoints=assigned_keypoints)
    free_text = free_blocks_to_text(free_blocks)
    user_msg = build_user_message(section_id, section_title, free_text)

    response = await messages_create(
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = extract_text(response)
    data = parse_json(text)
    regen_paragraphs = list(data.get("paragraphs") or [])

    # Convert paragraphs → blocks, then KEEP ONLY rewritable types. Any
    # still-protected type (eq/fig/example/table/refs) Claude smuggled in is
    # silently dropped. "def" is rewritable here, so regenerated definitions
    # are retained.
    regen_blocks_all = paragraphs_to_blocks(regen_paragraphs)
    regen_free = [
        b for b in regen_blocks_all if b.get("t") not in REGEN_PROTECTED_TYPES
    ]

    return merge_blocks_in_order(
        blocks, regen_free, protected_types=REGEN_PROTECTED_TYPES
    )


def post_regen_qc(original_blocks: list[dict], regenerated_blocks: list[dict]) -> PostRegenQCResult:
    """Defensive QC on regenerated content. No extra LLM calls — purely
    structural/textual checks that catch the failure modes we've seen:

      1. Numerical value drift — every >1-char number in the original must
         appear unchanged in the regenerated text.
      2. Block count drift — the count of free blocks (p/h3/kp/list) in
         regenerated should match original. Mismatch means the LLM
         under- or over-produced (now structurally rare after R1.3 fixes,
         but we still log a warning).
      3. Word ratio drift — regenerated text length should stay within
         ~0.7×–1.3× of original (prompt requires ±15%; we're a bit looser
         here to avoid false positives on short sections).
    """
    from app.schemas.block import INVARIANT_TYPES

    orig_text = blocks_to_plain_text(original_blocks)
    regen_text_raw = blocks_to_plain_text(regenerated_blocks)
    regen_text = regen_text_raw.lower()

    # ── 1. Numerical drift ────────────────────────────────────────
    orig_numbers = extract_numbers(orig_text)
    drifted = [n for n in orig_numbers if len(n) > 1 and n.lower() not in regen_text]

    # ── 2. Block count drift ──────────────────────────────────────
    orig_free = sum(1 for b in original_blocks if b.get("t") not in INVARIANT_TYPES)
    regen_free = sum(1 for b in regenerated_blocks if b.get("t") not in INVARIANT_TYPES)
    warnings: list[str] = []
    if orig_free > 0 and regen_free != orig_free:
        warnings.append(
            f"Block count drift: original had {orig_free} free blocks, "
            f"regenerated has {regen_free}."
        )

    # ── 3. Word ratio drift ───────────────────────────────────────
    orig_words = len(orig_text.split())
    regen_words = len(regen_text_raw.split())
    word_ratio = regen_words / orig_words if orig_words > 0 else 1.0
    if orig_words > 50:  # only check meaningful sections (skip stub blocks)
        if word_ratio < 0.7:
            warnings.append(
                f"Length shrank to {int(word_ratio * 100)}% of original — possible truncation."
            )
        elif word_ratio > 1.3:
            warnings.append(
                f"Length expanded to {int(word_ratio * 100)}% of original — possible runaway."
            )

    if drifted:
        warnings.append(f"Numerical values changed: {', '.join(drifted[:5])}")

    return PostRegenQCResult(
        pass_=(len(drifted) == 0 and len(warnings) == 0),
        drifted_values=drifted,
        original_number_count=len(orig_numbers),
        block_count_original=orig_free,
        block_count_regenerated=regen_free,
        word_count_original=orig_words,
        word_count_regenerated=regen_words,
        word_ratio=round(word_ratio, 3),
        warnings=warnings,
    )
