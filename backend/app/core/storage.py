"""Pluggable storage backend — local filesystem by default, S3/MinIO when configured.

The backend is selected via ``STORAGE_BACKEND`` (``local`` or ``s3``). In local
mode, PDFs are stored under ``STORAGE_LOCAL_ROOT`` (default ``./storage``).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from uuid import uuid4

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Local filesystem backend ─────────────────────────────────────────────

def _local_root() -> Path:
    root = Path(settings.STORAGE_LOCAL_ROOT).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _local_put(subdir: str, filename: str, data: bytes, _content_type: str) -> str:
    rel = f"{subdir}/{uuid4()}/{filename}"
    dest = _local_root() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return rel


def _local_get(key: str) -> bytes:
    path = _local_root() / key
    return path.read_bytes()


def _local_delete(key: str) -> None:
    path = _local_root() / key
    if path.exists():
        path.unlink(missing_ok=True)
        parent = path.parent
        if parent.exists() and not any(parent.iterdir()):
            shutil.rmtree(parent, ignore_errors=True)


def _local_url(key: str, _expires: int) -> str:
    # Served by the /storage/{key} endpoint for local mode.
    return f"{settings.S3_PUBLIC_ENDPOINT.rstrip('/')}/storage/{key}"


# ── S3 / MinIO backend (lazy) ─────────────────────────────────────────────

_s3_client = None
_public_s3_client = None


def _get_s3():
    global _s3_client
    if _s3_client is None:
        import boto3
        from botocore.client import Config

        _s3_client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            region_name=settings.S3_REGION,
            config=Config(signature_version="s3v4"),
        )
    return _s3_client


def _get_public_s3():
    global _public_s3_client
    if _public_s3_client is None:
        import boto3
        from botocore.client import Config

        _public_s3_client = boto3.client(
            "s3",
            endpoint_url=settings.S3_PUBLIC_ENDPOINT,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            region_name=settings.S3_REGION,
            config=Config(signature_version="s3v4"),
        )
    return _public_s3_client


def _s3_ensure_bucket(bucket: str) -> None:
    s3 = _get_s3()
    from botocore.exceptions import ClientError

    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchBucket"):
            s3.create_bucket(Bucket=bucket)
        else:
            raise


def _s3_put(bucket: str, filename: str, data: bytes, content_type: str) -> str:
    _s3_ensure_bucket(bucket)
    key = f"pdfs/{uuid4()}/{filename}"
    _get_s3().put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
    return key


def _s3_get(bucket: str, key: str) -> bytes:
    obj = _get_s3().get_object(Bucket=bucket, Key=key)
    return obj["Body"].read()


def _s3_url(bucket: str, key: str, expires: int) -> str:
    return _get_public_s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )


# ── Public API (backend-agnostic) ────────────────────────────────────────

def upload_pdf(data: bytes, original_filename: str) -> str:
    if settings.STORAGE_BACKEND == "local":
        return _local_put("pdfs", original_filename, data, "application/pdf")
    return _s3_put(settings.S3_BUCKET_PDFS, original_filename, data, "application/pdf")


def download_pdf(key: str) -> bytes:
    if settings.STORAGE_BACKEND == "local":
        return _local_get(key)
    return _s3_get(settings.S3_BUCKET_PDFS, key)


def pdf_url(key: str, expires: int = 3600) -> str:
    if settings.STORAGE_BACKEND == "local":
        return _local_url(key, expires)
    return _s3_url(settings.S3_BUCKET_PDFS, key, expires)


def delete_pdf(key: str) -> None:
    if settings.STORAGE_BACKEND == "local":
        _local_delete(key)
        return
    # S3 deletion intentionally not implemented in this sprint.


def local_root_path() -> Path:
    """Used by the /storage/* FastAPI route to serve files back in local mode."""
    return _local_root()


# ── Figure storage (images extracted from / generated for PDFs) ──────────

def _figure_rel(book_id: str, section_id: str, filename: str) -> str:
    safe_section = section_id.replace("/", "_").replace(" ", "_")
    return f"figures/{book_id}/{safe_section}/{filename}"


def upload_figure(data: bytes, book_id: str, section_id: str, filename: str) -> str:
    """Save an extracted or redrawn figure image. Returns the storage key."""
    rel = _figure_rel(book_id, section_id, filename)
    if settings.STORAGE_BACKEND == "local":
        dest = _local_root() / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return rel
    _s3_ensure_bucket(settings.S3_BUCKET_PDFS)
    _get_s3().put_object(Bucket=settings.S3_BUCKET_PDFS, Key=rel, Body=data, ContentType="image/png")
    return rel


def download_figure(key: str) -> bytes:
    if settings.STORAGE_BACKEND == "local":
        return _local_get(key)
    return _s3_get(settings.S3_BUCKET_PDFS, key)


def figure_url(key: str, expires: int = 3600) -> str:
    if settings.STORAGE_BACKEND == "local":
        return _local_url(key, expires)
    return _s3_url(settings.S3_BUCKET_PDFS, key, expires)
