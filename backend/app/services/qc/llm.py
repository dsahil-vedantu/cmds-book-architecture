"""LLM QC — P6 auditor, runs on sections that failed local QC."""

from __future__ import annotations

import logging

from app.core.claude_client import extract_text, messages_create
from app.schemas.qc import LLMQCResult
from app.services.prompt_loader import load_raw
from app.utils.json_parse import parse_json

logger = logging.getLogger(__name__)

MAX_TOKENS = 1500


async def audit(
    *,
    source_text: str,
    extracted_text: str,
    local_failures: list[str],
) -> LLMQCResult:
    """Ask Claude to diagnose an extraction failure. Returns LLMQCResult."""
    system_prompt = load_raw("qc_auditor")

    failures_text = "\n".join(local_failures) if local_failures else "(none)"
    user_msg = (
        "Run QC analysis on this extraction.\n\n"
        "SOURCE_TEXT:\n"
        f"{source_text[:2500]}\n\n"
        "EXTRACTED_TEXT:\n"
        f"{extracted_text[:2000]}\n\n"
        "LOCAL QC FAILURES DETECTED:\n"
        f"{failures_text}\n\n"
        "Identify exactly what is wrong. Return the QC JSON."
    )

    response = await messages_create(
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = extract_text(response)
    data = parse_json(text)

    # The prompt returns {"pass": ..., ...} — pydantic alias handles this.
    return LLMQCResult.model_validate(data)


def issues_to_fix_text(result: LLMQCResult) -> str:
    """Build a fix-prompt string from LLM QC issues (used on re-extract)."""
    parts: list[str] = []
    issues = result.issues
    for item in issues.missing_content:
        parts.append(f"MISSING: {item}")
    for vd in issues.value_drift:
        parts.append(f'VALUE DRIFT: "{vd.source}" must stay as written')
    for item in issues.truncated:
        parts.append(f"TRUNCATED: complete this: {item}")
    for item in issues.added_content:
        parts.append(f"REMOVE: {item}")
    return "\n".join(parts) or "Re-extract carefully matching source exactly"
