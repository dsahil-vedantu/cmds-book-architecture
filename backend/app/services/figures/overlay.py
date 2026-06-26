"""Label overlay service — wraps the standalone overlay_labels.py
pipeline as an in-process Python function.

Algorithm (verbatim port of overlay_labels.py — keeps the
hard-won spatial-merge thresholds and tiered fuzzy matcher intact):
  1. Gemini OCR original crop -> [{text, bbox_2d}] -> spatial-merge into
     verbatim ground-truth labels
  2. Gemini OCR regenerated PNG -> [{text, bbox_2d}] -> NO spatial merge
     (can't distinguish multi-line callouts from stacked-distinct labels)
  3. For each regen fragment: tiered fuzzy match against ground-truth list
       Tier 1: exact match -> score 1.0
       Tier 2: query ⊆ candidate -> pick longest containing candidate
       Tier 3: candidate ⊆ query -> pick longest
       Tier 4: SequenceMatcher.ratio()
  4. Threshold = FIGURE_FUZZY_THRESHOLD (default 0.55)
  5. Post-match: merge fragments mapping to same verbatim + spatially
     adjacent -> single render at union bbox
  6. PIL: fill bbox with sampled background color, draw verbatim text with
     TrueType font fitted to bbox size

Environment knobs:
  - FIGURE_OCR_MODEL         default: "gemini-3.1-pro-preview"
  - FIGURE_FUZZY_THRESHOLD   default: 0.55
  - GEMINI_API_KEY           required (fallback: EXTERNAL_GEMINI_API_KEY)
"""

from __future__ import annotations

import json
import logging
import os
import re
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).resolve().parents[3] / "prompts" / "v1" / "figures"

DEFAULT_FUZZY_THRESHOLD = 0.55


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _api_key() -> str:
    key = (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("EXTERNAL_GEMINI_API_KEY")
        or ""
    )
    if not key:
        try:
            from app.core.config import settings as _settings
            key = _settings.GEMINI_API_KEY or ""
        except Exception:
            key = ""
    return key


def _ocr_model() -> str:
    return os.environ.get("FIGURE_OCR_MODEL", "gemini-3.1-pro-preview")


def _fuzzy_threshold() -> float:
    try:
        return float(os.environ.get("FIGURE_FUZZY_THRESHOLD", str(DEFAULT_FUZZY_THRESHOLD)))
    except ValueError:
        return DEFAULT_FUZZY_THRESHOLD


def _ocr_prompt() -> str:
    return (PROMPT_DIR / "figure_ocr_with_bbox.txt").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Resilient JSON parsing (verbatim from upstream — handles malformed Gemini)
# ---------------------------------------------------------------------------

_LABEL_BBOX_RE = re.compile(
    r'\{\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,'
    r'\s*"bbox_2d"\s*:\s*\[\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\]'
    r'(?:\s*,[^}]*)?\s*\}?',
    re.DOTALL,
)


def _extract_labels_via_regex(text: str) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple] = set()
    for m in _LABEL_BBOX_RE.finditer(text):
        try:
            text_val = json.loads(f'"{m.group(1)}"')
        except json.JSONDecodeError:
            text_val = m.group(1)
        bbox = [int(m.group(i)) for i in (2, 3, 4, 5)]
        key = (text_val, tuple(bbox))
        if key in seen:
            continue
        seen.add(key)
        out.append({"text": text_val, "bbox_2d": bbox})
    return out


def _parse_json_loose(text: str) -> dict:
    if not text:
        raise json.JSONDecodeError("empty response", text or "", 0)
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s.rsplit("```", 1)[0]
        s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(s[start: end + 1])
        except json.JSONDecodeError:
            pass
    repaired = re.sub(r",(\s*[}\]])", r"\1", s)
    if repaired.endswith("]]}"):
        repaired = repaired[:-3] + "}]}"
    if repaired != s:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass
    labels = _extract_labels_via_regex(s)
    if labels:
        return {"labels": labels, "_recovered_via": "regex"}
    raise json.JSONDecodeError("could not extract JSON object or labels", s, 0)


def _gemini_ocr(image_bytes: bytes, *, model: str | None = None,
                max_retries: int = 3) -> dict:
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    target_model = model or _ocr_model()
    system_prompt = _ocr_prompt()

    last_text = ""
    temps = [0.0, 0.2, 0.5, 0.8]
    for attempt in range(max_retries + 1):
        try:
            response = client.models.generate_content(
                model=target_model,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                    "Run OCR per the system instructions. Return ONLY valid JSON.",
                ],
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    temperature=temps[min(attempt, len(temps) - 1)],
                ),
            )
            last_text = response.text or ""
            return _parse_json_loose(last_text)
        except json.JSONDecodeError:
            continue
    raise RuntimeError(
        f"OCR failed after {max_retries + 1} attempts. "
        f"Last response: {last_text[:500]!r}"
    )


