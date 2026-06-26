"""Content-hash cache for figure regenerations.

Idempotency rule: given the same (source_bytes, style, custom_instructions,
watermark_clean, overlay, model, prompt_version), the regenerated output is
considered identical and is reused from cache instead of paying for another
Gemini round-trip.

The cache is just a lookup against the `figures` table itself —
`source_hash` and `regen_cache_key` are persisted on every regen.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


# Bump this when the prompt files change to invalidate every cached entry.
PROMPT_VERSION = "v1.0"


def _prompt_hash() -> str:
    """sha256 of all prompts in `prompts/v1/figures/`, used as part of the
    cache key so a prompt edit invalidates cached results.
    """
    prompt_dir = Path(__file__).resolve().parents[3] / "prompts" / "v1" / "figures"
    h = hashlib.sha256()
    for p in sorted(prompt_dir.glob("*.txt")):
        h.update(p.name.encode("utf-8"))
        h.update(p.read_bytes())
    h.update(PROMPT_VERSION.encode("utf-8"))
    return h.hexdigest()


def source_hash(image_bytes: bytes) -> str:
    """sha256 of the source image."""
    return hashlib.sha256(image_bytes).hexdigest()


def cache_key(
    *,
    source_bytes: bytes,
    style: str,
    custom_instructions: str | None,
    watermark_clean: bool,
    overlay: bool,
    model: str,
) -> str:
    """Composite cache key — same inputs always produce the same key."""
    h = hashlib.sha256()
    h.update(source_hash(source_bytes).encode("utf-8"))
    h.update(b"|style=")
    h.update(style.encode("utf-8"))
    h.update(b"|custom=")
    h.update((custom_instructions or "").strip().encode("utf-8"))
    h.update(b"|watermark=")
    h.update(b"1" if watermark_clean else b"0")
    h.update(b"|overlay=")
    h.update(b"1" if overlay else b"0")
    h.update(b"|model=")
    h.update(model.encode("utf-8"))
    h.update(b"|prompts=")
    h.update(_prompt_hash().encode("utf-8"))
    return h.hexdigest()
