"""Google Cloud Vision provider — best for scanned / image-based PDFs.

Requires GCS bucket because Vision's async_batch_annotate_files only reads from GCS.
Dependencies are imported lazily so missing SDKs don't block the rest of the
application at boot time.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import uuid4

from app.providers.base import OCRProvider

logger = logging.getLogger(__name__)


class GoogleVisionProvider(OCRProvider):
    name = "google_vision"
    handles = ["scanned", "image_pdf"]
    avg_time_per_page = 5.0

    def __init__(self, credentials_json: dict[str, Any], gcs_bucket: str):
        if not credentials_json or not gcs_bucket:
            raise ValueError("GoogleVision requires credentials_json and gcs_bucket")
        try:
            from google.cloud import storage, vision
        except ImportError as e:
            raise RuntimeError(
                "google-cloud-vision and google-cloud-storage packages are not installed. "
                "Install the 'gcp' extra to enable this provider."
            ) from e

        self._vision_mod = vision
        self._storage_mod = storage
        self.vision_client = vision.ImageAnnotatorClient.from_service_account_info(
            credentials_json
        )
        self.storage_client = storage.Client.from_service_account_info(credentials_json)
        self.bucket = gcs_bucket

    async def extract_text(
        self, pdf_bytes: bytes, options: dict[str, Any] | None = None
    ) -> str:
        # All google-cloud SDK calls are synchronous; run in a thread.
        return await asyncio.to_thread(self._sync_extract, pdf_bytes)

    def _sync_extract(self, pdf_bytes: bytes) -> str:
        vision = self._vision_mod
        run_id = uuid4().hex
        input_blob = f"ocr_input/{run_id}.pdf"
        output_prefix = f"ocr_output/{run_id}/"

        bucket = self.storage_client.bucket(self.bucket)
        bucket.blob(input_blob).upload_from_string(
            pdf_bytes, content_type="application/pdf"
        )

        gcs_source = vision.GcsSource(uri=f"gs://{self.bucket}/{input_blob}")
        input_config = vision.InputConfig(
            gcs_source=gcs_source, mime_type="application/pdf"
        )
        gcs_destination = vision.GcsDestination(
            uri=f"gs://{self.bucket}/{output_prefix}"
        )
        output_config = vision.OutputConfig(
            gcs_destination=gcs_destination, batch_size=10
        )
        feature = vision.Feature(type_=vision.Feature.Type.DOCUMENT_TEXT_DETECTION)
        request = vision.AsyncAnnotateFileRequest(
            features=[feature],
            input_config=input_config,
            output_config=output_config,
        )
        operation = self.vision_client.async_batch_annotate_files(requests=[request])
        operation.result(timeout=600)

        texts: list[str] = []
        for blob in self.storage_client.list_blobs(self.bucket, prefix=output_prefix):
            payload = json.loads(blob.download_as_string())
            for page in payload.get("responses", []):
                ann = page.get("fullTextAnnotation")
                if ann and ann.get("text"):
                    texts.append(ann["text"])
        return "\n\n".join(texts)

    async def health_check(self) -> bool:
        try:
            # Cheapest available signal: does the bucket exist?
            return await asyncio.to_thread(
                lambda: self.storage_client.bucket(self.bucket).exists()
            )
        except Exception as e:
            logger.warning("GoogleVision health_check failed: %s", e)
            return False