def _ocr_with_bbox(image_bytes: bytes, *, model: str | None = None) -> list[dict]:
    raw = _gemini_ocr(image_bytes, model=model).get("labels") or []
    cleaned: list[dict] = []
    for e in raw:
        bbox = e.get("bbox_2d") or e.get("bbox")
        text = e.get("text")
        if text and bbox and len(bbox) == 4:
            cleaned.append({"text": text, "bbox_2d": list(bbox)})
    return cleaned


# ---------------------------------------------------------------------------
# Spatial merge (verbatim from upstream)
# ---------------------------------------------------------------------------

def _are_ocr_adjacent(a: list[int], b: list[int],
                       vgap_factor: float = 0.5, hovl_threshold: float = 0.55,
                       hcenter_threshold: float = 0.35) -> bool:
    a_ymin, a_xmin, a_ymax, a_xmax = a
    b_ymin, b_xmin, b_ymax, b_xmax = b
    a_h, b_h = a_ymax - a_ymin, b_ymax - b_ymin
    a_w, b_w = a_xmax - a_xmin, b_xmax - b_xmin
    if a_h <= 0 or b_h <= 0 or a_w <= 0 or b_w <= 0:
        return False
    smaller_h = min(a_h, b_h)
    wider_w = max(a_w, b_w)
    if a_ymax <= b_ymin:
        vgap = b_ymin - a_ymax
    elif b_ymax <= a_ymin:
        vgap = a_ymin - b_ymax
    else:
        return False
    if vgap > vgap_factor * smaller_h:
        return False
    width_ratio = wider_w / max(1, min(a_w, b_w))
    if width_ratio > 2.0:
        return False
    a_cx = (a_xmin + a_xmax) / 2
    b_cx = (b_xmin + b_xmax) / 2
    if abs(a_cx - b_cx) > hcenter_threshold * wider_w:
        return False
    overlap = max(0, min(a_xmax, b_xmax) - max(a_xmin, b_xmin))
    narrower_w = min(a_w, b_w)
    return (overlap / narrower_w) >= hovl_threshold


def _merge_ocr_fragments_spatially(entries: list[dict]) -> list[dict]:
    n = len(entries)
    if n <= 1:
        return list(entries)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            if _are_ocr_adjacent(entries[i]["bbox_2d"], entries[j]["bbox_2d"]):
                union(i, j)

    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    merged: list[dict] = []
    for indices in clusters.values():
        if len(indices) == 1:
            merged.append(entries[indices[0]])
            continue
        indices.sort(key=lambda k: (entries[k]["bbox_2d"][0], entries[k]["bbox_2d"][1]))
        text = " ".join(entries[k]["text"] for k in indices)
        boxes = [entries[k]["bbox_2d"] for k in indices]
        union_box = [
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        ]
        merged.append({"text": text, "bbox_2d": union_box})
    merged.sort(key=lambda e: (e["bbox_2d"][0], e["bbox_2d"][1]))
    return merged


# ---------------------------------------------------------------------------
# Tiered matcher (verbatim from upstream)
# ---------------------------------------------------------------------------

def best_match(query: str, candidates: list[str]) -> tuple[str, float]:
    if not candidates:
        return query, 0.0
    q = (query or "").lower().strip()
    q_inner = q.strip("()").strip()
    for c in candidates:
        if c.lower().strip() == q:
            return c, 1.0
    if q_inner:
        containing = [c for c in candidates if q_inner in c.lower().strip()]
        if containing:
            longest = max(containing, key=len)
            coverage = len(q_inner) / max(1, len(longest))
            return longest, 0.90 + 0.10 * coverage
    contained = [c for c in candidates if c.lower().strip() and c.lower().strip() in q]
    if contained:
        longest = max(contained, key=len)
        coverage = len(longest) / max(1, len(q))
        return longest, 0.85 + 0.10 * coverage
    scored = [(c, SequenceMatcher(None, q, c.lower().strip()).ratio()) for c in candidates]
    return max(scored, key=lambda x: x[1])


# ---------------------------------------------------------------------------
# PIL rendering helpers (verbatim from upstream — Linux font fallbacks)
# ---------------------------------------------------------------------------

