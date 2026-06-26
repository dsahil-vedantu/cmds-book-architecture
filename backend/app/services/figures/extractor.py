"""Figure extraction service — wraps the standalone extract_figures.py
pipeline as an in-process Python function.

Algorithm (verbatim from the upstream extract_figures.py + the
figure_extraction_system.txt "coordinate oracle" prompt):
  1. Send the whole PDF to Gemini in one call with the coordinate-oracle
     system prompt
  2. Gemini returns JSON with figures[] each carrying
     {id, figure_label, page, bounding_box, type, context, question_ref, caption}
  3. Render each referenced page at RENDER_DPI via pymupdf
  4. Crop each figure using normalized [ymin, xmin, ymax, xmax] @ 0-1000
  5. Return (image_bytes per figure_id, raw metadata)

Pure CPU after the single Gemini call. Caller persists into DB.

Environment knobs:
  - FIGURE_EXTRACTION_MODEL    default: "gemini-3.1-pro-preview"
  - FIGURE_RENDER_DPI          default: 200
  - FIGURE_INLINE_LIMIT_MB     default: 18
  - GEMINI_API_KEY             required
"""

from __future__ import annotations

import json
import logging
import os
from io import BytesIO
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROMPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "prompts" / "v1" / "figures" / "figure_extraction_system.txt"
)
USER_TURN = (
    "Extract every figure from this PDF per the system instructions. "
    "Return only the JSON object."
)


def _model_name() -> str:
    return os.environ.get("FIGURE_EXTRACTION_MODEL", "gemini-3.1-pro-preview")


def _render_dpi() -> int:
    try:
        return int(os.environ.get("FIGURE_RENDER_DPI", "200"))
    except ValueError:
        return 200


def _inline_limit_bytes() -> int:
    try:
        return int(os.environ.get("FIGURE_INLINE_LIMIT_MB", "18")) * 1024 * 1024
    except ValueError:
        return 18 * 1024 * 1024


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _bbox_to_pixels(bbox: list[int], width: int, height: int) -> tuple[int, int, int, int]:
    """Convert Gemini [ymin, xmin, ymax, xmax] @ 0-1000 to pixel bounds.

    Matches the upstream pipeline's bbox_to_pixels (extract_figures.py:77).
    """
    ymin, xmin, ymax, xmax = bbox
    left = max(0, int(xmin / 1000 * width))
    top = max(0, int(ymin / 1000 * height))
    right = min(width, int(xmax / 1000 * width))
    bottom = min(height, int(ymax / 1000 * height))
    return left, top, right, bottom


def _api_key() -> str:
    """Env first, pydantic settings fallback. Same pattern as
    `app.core.gemini_runtime._get_api_key` so the figures pipeline
    works in any context that the rest of the workers do."""
    key = os.environ.get("GEMINI_API_KEY") or ""
    if not key:
        try:
            from app.core.config import settings as _settings
            key = _settings.GEMINI_API_KEY or ""
        except Exception:
            key = ""
    return key


def call_gemini_extract(pdf_bytes: bytes) -> dict[str, Any]:
    """Single Gemini call: PDF -> figure metadata JSON.

    Mirrors call_gemini() in upstream extract_figures.py exactly, except
    we accept bytes rather than a file path so we can be called from a
    worker that loaded the PDF from S3/disk.
    """
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    model = _model_name()
    system_prompt = _load_prompt()

    size = len(pdf_bytes)
    if size <= _inline_limit_bytes():
        pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
    else:
        # Upstream uses files.upload(file=str(path)) — we have bytes, so we
        # spill to a temp file once for upload. Gemini Files API is needed
        # for PDFs over ~20MB.
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name
        try:
            uploaded = client.files.upload(
                file=tmp_path, config={"mime_type": "application/pdf"}
            )
            pdf_part = uploaded
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    response = client.models.generate_content(
        model=model,
        contents=[pdf_part, USER_TURN],
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            temperature=0.0,
        ),
    )
    raw = response.text
    if raw is None:
        raise RuntimeError(f"Gemini returned no text. Full response: {response}")
    return _parse_figure_json(raw)


