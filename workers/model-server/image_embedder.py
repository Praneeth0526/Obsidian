"""
image_embedder.py — GME-Qwen2-VL-2B-Instruct wrapper for the Model Server.

Loads ``Alibaba-NLP/gme-Qwen2-VL-2B-Instruct`` once at startup and exposes a
single ``embed()`` method that converts preprocessed JPEG bytes into a
1536-dimensional float vector.

Design decisions:
    - Uses SentenceTransformer since gme-Qwen2-VL-2B-Instruct is a unified
      multimodal model.
    - Output vectors are L2-normalised (``normalize_embeddings=True``).
    - Device selection mirrors text_embedder.py.

Usage (inside server.py):
    embedder = ImageEmbedder()
    embedder.load()                  # warm up before serving traffic
    with open("photo.jpg", "rb") as f:
        vector = embedder.embed(f.read())
"""

import io
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MODEL_NAME = "sentence-transformers/clip-ViT-B-32"
_EXPECTED_DIM = 512
_EXPECTED_SIZE = (224, 224)

# ---------------------------------------------------------------------------
# ImageEmbedder
# ---------------------------------------------------------------------------

class ImageEmbedder:
    def __init__(
        self,
        model_name: Optional[str] = None,
        device: Optional[str] = None,
    ) -> None:
        self.model_name = (
            model_name
            or os.environ.get("IMAGE_EMBEDDER_MODEL", _MODEL_NAME)
        )
        self.device = device or os.environ.get("IMAGE_EMBEDDER_DEVICE") or None
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return

        from sentence_transformers import SentenceTransformer
        import torch

        logger.info(
            "Loading image embedding model '%s' (device=%s) …",
            self.model_name,
            self.device or "auto",
        )

        self._model = SentenceTransformer(
            self.model_name,
            device=self.device,
            trust_remote_code=True,
        )

        dim = self._model.get_sentence_embedding_dimension()
        if dim is not None and dim != _EXPECTED_DIM:
            raise RuntimeError(f"Expected dim {_EXPECTED_DIM}, got {dim}")

        logger.info("Image embedding model ready — dim=%d", dim)

    def embed(self, image_bytes: bytes) -> list[float]:
        if not image_bytes:
            raise ValueError("image_bytes must not be empty")

        if self._model is None:
            raise RuntimeError("ImageEmbedder.load() must be called before embed().")

        from PIL import Image

        try:
            img = Image.open(io.BytesIO(image_bytes))
            img.load()
            img = img.convert("RGB")
        except Exception as exc:
            raise ValueError(f"Failed to decode image_bytes: {exc}") from exc

        logger.debug(
            "Encoding image (mode=%s size=%dx%d bytes=%d)",
            img.mode,
            img.size[0],
            img.size[1],
            len(image_bytes),
        )

        vectors = self._model.encode([img], show_progress_bar=False, normalize_embeddings=True)
        vector: list[float] = vectors[0].tolist()

        logger.debug("Image encoded — vector dim=%d", len(vector))
        return vector

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def dimension(self) -> int:
        return _EXPECTED_DIM
