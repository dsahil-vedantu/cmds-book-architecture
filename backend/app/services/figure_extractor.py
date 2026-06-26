"""Figure extraction service.

Two-step pipeline per section:
1. pymupdf extracts raw image bytes from digital PDFs (fast, reliable).
2. Gemini analyses the page(s) to produce metadata: caption, description,
   semantic_type, bounding_box, tags.
3. Images are matched to metadata by bounding-box IoU or order.

For scanned PDFs: Gemini identifies bounding boxes → page rendered as
pixmap at 150 dpi → cropped by bbox.
"""

from __future__ import annotations

import logging
from io import BytesIO

from app.core.gemini_client import extract_text, messages_create
from app.services.prompt_loader import render
from app.utils.json_parse import parse_json

logger = logging.getLogger(__name__)

FIGURE_EXTRACT_MODEL = "gemini-3.0-pro"
MIN_IMAGE_WIDTH = 50
MIN_IMAGE_HEIGHT = 50
MIN_IMAGE_BYTES = 3000  # skip tiny icons / decorations


def _iou(a: dict, b: dict) -> float:
    """Compute intersection-over-union of two bounding boxes {x0,y0,x1,y1}."""
    try:
        ix0 = max(a["x0"], b["x0"])
        iy0 = max(a["y0"], b["y0"])
        ix1 = min(a["x1"], b["x1"])
        iy1 = min(a["y1"], b["y1"])
        iw = max(0, ix1 - ix0)
        ih = max(0, iy1 - iy0)
        inter = iw * ih
        area_a = max(0, a["x1"] - a["x0"]) * max(0, a["y1"] - a["y0"])
        area_b = max(0, b["x1"] - b["x0"]) * max(0, b["y1"] - b["y0"])
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0
    except Exception:
        return 0.0


def _extract_images_pymupdf(
    pdf_bytes: bytes, page_start: int, page_end: int
) -> list[dict]:
    """Extract image bytes from a digital PDF using pymupdf.

    Returns list of dicts: {bytes, page, bbox {x0,y0,x1,y1}, width, height}
    page is 1-indexed relative to the whole document.
    """
    import pymupdf  # type: ignore

    results: list[dict] = []
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    for page_idx in range(page_start - 1, min(page_end, len(doc))):
        page = doc[page_idx]
        page_num = page_idx + 1
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                img_dict = doc.extract_image(xref)
                img_bytes = img_dict.get("image", b"")
                w = img_dict.get("width", 0)
                h = img_dict.get("height", 0)
                if w < MIN_IMAGE_WIDTH or h < MIN_IMAGE_HEIGHT or len(img_bytes) < MIN_IMAGE_BYTES:
                    continue
                # Get bbox on page
                rects = page.get_image_rects(xref)
                bbox = None
                if rects:
                    r = rects[0]
                    bbox = {"x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1}
                # Convert to PNG if not already
                if img_dict.get("ext", "png") != "png":
                    try:
                        import pymupdf  # noqa — already imported
                        pix = pymupdf.Pixmap(doc, xref)
                        if pix.n > 4:
                            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                        img_bytes = pix.tobytes("png")
                    except Exception:
                        pass  # keep original bytes
                results.append({
                    "bytes": img_bytes,
                    "page": page_num,
                    "bbox": bbox,
                    "width": w,
                    "height": h,
                })
            except Exception as exc:
                logger.debug("pymupdf extract xref %s page %s: %s", xref, page_num, exc)
    doc.close()
    return results


def _crop_page_to_bbox(
    pdf_bytes: bytes, page_num: int, bbox: dict
) -> bytes | None:
    """Render a PDF page at 150 dpi and crop to the given bbox.

    Used for scanned PDFs where Gemini provides bounding boxes but pymupdf
    cannot extract embedded images.
    """
    try:
        import pymupdf  # type: ignore

        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        page = doc[page_num - 1]
        # 150 dpi → zoom factor 150/72 ≈ 2.083
        zoom = 150 / 72
        mat = pymupdf.Matrix(zoom, zoom)
        # Clip rect in PDF points
        clip = pymupdf.Rect(bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"])
        pix = page.get_pixmap(matrix=mat, clip=clip)
        doc.close()
        return pix.tobytes("png")
    except Exception as exc:
        logger.warning("Crop page %s bbox %s failed: %s", page_num, bbox, exc)
        return None


def _pdf_pages_to_images(pdf_bytes: bytes, page_start: int, page_end: int) -> list[bytes]:
    """Render PDF pages as PNG images for sending to Gemini."""
    try:
        import pymupdf  # type: ignore

        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        images = []
        for page_idx in range(page_start - 1, min(page_end, len(doc))):
            page = doc[page_idx]
            zoom = 150 / 72
            mat = pymupdf.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            images.append(pix.tobytes("png"))
        doc.close()
        return images
    except Exception as exc:
        logger.warning("PDF page render failed: %s", exc)
        return []


async def _gemini_extract_metadata(page_images: list[bytes]) -> list[dict]:
    """Send rendered page images to Gemini and return figure metadata list."""
    import base64

    system_prompt = render("figure_extractor")

    # Build message with images
    content_parts: list[dict] = []
    for i, img_bytes in enumerate(page_images, 1):
        b64 = base64.b64encode(img_bytes).decode()
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })
    content_parts.append({
        "type": "text",
        "text": "Identify all figures, diagrams, graphs, charts, and images in the pages above. Return JSON.",
    })

    try:
        response = await messages_create(
            max_tokens=8000,
            system=system_prompt,
            messages=[{"role": "user", "content": content_parts}],
            model=FIGURE_EXTRACT_MODEL,
        )
        text = extract_text(response)
        data = parse_json(text)
        return list(data.get("figures") or [])
    except Exception as exc:
        logger.warning("Gemini figure metadata extraction failed: %s", exc)
        return []


