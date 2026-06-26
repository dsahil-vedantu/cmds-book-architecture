"""Bridge to Claude via the Claude Agent SDK.

Spawns the user's local ``claude`` CLI as a subprocess, which authenticates
via the user's Claude Code OAuth / Max subscription — no separate Anthropic
API key or credit balance required.

Exposes the same ``messages_create`` / ``extract_text`` shape as
``claude_client`` so the rest of the service is unchanged.

PDF handling is vision-aware:

  1. We try ``pymupdf`` to extract text from the PDF. If the text is
     substantive (≥ 300 chars across the first pages), the PDF is digital
     and we send the PDF to Claude via the ``Read`` tool (which runs text
     extraction — fast, cheap, exact).

  2. If text extraction is empty/minimal, the PDF is scanned. We render
     each page as a PNG image using pymupdf and point Claude at the image
     paths. Claude Code's ``Read`` tool surfaces images visually to the
     multimodal model, which then does OCR via vision.

  3. Either way we cap at the first N pages for the initial analyse/schema
     calls so subprocess context stays manageable.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

_TEMP_ROOT = Path("/tmp/cmds_agent_pdfs")
_IMAGE_ROOT = Path("/tmp/cmds_agent_images")

MAX_PAGES_ANALYSE = 20  # cap pages sent for analyse / schema / raw-text
IMAGE_RENDER_DPI = 150  # balances legibility vs file size for vision


def _find_claude_cli() -> str | None:
    for candidate in (
        os.path.expanduser("~/.local/bin/claude"),
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("claude")


def is_available() -> bool:
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return False
    return _find_claude_cli() is not None


def _flatten_user_messages(messages: list[dict[str, Any]]) -> tuple[str, bytes | None]:
    user_text_parts: list[str] = []
    pdf_bytes: bytes | None = None
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            user_text_parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    user_text_parts.append(block.get("text", ""))
                elif btype == "document":
                    src = block.get("source", {})
                    if src.get("type") == "base64":
                        try:
                            pdf_bytes = base64.b64decode(src.get("data", ""))
                        except Exception as e:
                            logger.warning("Failed to decode PDF base64: %s", e)
    return "\n\n".join(p for p in user_text_parts if p), pdf_bytes


def _pdf_has_substantive_text(pdf_bytes: bytes) -> tuple[bool, int]:
    """Return (has_text, total_chars). Uses pymupdf."""
    try:
        import pymupdf

        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        total = 0
        for i, page in enumerate(doc):
            if i >= MAX_PAGES_ANALYSE:
                break
            try:
                total += len((page.get_text() or "").strip())
            except Exception:
                pass
            if total > 300:
                doc.close()
                return True, total
        doc.close()
        return total > 300, total
    except Exception as e:
        logger.warning("pymupdf text probe failed: %s", e)
        return False, 0


def _render_pages_to_pngs(pdf_bytes: bytes, out_dir: Path, max_pages: int) -> list[Path]:
    """Render the first N PDF pages to PNG images. Returns list of paths."""
    import pymupdf

    out_dir.mkdir(parents=True, exist_ok=True)
    zoom = IMAGE_RENDER_DPI / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    paths: list[Path] = []
    try:
        for i in range(min(len(doc), max_pages)):
            page = doc[i]
            pix = page.get_pixmap(matrix=matrix)
            path = out_dir / f"page_{i + 1:03d}.png"
            pix.save(str(path))
            paths.append(path)
    finally:
        doc.close()
    return paths


def _cleanup_dir(d: Path) -> None:
    if d.exists():
        try:
            shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


class _AgentTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _AgentResponse:
    def __init__(self, text: str):
        self.content = [_AgentTextBlock(text)]


async def messages_create(
    *,
    system: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    model: str | None = None,
    **_kwargs: Any,
) -> Any:
    """Drop-in replacement for ``claude_client.messages_create`` — routes
    through the Agent SDK (Claude Code OAuth)."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        TextBlock,
        query,
    )

    user_text, pdf_bytes = _flatten_user_messages(messages)

    pdf_path: Path | None = None
    image_dir: Path | None = None
    image_paths: list[Path] = []

    if pdf_bytes:
        has_text, chars = _pdf_has_substantive_text(pdf_bytes)
        logger.info("agent PDF probe: has_text=%s chars=%s", has_text, chars)

        if has_text:
            # Digital PDF — let Claude Read it directly (native PDF text).
            _TEMP_ROOT.mkdir(parents=True, exist_ok=True)
            pdf_path = _TEMP_ROOT / f"{uuid4().hex}.pdf"
            pdf_path.write_bytes(pdf_bytes)
        else:
            # Scanned / image-based PDF — render pages to PNGs so Claude
            # Code's Read tool surfaces them to the multimodal model for
            # vision OCR.
            image_dir = _IMAGE_ROOT / uuid4().hex
            try:
                image_paths = _render_pages_to_pngs(
                    pdf_bytes, image_dir, MAX_PAGES_ANALYSE
                )
                logger.info(
                    "agent PDF scanned: rendered %d page(s) to PNG", len(image_paths)
                )
            except Exception as e:
                logger.warning("PDF → PNG render failed: %s", e)
                _cleanup_dir(image_dir)
                image_dir = None
                image_paths = []

    # Build the prompt
    if pdf_path is not None:
        prompt = (
            f"There is a PDF file at: {pdf_path}\n\n"
            "Use the Read tool to read it. Then respond to the request below. "
            "Return ONLY the requested output — no commentary, no markdown fences.\n\n"
            f"REQUEST:\n{user_text}"
        )
    elif image_paths:
        image_list = "\n".join(f"- {p}" for p in image_paths)
        prompt = (
            "The user uploaded a scanned/image-based PDF. I've rendered each "
            f"page as a PNG image ({len(image_paths)} page(s) total) at these "
            "paths:\n"
            f"{image_list}\n\n"
            "Use the Read tool on each image path — Claude Code will surface "
            "each image visually to you. Look at every page carefully (they "
            "contain the text content of the PDF) and then respond to the "
            "request below based on what you see across all pages.\n\n"
            "Return ONLY the requested output — no commentary, no markdown "
            "fences.\n\n"
            f"REQUEST:\n{user_text}"
        )
    else:
        prompt = user_text

    cli_path = _find_claude_cli()

    # Enough turns for Claude to Read every image/PDF plus respond.
    max_turns = 1
    if pdf_path is not None:
        max_turns = 5
    elif image_paths:
        max_turns = max(5, len(image_paths) + 3)

    options_kwargs: dict[str, Any] = {
        "system_prompt": system,
        "permission_mode": "bypassPermissions",
        "max_turns": max_turns,
    }
    if pdf_path is not None or image_paths:
        options_kwargs["allowed_tools"] = ["Read"]
    if model:
        options_kwargs["model"] = model
    if cli_path:
        options_kwargs["path_to_claude_code_executable"] = cli_path

    try:
        options = ClaudeAgentOptions(**options_kwargs)
    except TypeError:
        options_kwargs.pop("path_to_claude_code_executable", None)
        options = ClaudeAgentOptions(**options_kwargs)

    final_parts: list[str] = []
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for item in message.content:
                    if isinstance(item, TextBlock):
                        final_parts.append(item.text)
    finally:
        if pdf_path is not None and pdf_path.exists():
            try:
                pdf_path.unlink()
            except OSError:
                pass
        if image_dir is not None:
            _cleanup_dir(image_dir)

    return _AgentResponse(text="".join(final_parts).strip())
