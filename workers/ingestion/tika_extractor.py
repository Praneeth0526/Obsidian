"""
tika_extractor.py — Text and metadata extraction via Apache Tika Server.

Sends raw file bytes to the Tika REST API and returns extracted plain text
and structured metadata.

Currently supported formats:
    - PDF  (.pdf)
    - Word (.doc, .docx)
    - PowerPoint (.ppt, .pptx)
    - Plain Text (.txt)

Image support (JPEG, PNG, etc.) will be added in a later stage.

Usage:
    extractor = TikaExtractor(tika_url="http://localhost:9998")
    result = await extractor.extract(file_bytes, content_type="application/pdf")
    print(result.text)
    print(result.metadata)
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ExtractionResult:
    """Container for Tika extraction output."""

    text: str
    metadata: dict = field(default_factory=dict)
    success: bool = True
    error: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        """Return True if Tika produced no usable text."""
        return not self.text or not self.text.strip()


# ---------------------------------------------------------------------------
# Supported content types
# ---------------------------------------------------------------------------

# MIME types supported in Stage 1.
# TODO: Add image types (image/jpeg, image/png, etc.) in a later stage.
SUPPORTED_TYPES: set[str] = {
    # PDF
    "application/pdf",
    # Word — .doc (legacy) and .docx (OpenXML)
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    # PowerPoint — .ppt (legacy) and .pptx (OpenXML)
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    # Plain text
    "text/plain",
}


def is_supported(content_type: str) -> bool:
    """Check if the MIME type is supported for text extraction."""
    return content_type in SUPPORTED_TYPES


# ---------------------------------------------------------------------------
# Tika Extractor
# ---------------------------------------------------------------------------

class TikaExtractor:
    """
    Async client for the Apache Tika REST API.

    Endpoints used:
        PUT /tika          — extract plain text
        PUT /meta          — extract metadata (author, pages, dates, etc.)
        PUT /detect/stream — auto-detect MIME type
    """

    def __init__(
        self,
        tika_url: str = "http://localhost:9998",
        timeout: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        self.tika_url = tika_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def extract(
        self,
        file_bytes: bytes,
        content_type: str = "application/octet-stream",
        extract_metadata: bool = True,
    ) -> ExtractionResult:
        """
        Extract text (and optionally metadata) from raw file bytes.

        Args:
            file_bytes:       Raw bytes of the uploaded file.
            content_type:     MIME type (from the Kafka event's contentType).
            extract_metadata: If True, also calls /meta for structured metadata.

        Returns:
            ExtractionResult with .text, .metadata, .success, and .error.
        """
        if not file_bytes:
            return ExtractionResult(
                text="", success=False, error="Empty file bytes provided"
            )

        text = await self._extract_text(file_bytes, content_type)
        if text is None:
            return ExtractionResult(
                text="", success=False, error="Tika text extraction failed"
            )

        metadata: dict = {}
        if extract_metadata:
            metadata = await self._extract_metadata(file_bytes, content_type)

        result = ExtractionResult(text=text.strip(), metadata=metadata)

        if result.is_empty:
            logger.warning(
                "Tika returned empty text",
                extra={"content_type": content_type, "file_size": len(file_bytes)},
            )

        return result

    async def detect_type(self, file_bytes: bytes) -> Optional[str]:
        """
        Auto-detect the MIME type of file bytes via Tika.

        Useful when the Kafka event's contentType is missing or unreliable.
        """
        return await self._put_request(
            endpoint="/detect/stream",
            file_bytes=file_bytes,
            content_type="application/octet-stream",
            accept="text/plain",
        )

    async def health_check(self) -> bool:
        """Return True if the Tika server is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.tika_url}/tika")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _extract_text(
        self, file_bytes: bytes, content_type: str
    ) -> Optional[str]:
        """PUT /tika — returns plain text."""
        return await self._put_request(
            endpoint="/tika",
            file_bytes=file_bytes,
            content_type=content_type,
            accept="text/plain",
        )

    async def _extract_metadata(
        self, file_bytes: bytes, content_type: str
    ) -> dict:
        """PUT /meta — returns JSON metadata."""
        raw = await self._put_request(
            endpoint="/meta",
            file_bytes=file_bytes,
            content_type=content_type,
            accept="application/json",
        )
        if raw is None:
            return {}

        # Tika /meta returns a JSON object when Accept: application/json
        import json

        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse Tika metadata response")
            return {}

    async def _put_request(
        self,
        endpoint: str,
        file_bytes: bytes,
        content_type: str,
        accept: str,
    ) -> Optional[str]:
        """
        Send a PUT request to a Tika endpoint with retry logic.

        Args:
            endpoint:     e.g. "/tika", "/meta", "/detect/stream"
            file_bytes:   Raw file content.
            content_type: MIME type header sent to Tika.
            accept:       Accept header ("text/plain" or "application/json").

        Returns:
            Response body as a string, or None on failure.
        """
        url = f"{self.tika_url}{endpoint}"
        headers = {
            "Content-Type": content_type,
            "Accept": accept,
        }

        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.put(
                        url, content=file_bytes, headers=headers
                    )
                    response.raise_for_status()
                    return response.text

            except httpx.TimeoutException as exc:
                last_error = exc
                logger.warning(
                    "Tika request timed out (attempt %d/%d)",
                    attempt,
                    self.max_retries,
                    extra={
                        "endpoint": endpoint,
                        "file_size": len(file_bytes),
                        "timeout": self.timeout,
                    },
                )

            except httpx.HTTPStatusError as exc:
                last_error = exc
                logger.error(
                    "Tika returned HTTP %d (attempt %d/%d)",
                    exc.response.status_code,
                    attempt,
                    self.max_retries,
                    extra={"endpoint": endpoint, "response": exc.response.text[:500]},
                )
                # Don't retry on 4xx client errors
                if 400 <= exc.response.status_code < 500:
                    break

            except httpx.HTTPError as exc:
                last_error = exc
                logger.error(
                    "Tika connection error (attempt %d/%d): %s",
                    attempt,
                    self.max_retries,
                    str(exc),
                    extra={"endpoint": endpoint},
                )

        logger.error(
            "Tika extraction failed after %d attempts: %s",
            self.max_retries,
            str(last_error),
        )
        return None
