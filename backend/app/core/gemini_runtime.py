"""Central Gemini call helper with real socket-level timeouts.

Replaces ad-hoc daemon-thread "timeouts" scattered across workers/services.
The previous pattern (`thread.join(timeout=N)` then abandon the thread)
could never interrupt a hung HTTPS call — the socket stayed open and the
heartbeat froze. Here we let the SDK enforce the timeout via
``HttpOptions(timeout=N_ms)`` so a stuck call actually closes its socket
and raises promptly.

Phase 0 keeps the surface small: one function ``call_gemini_with_pdf`` that
covers every existing PDF-attached call. Future phases (rate limit, retry
with backoff, structured tracing) hang off this same module so we get them
everywhere for free.
"""

from __future__ import annotations

import logging
import os
import random
import tempfile
import threading
import time
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# Hard ceiling on a single Gemini call. Flash usually responds in 15-60s on a
# single block; anything past 150s is a hung request we want to abandon.
DEFAULT_TIMEOUT_S = 150

# CONTAINER-WIDE memory ceiling on concurrent Gemini calls. 8 is the safe
# total for Railway's memory budget — tried 12 and Celery workers were
# OOM-killed by the kernel (SIGKILL) during figure extraction. Each in-flight
# call holds the PDF slice, Gemini request buffer, and (for figures) PNG bytes,
# so memory adds up fast. Override via GEMINI_GLOBAL_INFLIGHT only after a
# container upgrade.
_GLOBAL_INFLIGHT_CAP = int(os.environ.get("GEMINI_GLOBAL_INFLIGHT", "8"))


def _worker_concurrency() -> int:
    """Number of worker processes sharing this container's memory.

    Celery prefork forks CELERY_CONCURRENCY child processes; EACH imports this
    module and gets its OWN semaphore. So the real container-wide in-flight
    total is (per-process cap) × (concurrency). Inline/dev mode is a single
    process → 1.
    """
    try:
        return max(1, int(os.environ.get("CELERY_CONCURRENCY", "1")))
    except (TypeError, ValueError):
        return 1


