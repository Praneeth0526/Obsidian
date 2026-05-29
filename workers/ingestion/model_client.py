"""
model_client.py — HTTP client for the shared Model Server.

Sends text chunks (or preprocessed images) to the model-server and receives
high-dimensional embedding vectors in return.

Endpoints called:
    POST /embed/text   — batch of strings  → batch of 384-dim float vectors
    POST /embed/image  — multipart image   → single 384-dim float vector

Design decisions:
    - Batches text chunks in configurable groups (default 32) to balance
      throughput vs. model-server memory pressure.
    - Retries on transient errors (5xx, timeouts, connection errors) with
      exponential backoff. Does NOT retry on 4xx client errors.
    - All config (URL, timeout, batch size, retries) comes from environment
      variables so the worker is fully container-friendly.
    - Uses httpx.AsyncClient for non-blocking I/O that fits the asyncio
      Kafka consumer in main.py.

Usage:
    client = ModelClient()                     # reads MODEL_SERVER_URL from env
    vectors = await client.embed_texts(chunks) # list[ChunkResult] → list[list[float]]
    vector  = await client.embed_image(img_bytes, "image/jpeg")
"""

import asyncio
import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults (all overridable via environment variables)
# ---------------------------------------------------------------------------

_DEFAULT_URL        = "http://localhost:8001"
_DEFAULT_TIMEOUT    = 30.0   # seconds per request
_DEFAULT_BATCH_SIZE = 32     # text chunks per /embed/text call
_DEFAULT_MAX_RETRY  = 3      # total attempts (1 initial + 2 retries)
_DEFAULT_BACKOFF    = 1.0    # base seconds for exponential backoff


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class EmbeddingError(Exception):
    """Raised when the model-server returns an unrecoverable error."""


# ---------------------------------------------------------------------------
# Model Client
# ---------------------------------------------------------------------------