def _is_digital_pdf(pdf_bytes: bytes) -> bool:
    """Check if PDF has extractable text (digital) vs scanned."""
    try:
        import pymupdf  # type: ignore

        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        text = ""
        for i in range(min(5, len(doc))):
            text += doc[i].get_text() or ""
        doc.close()
        return len(text.strip()) > 200
    except Exception:
        return False


def _match_images_to_metadata(
    raw_images: list[dict], metadata: list[dict], page_start: int
) -> list[tuple[dict, dict | None]]:
    """Match extracted images to Gemini metadata.

    Returns list of (metadata_item, image_dict_or_None).
    metadata_item page is absolute; raw_images page is also absolute.
    """
    matched: list[tuple[dict, dict | None]] = []
    used_image_indices: set[int] = set()

    for meta in metadata:
        meta_page = meta.get("page", 1) + page_start - 1  # convert relative→absolute
        meta_bbox = meta.get("bounding_box")
        best_idx = None
        best_iou = 0.0

        for idx, img in enumerate(raw_images):
            if idx in used_image_indices:
                continue
            if img["page"] != meta_page:
                continue
            if meta_bbox and img.get("bbox"):
                score = _iou(meta_bbox, img["bbox"])
                if score > best_iou:
                    best_iou = score
                    best_idx = idx
            else:
                # No bbox info — take first unmatched image on same page
                best_idx = idx
                break

        if best_idx is not None and (best_iou > 0.3 or not meta_bbox):
            matched.append((meta, raw_images[best_idx]))
            used_image_indices.add(best_idx)
        else:
            matched.append((meta, None))

    return matched


async def extract_figures_for_section(
    *,
    pdf_bytes: bytes,
    section_id: str,
    page_start: int,
    page_end: int,
) -> list[dict]:
    """Extract figures from a section's page range.

    Returns list of figure dicts:
    {
        section_id, figure_number, caption, description,
        semantic_type, tags, page_number, bounding_box,
        image_bytes (bytes|None)
    }
    """
    if page_start < 1:
        page_start = 1

    # Step 1: render pages for Gemini
    page_images = _pdf_pages_to_images(pdf_bytes, page_start, page_end)
    if not page_images:
        logger.warning("No page images rendered for section %s pages %s-%s", section_id, page_start, page_end)
        return []

    # Step 2: get metadata from Gemini
    metadata_list = await _gemini_extract_metadata(page_images)
    if not metadata_list:
        return []

    # Step 3: extract image bytes
    is_digital = _is_digital_pdf(pdf_bytes)
    raw_images: list[dict] = []
    if is_digital:
        raw_images = _extract_images_pymupdf(pdf_bytes, page_start, page_end)

    # Step 4: match
    if raw_images:
        matched = _match_images_to_metadata(raw_images, metadata_list, page_start)
    else:
        # Scanned or no embedded images — try cropping by bbox
        matched = [(meta, None) for meta in metadata_list]

    figures: list[dict] = []
    for meta, img_dict in matched:
        img_bytes: bytes | None = None
        if img_dict is not None:
            img_bytes = img_dict.get("bytes")
        elif not is_digital and meta.get("bounding_box"):
            # Scanned PDF: crop page to bbox
            abs_page = meta.get("page", 1) + page_start - 1
            img_bytes = _crop_page_to_bbox(pdf_bytes, abs_page, meta["bounding_box"])

        figures.append({
            "section_id": section_id,
            "figure_number": meta.get("figure_number"),
            "caption": meta.get("caption"),
            "description": meta.get("description"),
            "semantic_type": meta.get("semantic_type", "other"),
            "tags": meta.get("tags") or [],
            "page_number": meta.get("page", 1) + page_start - 1,
            "bounding_box": meta.get("bounding_box"),
            "image_bytes": img_bytes,
        })

    return figures