# Per-process cap = global budget DIVIDED across the worker processes, so the
# container-wide total stays at _GLOBAL_INFLIGHT_CAP regardless of how
# CELERY_CONCURRENCY is tuned. This makes OOM architecturally impossible:
# bumping concurrency for throughput can no longer multiply memory load
# (the bug that SIGKILL-ed workers → orphaned jobs → "stuck extraction").
#   concurrency=1 → 8/proc → 8 total   concurrency=2 → 4/proc → 8 total
#   concurrency=3 → 2/proc → 6 total   concurrency=4 → 2/proc → 8 total
# Trade-off: at higher concurrency a single book's section fan-out is narrower
# (it shares the global budget), but total memory/throughput stay bounded —
# the right behaviour for many concurrent books.
_MAX_IN_FLIGHT = max(1, _GLOBAL_INFLIGHT_CAP // _worker_concurrency())
_inflight_sem = threading.BoundedSemaphore(_MAX_IN_FLIGHT)
logger.info(
    "gemini_runtime: in-flight cap = %d/process (global=%d ÷ concurrency=%d)",
    _MAX_IN_FLIGHT, _GLOBAL_INFLIGHT_CAP, _worker_concurrency(),
)

# Retry policy on transient errors (5xx, timeouts, connection resets). Total
# attempts = 1 + RETRY_ATTEMPTS (so default = 3 tries with exponential backoff
# 1s → 2s → 4s + jitter). Permanent errors (4xx, validation) are NOT retried.
RETRY_ATTEMPTS = 2
_BACKOFF_BASE_S = 1.0


def _is_transient(exc: BaseException) -> bool:
    """Return True if ``exc`` looks like a retryable Gemini failure.

    We retry on:
      - any timeout / connection reset / DNS hiccup
      - HTTP 429 (rate limited) and 5xx (server side)
    We do NOT retry on:
      - 400-class validation errors (prompt too long, bad PDF, auth)
      - JSON parse / schema errors (those bubble out of this layer anyway)
    """
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "timeout" in msg:
        return True
    if "connection" in msg and ("reset" in msg or "refused" in msg or "aborted" in msg):
        return True
    # google-genai surfaces server errors as APIError / ServerError-shaped objects;
    # check the status code if present.
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(code, int):
        if code == 429 or 500 <= code < 600:
            return True
    # Gemini sometimes returns "resource exhausted" / "deadline exceeded"
    if "resource exhausted" in msg or "deadline exceeded" in msg:
        return True
    if "service unavailable" in msg or "internal server error" in msg:
        return True
    return False


def _get_api_key() -> str:
    api_key = os.environ.get("GEMINI_API_KEY") or ""
    if not api_key:
        try:
            api_key = settings.GEMINI_API_KEY
        except AttributeError:
            api_key = ""
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set")
    return api_key


def _build_client(timeout_s: int):
    """Return a genai.Client whose underlying httpx client honours the timeout.

    The SDK accepts timeout in milliseconds via HttpOptions. We pass it through
    so connect/read/write/pool all share the same deadline.
    """
    from google import genai
    from google.genai import types as gtypes

    return genai.Client(
        api_key=_get_api_key(),
        http_options=gtypes.HttpOptions(timeout=int(timeout_s * 1000)),
    )


def call_gemini_with_pdf(
    *,
    pdf_bytes: bytes,
    system_prompt: str,
    user_prompt: str,
    model: str = "gemini-2.5-flash",
    timeout_s: int = DEFAULT_TIMEOUT_S,
    max_output_tokens: int = 65536,
    temperature: float = 0.0,
    response_mime_type: str = "application/json",
    display_name: str = "extract.pdf",
) -> str:
    """Upload a PDF slice, call Gemini, return the response text.

    A real socket timeout (timeout_s) bounds the call. On timeout the SDK
    raises and we propagate — callers retry or mark the job failed. The
    uploaded file is best-effort cleaned up in finally; cleanup errors are
    logged but never mask the original exception.
    """
    from google.genai import types as gtypes

    last_exc: BaseException | None = None
    # 1 initial try + RETRY_ATTEMPTS retries on transient failures.
    for attempt in range(RETRY_ATTEMPTS + 1):
        # Concurrency cap: hold the semaphore for the lifetime of one attempt
        # (upload + generate + cleanup). Released even on exception.
        with _inflight_sem:
            client = _build_client(timeout_s)
            tmp_path: str | None = None
            uploaded: Any = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(pdf_bytes)
                    tmp_path = tmp.name
                with open(tmp_path, "rb") as f:
                    uploaded = client.files.upload(
                        file=f,
                        config=gtypes.UploadFileConfig(
                            mime_type="application/pdf",
                            display_name=display_name,
                        ),
                    )
                response = client.models.generate_content(
                    model=model,
                    contents=[
                        gtypes.Part.from_uri(
                            file_uri=uploaded.uri,
                            mime_type="application/pdf",
                        ),
                        system_prompt + "\n\n" + user_prompt,
                    ],
                    config=gtypes.GenerateContentConfig(
                        response_mime_type=response_mime_type,
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                    ),
                )
                return response.text or ""
            except Exception as e:
                last_exc = e
                if attempt < RETRY_ATTEMPTS and _is_transient(e):
                    backoff = _BACKOFF_BASE_S * (2 ** attempt)
                    backoff += random.uniform(0, backoff * 0.25)  # jitter
                    logger.warning(
                        "Gemini transient error (attempt %s/%s): %s — retrying in %.1fs",
                        attempt + 1, RETRY_ATTEMPTS + 1, e, backoff,
                    )
                    time.sleep(backoff)
                    continue
                raise
            finally:
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except OSError as e:
                        logger.warning("failed to unlink temp PDF %s: %s", tmp_path, e)
                if uploaded is not None:
                    try:
                        client.files.delete(name=uploaded.name)
                    except Exception as e:
                        logger.warning("failed to delete Gemini upload %s: %s",
                                       uploaded.name, e)

    # Exhausted retries on transient failures
    assert last_exc is not None
    raise last_exc


def call_gemini_text_only(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str = "gemini-2.5-flash",
    timeout_s: int = DEFAULT_TIMEOUT_S,
    max_output_tokens: int = 32768,
    temperature: float = 0.4,
    response_mime_type: str = "application/json",
) -> str:
    """Call Gemini WITHOUT a PDF — pure text-in/text-out.

    Used by the question regenerator: source is the previously-extracted
    question's `raw_text` (already OCR'd by the extractor), not the PDF.
    Cheaper, faster, no upload/cleanup overhead.

    Same retry + concurrency semantics as ``call_gemini_with_pdf``.
    Default temperature is higher (0.4) than for OCR-style calls (0.0) so
    rephrasing/variation gets a little stochasticity.
    """
    from google.genai import types as gtypes

    last_exc: BaseException | None = None
    for attempt in range(RETRY_ATTEMPTS + 1):
        with _inflight_sem:
            client = _build_client(timeout_s)
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=[system_prompt + "\n\n" + user_prompt],
                    config=gtypes.GenerateContentConfig(
                        response_mime_type=response_mime_type,
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                    ),
                )
                return response.text or ""
            except Exception as e:
                last_exc = e
                if attempt < RETRY_ATTEMPTS and _is_transient(e):
                    backoff = _BACKOFF_BASE_S * (2 ** attempt)
                    backoff += random.uniform(0, backoff * 0.25)  # jitter
                    logger.warning(
                        "Gemini (text-only) transient error (attempt %s/%s): %s — retrying in %.1fs",
                        attempt + 1, RETRY_ATTEMPTS + 1, e, backoff,
                    )
                    time.sleep(backoff)
                    continue
                raise

    assert last_exc is not None
    raise last_exc