class ModelClient:
    """
    Async HTTP client for the shared model-server.

    Parameters:
        model_server_url: Base URL of the model-server
                          (default: ``MODEL_SERVER_URL`` env var or localhost).
        timeout:          Per-request timeout in seconds
                          (default: ``MODEL_SERVER_TIMEOUT`` env var or 30 s).
        batch_size:       Max text chunks per ``/embed/text`` request
                          (default: ``MODEL_SERVER_BATCH_SIZE`` env var or 32).
        max_retries:      Total attempts before raising
                          (default: ``MODEL_SERVER_MAX_RETRIES`` env var or 3).
        backoff_base:     Base seconds for exponential backoff
                          (default: ``MODEL_SERVER_BACKOFF`` env var or 1.0 s).
    """

    def __init__(
        self,
        model_server_url: Optional[str] = None,
        timeout: Optional[float] = None,
        batch_size: Optional[int] = None,
        max_retries: Optional[int] = None,
        backoff_base: Optional[float] = None,
    ) -> None:
        self.base_url = (
            model_server_url
            or os.environ.get("MODEL_SERVER_URL", _DEFAULT_URL)
        ).rstrip("/")

        self.timeout = float(
            timeout
            if timeout is not None
            else os.environ.get("MODEL_SERVER_TIMEOUT", _DEFAULT_TIMEOUT)
        )
        self.batch_size = int(
            batch_size
            if batch_size is not None
            else os.environ.get("MODEL_SERVER_BATCH_SIZE", _DEFAULT_BATCH_SIZE)
        )
        self.max_retries = int(
            max_retries
            if max_retries is not None
            else os.environ.get("MODEL_SERVER_MAX_RETRIES", _DEFAULT_MAX_RETRY)
        )
        self.backoff_base = float(
            backoff_base
            if backoff_base is not None
            else os.environ.get("MODEL_SERVER_BACKOFF", _DEFAULT_BACKOFF)
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a list of text strings into 384-dim vectors.

        The list is split into batches of ``self.batch_size`` and each batch
        is sent as one ``POST /embed/text`` request.  Results are reassembled
        in the original order.

        Args:
            texts: Strings to embed (e.g. chunk texts from TextChunker).

        Returns:
            A list of float vectors, one per input string, in the same order.

        Raises:
            EmbeddingError: If the model-server fails after all retries.
            ValueError:     If the server response has mismatched length.
        """
        if not texts:
            return []

        all_embeddings: list[list[float]] = []
        batches = _batchify(texts, self.batch_size)

        logger.info(
            "Embedding %d text(s) in %d batch(es) (batch_size=%d)",
            len(texts),
            len(batches),
            self.batch_size,
        )

        for batch_idx, batch in enumerate(batches):
            t0 = time.monotonic()
            embeddings = await self._post_text_batch(batch, batch_idx)
            elapsed = (time.monotonic() - t0) * 1000

            if len(embeddings) != len(batch):
                raise ValueError(
                    f"Batch {batch_idx}: expected {len(batch)} vectors, "
                    f"got {len(embeddings)} from model-server"
                )

            logger.debug(
                "Batch %d/%d embedded %d chunks in %.0f ms",
                batch_idx + 1,
                len(batches),
                len(batch),
                elapsed,
            )
            all_embeddings.extend(embeddings)

        return all_embeddings

    async def embed_image(
        self,
        image_bytes: bytes,
        content_type: str = "image/jpeg",
    ) -> list[float]:
        """
        Embed a single preprocessed image into a 384-dim vector.

        Args:
            image_bytes:  Raw image bytes (already resized/normalized by
                          image_handler.py before calling here).
            content_type: MIME type of the image (e.g. ``"image/png"``).

        Returns:
            A single float vector.

        Raises:
            EmbeddingError: If the model-server fails after all retries.
        """
        if not image_bytes:
            raise ValueError("image_bytes must not be empty")

        url = f"{self.base_url}/embed/image"
        logger.info(
            "Embedding image (%s, %d bytes)", content_type, len(image_bytes)
        )

        response = await self._request_with_retry(
            method="POST",
            url=url,
            files={"file": ("image", image_bytes, content_type)},
        )

        data = response.json()
        embedding = data.get("embedding")
        if not embedding or not isinstance(embedding, list):
            raise EmbeddingError(
                f"Model-server /embed/image returned unexpected body: {data!r}"
            )

        logger.debug("Image embedded — vector dim=%d", len(embedding))
        return embedding

    async def health_check(self) -> bool:
        """Return True if the model-server's /health endpoint responds 200."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/health")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _post_text_batch(
        self, batch: list[str], batch_idx: int
    ) -> list[list[float]]:
        """Send one batch to POST /embed/text and return its embeddings."""
        url = f"{self.base_url}/embed/text"
        response = await self._request_with_retry(
            method="POST",
            url=url,
            json={"texts": batch},
            extra_log={"batch_index": batch_idx, "batch_size": len(batch)},
        )
        data = response.json()
        embeddings = data.get("embeddings")
        if embeddings is None or not isinstance(embeddings, list):
            raise EmbeddingError(
                f"Model-server /embed/text returned unexpected body: {data!r}"
            )
        return embeddings

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        json: Optional[dict] = None,
        files: Optional[dict] = None,
        extra_log: Optional[dict] = None,
    ) -> httpx.Response:
        """
        Send an HTTP request and retry on transient failures.

        Retry policy:
            - Retry on: 5xx responses, httpx.TimeoutException, httpx.ConnectError
            - Do NOT retry on: 4xx responses (client error — won't fix itself)
            - Backoff: attempt 1→2: base*1, attempt 2→3: base*2  (exponential)

        Args:
            method:    HTTP verb (``"POST"``).
            url:       Full URL to call.
            json:      JSON body dict (for text embedding).
            files:     Multipart files dict (for image embedding).
            extra_log: Extra fields merged into structured log records.

        Returns:
            The successful httpx.Response.

        Raises:
            EmbeddingError: After ``max_retries`` failed attempts.
        """
        last_error: Optional[Exception] = None
        log_extra = extra_log or {}

        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    if files:
                        response = await client.request(
                            method, url, files=files
                        )
                    else:
                        response = await client.request(
                            method, url, json=json
                        )
                    response.raise_for_status()
                    return response

            except httpx.TimeoutException as exc:
                last_error = exc
                logger.warning(
                    "Model-server timeout (attempt %d/%d): %s",
                    attempt,
                    self.max_retries,
                    url,
                    extra={**log_extra, "timeout": self.timeout},
                )

            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code

                logger.error(
                    "Model-server HTTP %d (attempt %d/%d): %s",
                    status,
                    attempt,
                    self.max_retries,
                    url,
                    extra={**log_extra, "response_body": exc.response.text[:300]},
                )

                # 4xx = client error — no point retrying
                if 400 <= status < 500:
                    raise EmbeddingError(
                        f"Model-server client error {status} at {url}: "
                        f"{exc.response.text[:200]}"
                    ) from exc

            except httpx.ConnectError as exc:
                last_error = exc
                logger.warning(
                    "Model-server connection error (attempt %d/%d): %s",
                    attempt,
                    self.max_retries,
                    str(exc),
                    extra=log_extra,
                )

            except httpx.HTTPError as exc:
                last_error = exc
                logger.error(
                    "Model-server unexpected HTTP error (attempt %d/%d): %s",
                    attempt,
                    self.max_retries,
                    str(exc),
                    extra=log_extra,
                )

            # Exponential backoff before next attempt
            if attempt < self.max_retries:
                wait = self.backoff_base * (2 ** (attempt - 1))
                logger.debug("Backing off %.1f s before retry %d", wait, attempt + 1)
                await asyncio.sleep(wait)

        raise EmbeddingError(
            f"Model-server failed after {self.max_retries} attempts "
            f"({url}): {last_error}"
        ) from last_error


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _batchify(items: list, size: int) -> list[list]:
    """Split *items* into sub-lists of at most *size* elements."""
    return [items[i : i + size] for i in range(0, len(items), size)]