def _find_font(size: int):
    from PIL import ImageFont

    candidates = [
        "DejaVuSans.ttf",
        "Arial.ttf", "arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _fit_font_size(text: str, max_w: int, max_h: int):
    size = max(8, int(max_h * 0.75))
    while size > 6:
        font = _find_font(size)
        try:
            bbox = font.getbbox(text)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:
            tw, th = font.getsize(text)
        if tw <= max_w * 0.95 and th <= max_h * 0.95:
            return size
        size -= 1
    return 8


def _bbox_to_pixels(bbox: list[int], width: int, height: int):
    ymin, xmin, ymax, xmax = bbox
    left = max(0, int(xmin / 1000 * width))
    top = max(0, int(ymin / 1000 * height))
    right = min(width, int(xmax / 1000 * width))
    bottom = min(height, int(ymax / 1000 * height))
    return left, top, right, bottom


def _sample_background_color(img, box):
    left, top, right, bottom = box
    pad = 4
    samples = []
    w, h = img.size
    for x in range(left, right, max(1, (right - left) // 8)):
        for y in (top - pad, bottom + pad):
            if 0 <= y < h:
                try:
                    samples.append(img.getpixel((x, y)))
                except Exception:
                    pass
    if not samples:
        return (255, 255, 255)
    rgb_samples = [s[:3] if isinstance(s, tuple) else (s, s, s) for s in samples]
    rs = sorted(s[0] for s in rgb_samples)
    gs = sorted(s[1] for s in rgb_samples)
    bs = sorted(s[2] for s in rgb_samples)
    return (rs[len(rs) // 2], gs[len(gs) // 2], bs[len(bs) // 2])


def _bbox_horizontal_overlap(a, b) -> float:
    al, _, ar, _ = a
    bl, _, br, _ = b
    overlap = max(0, min(ar, br) - max(al, bl))
    narrower = max(1, min(ar - al, br - bl))
    return overlap / narrower


def _merge_label_fragments(matches: list[dict]) -> list[dict]:
    by_text: dict[str, list[int]] = {}
    for i, m in enumerate(matches):
        by_text.setdefault(m["matched_text"], []).append(i)
    merged: list[dict] = []
    for matched_text, indices in by_text.items():
        if len(indices) == 1:
            merged.append(matches[indices[0]])
            continue
        boxes = sorted(indices, key=lambda i: matches[i]["bbox_pixels"][1])
        clusters: list[list[int]] = []
        for idx in boxes:
            placed = False
            for cluster in clusters:
                last = matches[cluster[-1]]["bbox_pixels"]
                cur = matches[idx]["bbox_pixels"]
                vertical_gap = cur[1] - last[3]
                avg_h = (last[3] - last[1] + cur[3] - cur[1]) / 2
                if vertical_gap < avg_h and _bbox_horizontal_overlap(last, cur) > 0.3:
                    cluster.append(idx)
                    placed = True
                    break
            if not placed:
                clusters.append([idx])
        for cluster in clusters:
            if len(cluster) == 1:
                merged.append(matches[cluster[0]])
                continue
            cluster_boxes = [matches[i]["bbox_pixels"] for i in cluster]
            union = [
                min(b[0] for b in cluster_boxes),
                min(b[1] for b in cluster_boxes),
                max(b[2] for b in cluster_boxes),
                max(b[3] for b in cluster_boxes),
            ]
            merged.append({
                "regen_text": " | ".join(matches[i]["regen_text"] for i in cluster),
                "matched_text": matched_text,
                "score": max(matches[i]["score"] for i in cluster),
                "bbox_pixels": union,
            })
    return merged


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def overlay(
    original_bytes: bytes,
    regen_bytes: bytes,
    *,
    ocr_model: str | None = None,
    threshold: float | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Replace AI-rendered labels in `regen_bytes` with verbatim text OCR'd
    from `original_bytes`. Returns (overlaid_png_bytes, debug_report).
    """
    from PIL import Image, ImageDraw

    threshold = threshold if threshold is not None else _fuzzy_threshold()

    original_raw = _ocr_with_bbox(original_bytes, model=ocr_model)
    original_merged = _merge_ocr_fragments_spatially(original_raw)
    original_labels = [e["text"] for e in original_merged]

    regen_raw = _ocr_with_bbox(regen_bytes, model=ocr_model)

    img = Image.open(BytesIO(regen_bytes)).convert("RGB")
    width, height = img.size

    raw_matches: list[dict] = []
    for entry in regen_raw:
        text_in_regen = entry.get("text", "")
        bbox = entry.get("bbox_2d") or entry.get("bbox")
        if not text_in_regen or not bbox or len(bbox) != 4:
            continue
        verbatim, score = best_match(text_in_regen, original_labels)
        chosen_text = verbatim if score >= threshold else text_in_regen
        left, top, right, bottom = _bbox_to_pixels(bbox, width, height)
        if right <= left or bottom <= top:
            continue
        raw_matches.append({
            "regen_text": text_in_regen,
            "matched_text": chosen_text,
            "score": round(score, 3),
            "bbox_pixels": [left, top, right, bottom],
        })

    matched = _merge_label_fragments(raw_matches)

    draw = ImageDraw.Draw(img)
    for m in matched:
        chosen_text = m["matched_text"]
        left, top, right, bottom = m["bbox_pixels"]
        bg = _sample_background_color(img, (left, top, right, bottom))
        pad = 2
        draw.rectangle(
            [max(0, left - pad), max(0, top - pad),
             min(width, right + pad), min(height, bottom + pad)],
            fill=bg,
        )
        bw, bh = right - left, bottom - top
        size = _fit_font_size(chosen_text, bw, bh)
        font = _find_font(size)
        try:
            tb = font.getbbox(chosen_text)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
        except AttributeError:
            tw, th = font.getsize(chosen_text)
        tx = left + (bw - tw) // 2
        ty = top + (bh - th) // 2
        draw.text((tx, ty), chosen_text, fill=(15, 23, 42), font=font)

    out_buf = BytesIO()
    img.save(out_buf, format="PNG")

    report = {
        "original_labels": original_labels,
        "regen_raw": regen_raw,
        "matched": matched,
        "threshold": threshold,
    }
    return out_buf.getvalue(), report
