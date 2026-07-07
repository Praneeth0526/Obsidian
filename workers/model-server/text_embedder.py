"""
text_embedder.py — SentenceTransformers wrapper for the Model Server.

Loads ``all-MiniLM-L6-v2`` once at startup and exposes a single ``embed()``
method that converts a list of strings into 384-dimensional float vectors.

Design decisions:
    - The model is loaded lazily on first call (or eagerly via ``load()``) so
      that importing this module does not trigger a slow disk-read at import time.
    - ``encode()`` is called with ``normalize_embeddings=True`` so cosine
      similarity in OpenSearch can use the dot-product shortcut.
    - Device selection is automatic: CUDA → MPS (Apple Silicon) → CPU.
      Override with the ``TEXT_EMBEDDER_DEVICE`` environment variable.
    - Batch size for the SentenceTransformer encode pass is controlled by
      ``TEXT_EMBEDDER_BATCH_SIZE`` (default 64). This is separate from the
      HTTP batching that model_client.py performs upstream.

Usage (inside server.py):
    embedder = TextEmbedder()
    embedder.load()                          # warm up before serving traffic
    vectors = embedder.embed(["hello world", "another sentence"])
    # → [[0.031, -0.012, ...], [0.045, 0.003, ...]]  (each 384-dim)
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Model identifier consumed by sentence-transformers.
# Must match model_spec.json → text_model.name.
_MODEL_NAME = "Alibaba-NLP/gme-Qwen2-VL-2B-Instruct"

# Expected output dimension — validated on first encode call.
_EXPECTED_DIM = 1536

# Default encode batch size sent to the SentenceTransformer encoder.
# Larger values improve GPU throughput but increase VRAM pressure.
_DEFAULT_ENCODE_BATCH_SIZE = 64


# ---------------------------------------------------------------------------
# TextEmbedder
# ---------------------------------------------------------------------------

class TextEmbedder:
    """
    Wraps SentenceTransformers ``all-MiniLM-L6-v2`` for text embedding.

    Parameters
    ----------
    model_name : str
        HuggingFace model identifier.  Defaults to the value declared in
        ``model_spec.json``.  Override via ``TEXT_EMBEDDER_MODEL`` env var.
    device : str or None
        Torch device string (``"cpu"``, ``"cuda"``, ``"mps"``).
        ``None`` (default) lets sentence-transformers pick automatically
        unless ``TEXT_EMBEDDER_DEVICE`` is set.
    encode_batch_size : int
        Number of strings encoded in one SentenceTransformer pass.
        Defaults to ``TEXT_EMBEDDER_BATCH_SIZE`` env var or 64.
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        device: Optional[str] = None,
        encode_batch_size: Optional[int] = None,
    ) -> None:
        self.model_name = (
            model_name
            or os.environ.get("TEXT_EMBEDDER_MODEL", _MODEL_NAME)
        )
        self.device = device or os.environ.get("TEXT_EMBEDDER_DEVICE") or None
        self.encode_batch_size = int(
            encode_batch_size
            if encode_batch_size is not None
            else os.environ.get("TEXT_EMBEDDER_BATCH_SIZE", _DEFAULT_ENCODE_BATCH_SIZE)
        )
        self._model = None  # lazily loaded

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> None:
        """
        Load the SentenceTransformer model into memory.

        Call this once at server startup to avoid a cold-start delay on the
        first embedding request.  Safe to call multiple times (no-op after
        first load).
        """
        if self._model is not None:
            return

        # Import deferred so that ``import text_embedder`` is fast when the
        # model is not needed (e.g. in unit tests that mock this class).
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        logger.info(
            "Loading text embedding model '%s' (device=%s) …",
            self.model_name,
            self.device or "auto",
        )
        import torch
        self._model = SentenceTransformer(
            self.model_name,
            device=self.device,
            trust_remote_code=True,
        )
        # Validate dimension on startup so mismatches surface immediately.
        dim = self._model.get_sentence_embedding_dimension()
        if dim != _EXPECTED_DIM:
            raise RuntimeError(
                f"Text model '{self.model_name}' produces {dim}-dim vectors; "
                f"expected {_EXPECTED_DIM}. Update model_spec.json if intentional."
            )
        logger.info(
            "Text embedding model ready — dim=%d device=%s",
            dim,
            self._model.device,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a list of text strings into 384-dim float vectors.

        Vectors are L2-normalised so cosine similarity equals dot product,
        matching the ``cosinesimil`` space_type in the OpenSearch index mapping.

        Args:
            texts: Non-empty list of strings to embed.

        Returns:
            A list of float vectors, one per input string, in the same order.
            Each vector has exactly 384 dimensions.

        Raises:
            RuntimeError: If ``load()`` has not been called.
            ValueError:   If ``texts`` is empty.
        """
        if not texts:
            raise ValueError("texts must be a non-empty list of strings")

        if self._model is None:
            raise RuntimeError(
                "TextEmbedder.load() must be called before embed(). "
                "Call embedder.load() at server startup."
            )

        logger.debug(
            "Encoding %d text(s) with batch_size=%d",
            len(texts),
            self.encode_batch_size,
        )

        # encode() returns a numpy ndarray of shape (N, 384).
        # tolist() converts to a plain Python list[list[float]].
        vectors = self._model.encode(
            texts,
            batch_size=self.encode_batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()

        logger.debug(
            "Encoded %d text(s) → %d vectors (dim=%d)",
            len(texts),
            len(vectors),
            len(vectors[0]) if vectors else 0,
        )
        return vectors

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        """``True`` after ``load()`` has completed successfully."""
        return self._model is not None

    @property
    def dimension(self) -> int:
        """Output vector dimension (384).  Fixed regardless of model state."""
        return _EXPECTED_DIM
