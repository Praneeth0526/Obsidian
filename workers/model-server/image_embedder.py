"""
image_embedder.py — nomic-embed-vision wrapper for the Model Server.

Loads ``nomic-ai/nomic-embed-vision-v1.5`` once at startup and exposes a
single ``embed()`` method that converts preprocessed JPEG bytes into a
384-dimensional float vector.

Design decisions:
    - Uses the SentenceTransformers vision pipeline (CLIPModel-compatible)
      so the image model can be loaded with the same API surface as the
      text embedder, keeping server.py symmetrical.
    - Input MUST be a 224×224 RGB JPEG — the format that ImageHandler already
      produces.  No resizing or format conversion is performed here; that
      responsibility belongs to the ingestion worker's image_handler.py.
    - Output vectors are L2-normalised (``normalize_embeddings=True``) to
      match the cosinesimil space type in the OpenSearch index mapping.
    - Device selection mirrors text_embedder.py: CUDA → MPS → CPU, with an
      optional ``IMAGE_EMBEDDER_DEVICE`` environment variable override.

Usage (inside server.py):
    embedder = ImageEmbedder()
    embedder.load()                  # warm up before serving traffic
    with open("photo.jpg", "rb") as f:
        vector = embedder.embed(f.read())
    # → [0.012, -0.045, ...]  (384-dim, L2-normalised)
"""

import io
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Model identifier consumed by sentence-transformers.
# Must match model_spec.json → image_model.name.
_MODEL_NAME = "nomic-ai/nomic-embed-vision-v1.5"

# Expected output dimension — validated on load.
_EXPECTED_DIM = 384

# Expected input resolution produced by ImageHandler.
_EXPECTED_SIZE = (224, 224)


# ---------------------------------------------------------------------------
# ImageEmbedder
# ---------------------------------------------------------------------------

class ImageEmbedder:
    """
    Wraps ``nomic-embed-vision-v1.5`` (CLIP-compatible) for image embedding.

    Parameters
    ----------
    model_name : str
        HuggingFace model identifier.  Defaults to the value declared in
        ``model_spec.json``.  Override via ``IMAGE_EMBEDDER_MODEL`` env var.
    device : str or None
        Torch device string (``"cpu"``, ``"cuda"``, ``"mps"``).
        ``None`` (default) lets sentence-transformers pick automatically
        unless ``IMAGE_EMBEDDER_DEVICE`` is set.
    """

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
        self._model = None   # lazily loaded
        self._processor = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> None:
        """
        Load the vision model and its image processor into memory.

        Call this once at server startup to avoid cold-start delays on the
        first embedding request.  Safe to call multiple times (no-op after
        the first successful load).

        Raises:
            RuntimeError: If the model output dimension does not match the
                          expected 384 declared in model_spec.json.
        """
        if self._model is not None:
            return

        # Deferred imports keep module import fast in test environments.
        from transformers import AutoModel, AutoImageProcessor  # noqa: PLC0415

        logger.info(
            "Loading image embedding model '%s' (device=%s) …",
            self.model_name,
            self.device or "auto",
        )

        self._processor = AutoImageProcessor.from_pretrained(self.model_name)
        self._model = AutoModel.from_pretrained(
            self.model_name,
            trust_remote_code=True,
        )
        if self.device:
            self._model.to(self.device)
        self._model.eval()

        logger.info(
            "Image embedding model ready — dim=%d (matryoshka) device=%s",
            _EXPECTED_DIM,
            self._model.device,
        )

    def embed(self, image_bytes: bytes) -> list[float]:
        """
        Embed a single preprocessed image into a 384-dim float vector.

        The input MUST be a JPEG-encoded 224×224 RGB image, as produced by
        ``ImageHandler.process()`` in the ingestion worker.  No resizing or
        colour-space conversion is performed here.

        Args:
            image_bytes: Raw JPEG bytes of the preprocessed image.

        Returns:
            A single float vector of length 384, L2-normalised.

        Raises:
            RuntimeError: If ``load()`` has not been called.
            ValueError:   If ``image_bytes`` is empty or the decoded image
                          does not match the expected 224×224 resolution.
        """
        if not image_bytes:
            raise ValueError("image_bytes must not be empty")

        if self._model is None:
            raise RuntimeError(
                "ImageEmbedder.load() must be called before embed(). "
                "Call embedder.load() at server startup."
            )

        # Decode bytes → PIL Image for the SentenceTransformers pipeline.
        from PIL import Image  # noqa: PLC0415

        try:
            img = Image.open(io.BytesIO(image_bytes))
            img.load()
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"Failed to decode image_bytes as a valid image: {exc}"
            ) from exc

        # Validate that the ingestion worker produced the expected dimensions.
        if img.size != _EXPECTED_SIZE:
            raise ValueError(
                f"Image resolution {img.size} does not match expected "
                f"{_EXPECTED_SIZE}. Ensure ImageHandler.process() ran before "
                "calling embed()."
            )

        logger.debug(
            "Encoding image (mode=%s size=%dx%d bytes=%d)",
            img.mode,
            img.size[0],
            img.size[1],
            len(image_bytes),
        )

        import torch
        import torch.nn.functional as F

        inputs = self._processor(img, return_tensors="pt")
        if self._model.device.type != "cpu":
            inputs = {k: v.to(self._model.device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self._model(**inputs)
            if hasattr(outputs, "pooler_output") and outputs.pooler_output is not None:
                embeddings = outputs.pooler_output
            else:
                embeddings = outputs.last_hidden_state.mean(dim=1)
            
            # Matryoshka dimensionality reduction
            embeddings = embeddings[:, :_EXPECTED_DIM]
            embeddings = F.normalize(embeddings, p=2, dim=1)

        vector: list[float] = embeddings[0].cpu().tolist()

        logger.debug("Image encoded — vector dim=%d", len(vector))
        return vector

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