def call_gemini_text_with_images(
    *,
    system_prompt: str,
    user_prompt: str,
    image_bytes_list: list[tuple[bytes, str]],
    model: str = "gemini-2.5-pro",
    timeout_s: int = DEFAULT_TIMEOUT_S,
    max_output_tokens: int = 32768,
    temperature: float = 0.4,
    response_mime_type: str = "application/json",
) -> str:
    """Call Gemini with text + one or more inline images (no PDF upload).

    Used by Phase 4 multimodal question regeneration: send the source
    question text alongside its attached figure image(s) so the LLM can
    decide whether the image needs regeneration after producing the new
    question text.

    ``image_bytes_list``: list of (raw_bytes, mime_type) pairs. mime_type
    is usually "image/png" or "image/jpeg".

    Same retry + concurrency semantics as the other call_gemini_* helpers.
    """
    from google.genai import types as gtypes

    last_exc: BaseException | None = None
    for attempt in range(RETRY_ATTEMPTS + 1):
        with _inflight_sem:
            client = _build_client(timeout_s)
            try:
                parts: list[Any] = []
                # Each image is sent inline (no upload). For typical figure
                # binaries (<2MB each) this fits within Gemini's input limit.
                for img_bytes, mime in image_bytes_list:
                    parts.append(
                        gtypes.Part.from_bytes(
                            data=img_bytes,
                            mime_type=mime,
                        )
                    )
                parts.append(system_prompt + "\n\n" + user_prompt)
                response = client.models.generate_content(
                    model=model,
                    contents=parts,
                    config=gtypes.GenerateContentConfig(
                        response_mime_type=response_mime_type,
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                    ),
                )
                return response.text or ""
            except Exception as e:
                last_exc = e
                if attempt < RETRY_ATTEMPTS and _is_transient(e):
                    backoff = _BACKOFF_BASE_S * (2 ** attempt)
                    backoff += random.uniform(0, backoff * 0.25)
                    logger.warning(
                        "Gemini (text+images) transient error (attempt %s/%s): %s — retrying in %.1fs",
                        attempt + 1, RETRY_ATTEMPTS + 1, e, backoff,
                    )
                    time.sleep(backoff)
                    continue
                raise

    assert last_exc is not None
    raise last_exc
