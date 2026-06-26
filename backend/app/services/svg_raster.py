"""SVG → PNG rasterization for Word export.

Word (python-docx) cannot embed raw ``<svg>`` markup — it needs a raster
image. The Step-2 diagram regen produces an inline ``svg_preview`` string; at
export time we turn that into PNG bytes here.

Two backends, tried in order:

  1. cairosvg — the lightweight, high-fidelity primary. On Linux/production a
     single ``apt-get install libcairo2`` makes it work; pure-pip otherwise.
  2. resvg — a dependency-free standalone binary (no native DLLs). This is the
     Windows-dev fallback so we get parity without fighting the Cairo DLL.

Both are best-effort: any failure returns ``None`` and the caller keeps the
original figure. Rasterization never breaks an export.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from functools import lru_cache

from app.core.config import settings

logger = logging.getLogger(__name__)

# Default raster width (px). 720 ≈ 5in at 144dpi, matching the docx image cap.
DEFAULT_WIDTH = 720


def _rasterize_cairosvg(svg: str, width: int) -> bytes | None:
    try:
        import cairosvg  # type: ignore
    except Exception:
        return None
    try:
        return cairosvg.svg2png(
            bytestring=svg.encode("utf-8"),
            output_width=width,
            background_color="white",
        )
    except Exception as e:
        # cairosvg present but native libcairo missing (typical on Windows),
        # or the SVG tripped it. Fall through to resvg.
        logger.debug("cairosvg rasterization failed: %s", e)
        return None


@lru_cache(maxsize=1)
def _find_resvg() -> str | None:
    """Locate the resvg binary: explicit config → PATH → bundled tools dir."""
    configured = (settings.RESVG_BINARY_PATH or "").strip()
    if configured and os.path.isfile(configured):
        return configured
    found = shutil.which("resvg")
    if found:
        return found
    # Bundled location: backend/tools/resvg/resvg(.exe)
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for name in ("resvg.exe", "resvg"):
        cand = os.path.join(here, "tools", "resvg", name)
        if os.path.isfile(cand):
            return cand
    return None


def _rasterize_resvg(svg: str, width: int) -> bytes | None:
    binary = _find_resvg()
    if not binary:
        return None
    try:
        # stdin → stdout: `resvg [OPTIONS] - -c`
        proc = subprocess.run(
            [binary, "--width", str(width), "--background", "white", "-", "-c"],
            input=svg.encode("utf-8"),
            capture_output=True,
            timeout=30,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
        logger.debug(
            "resvg failed rc=%s err=%s", proc.returncode, proc.stderr[:300]
        )
    except Exception as e:
        logger.debug("resvg invocation failed: %s", e)
    return None


def rasterize_svg_to_png(svg: str | None, *, width: int = DEFAULT_WIDTH) -> bytes | None:
    """Return PNG bytes for ``svg`` or None if neither backend can render it.

    Tries cairosvg first (production), then the resvg binary (dev fallback).
    Never raises — a None result tells the caller to keep the original figure.
    """
    if not svg or "<svg" not in svg:
        return None
    return _rasterize_cairosvg(svg, width) or _rasterize_resvg(svg, width)


@lru_cache(maxsize=1)
def rasterizer_available() -> bool:
    """True when at least one SVG→PNG backend can actually run in this env.

    Used for the Layered-Hydration check: we only DOWNGRADE a diagram to
    fallback-on-render-failure when a working rasterizer exists. If neither
    backend is installed (e.g. a bare dev box), we must NOT flip every diagram
    to fallback — we just skip the upstream validation and let the browser
    render the SVG and the export keep the original figure.
    """
    # cairosvg counts only if its native lib actually loads — probe a tiny SVG.
    probe = '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"></svg>'
    if _rasterize_cairosvg(probe, 1) is not None:
        return True
    return _find_resvg() is not None


# ---------------------------------------------------------------------------
# Tier 3 (NOT IMPLEMENTED — intentional seam): heavy LaTeX compilation.
#
# When the SVG fast-path can't represent a diagram (set via the model's
# ``fallback_to_original`` flag, or detected here when rasterization fails),
# the current behavior keeps the ORIGINAL textbook figure. A future, opt-in
# third tier could instead compile the model's ``latex_code`` to PDF→PNG using
# a slim, sandboxed engine (tectonic preferred — single static binary, no
# TeX Live, no shell-escape). Deferred deliberately: it adds a multi-GB image /
# slow cold starts, and the SVG path covers the overwhelming majority of K-12
# diagrams. To add it, implement ``_rasterize_tectonic(latex_code)`` and route
# to it from the export builder only when rasterize_svg_to_png() returns None.
# ---------------------------------------------------------------------------