def _parse_figure_json(raw: str) -> dict[str, Any]:
    """Parse the figure-extractor response — tolerant of trailing junk.

    Gemini's response_mime_type=application/json mostly returns clean JSON, but
    on long figure lists it occasionally appends commentary or a second JSON
    block AFTER the closing brace of the first object. Strict json.loads() then
    raises "Extra data: line N column 1 (char M)" and the whole figures task
    crashes — even though the FIRST JSON object is valid and contains the
    figures. Observed in prod: deterministic failure on a Class 9 maths PDF, 3
    retries all hit the same parse error.

    Strategy (cheap to expensive):
      1. Strip a leading ```json fence if present (defense in depth — never
         seen with mime=json, but a no-op when absent).
      2. Try strict json.loads(stripped) — handles the common clean case.
      3. On JSONDecodeError, use JSONDecoder().raw_decode() which returns the
         first valid JSON object plus where it stopped — silently drops the
         trailing junk. This is the actual fix for the observed failure.
    """
    text = raw.strip()
    if text.startswith("```"):
        # Strip ```json …``` fence if Gemini wrapped the response (rare with
        # response_mime_type=json, but harmless to handle).
        text = text.removeprefix("```json").removeprefix("```").strip()
        if text.endswith("```"):
            text = text[: -3].rstrip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            data, end = json.JSONDecoder().raw_decode(text)
            trailing = len(text) - end
            logger.warning(
                "Figure extractor: tolerated %d bytes of trailing junk after JSON",
                trailing,
            )
            return data
        except json.JSONDecodeError as e:
            logger.error(
                "Figure extractor returned invalid JSON: %s\n%s",
                e, raw[:500],
            )
            raise RuntimeError(
                f"Figure extractor returned invalid JSON: {e}"
            ) from e


def crop_figures(pdf_bytes: bytes, metadata: dict[str, Any]) -> dict[str, bytes]:
    """Crop each figure described in `metadata["figures"]` from the PDF
    using pymupdf at FIGURE_RENDER_DPI. Returns {figure_id_text: png_bytes}.

    Mirrors crop_figures() in upstream extract_figures.py — same DPI,
    same bbox-to-pixel logic, same skip rules for degenerate boxes.
    """
    import pymupdf as fitz
    from PIL import Image

    dpi = _render_dpi()
    zoom = dpi / 72.0

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_cache: dict[int, Image.Image] = {}
        out: dict[str, bytes] = {}
        for fig in metadata.get("figures", []) or []:
            fid = fig.get("id")
            if not fid:
                continue
            try:
                page_idx = int(fig["page"]) - 1
            except (KeyError, TypeError, ValueError):
                logger.warning("figure %s: missing/invalid page — skipping", fid)
                continue
            if page_idx < 0 or page_idx >= len(doc):
                logger.warning(
                    "figure %s: page %s out of range — skipping", fid, fig.get("page"),
                )
                continue
            if page_idx not in page_cache:
                page = doc[page_idx]
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                page_cache[page_idx] = Image.frombytes(
                    "RGB", (pix.width, pix.height), pix.samples,
                )
            page_img = page_cache[page_idx]
            bbox = fig.get("bounding_box")
            if not bbox or len(bbox) != 4:
                logger.warning("figure %s: missing bounding_box — skipping", fid)
                continue
            left, top, right, bottom = _bbox_to_pixels(
                bbox, page_img.width, page_img.height,
            )
            if right - left < 5 or bottom - top < 5:
                logger.warning(
                    "figure %s: degenerate bbox %s — skipping",
                    fid, (left, top, right, bottom),
                )
                continue
            crop = page_img.crop((left, top, right, bottom))
            buf = BytesIO()
            crop.save(buf, format="PNG")
            out[fid] = buf.getvalue()
        return out
    finally:
        doc.close()


def extract(pdf_bytes: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    """End-to-end extraction. Single Gemini call + pymupdf cropping.

    Returns (metadata_dict, {figure_id_text: png_bytes}).
    """
    metadata = call_gemini_extract(pdf_bytes)
    images = crop_figures(pdf_bytes, metadata)
    return metadata, images
