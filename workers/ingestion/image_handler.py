"""
image_handler.py — Image preprocessing pipeline for the ingestion worker.

Validates and preprocesses raw image bytes so they are ready to be sent to the
model server's ``POST /embed/image`` endpoint via ``model_client.embed_image()``.

Pipeline:
    raw bytes → validate → open with Pillow → convert to RGB
              → resize to 224×224 (LANCZOS) → encode to JPEG → ImageResult

Supported input formats:
    JPEG, PNG

Output is always a JPEG-encoded 224×224 RGB image, regardless of the
input format.  This keeps the payload to the model server small and avoids
any issues with alpha channels or palette-indexed colours.

Note on concurrency:
    ``ImageHandler.process()`` is a synchronous function because Pillow is
    CPU-bound.  In the async ``main.py`` pipeline call it via
    ``asyncio.to_thread(handler.process, image_bytes, content_type)`` to
    avoid blocking the event loop.

Usage:
    handler = ImageHandler()
    result  = handler.process(image_bytes, content_type="image/jpeg")
    if result.success:
        vector = await model_client.embed_image(result.image_bytes,
                                                result.content_type)
"""

import io
import logging
from dataclasses import dataclass
from typing import Optional

from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Supported input MIME types
# ---------------------------------------------------------------------------

SUPPORTED_IMAGE_TYPES: set[str] = {
    "image/jpeg",
    "image/png",
}


def is_image_supported(content_type: str) -> bool:
    """Return True if *content_type* is a supported image MIME type."""
    return content_type in SUPPORTED_IMAGE_TYPES


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ImageResult:
    """
    Container for the output of ``ImageHandler.process()``.

    Attributes:
        image_bytes:  Preprocessed JPEG bytes, ready to POST to the model
                      server.  Empty on failure.
        content_type: Always ``"image/jpeg"`` on success.
        orig_width:   Original image width in pixels (for logging/debugging).
        orig_height:  Original image height in pixels.
        orig_mode:    Original Pillow mode (e.g. ``"RGB"``, ``"RGBA"``,
                      ``"L"``, ``"P"``).
        success:      False if validation or preprocessing failed.
        error:        Human-readable error message when ``success`` is False.
    """

    image_bytes: bytes
    content_type: str
    orig_width: int
    orig_height: int
    orig_mode: str
    success: bool = True
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Image Handler
# ---------------------------------------------------------------------------

class ImageHandler:
    """
    Stateless image preprocessor for the ingestion worker.

    All configuration is expressed as class-level constants so that
    changing the target resolution or quality only requires editing one
    place.

    Parameters
    ----------
    target_size : tuple[int, int]
        ``(width, height)`` to resize every image to.
        Default ``(224, 224)`` matches the input expected by CLIP-style
        vision models such as ``nomic-embed-vision``.
    jpeg_quality : int
        JPEG encoding quality 1–95.  Default ``90`` gives a good
        quality-to-size ratio while keeping payloads small.
    """

    #: Output resolution expected by the vision model.
    TARGET_SIZE: tuple[int, int] = (224, 224)

    #: All output images are normalised to RGB JPEG regardless of input format.
    TARGET_MODE: str = "RGB"
    OUTPUT_FORMAT: str = "JPEG"

    #: JPEG encoding quality (1–95).  95 is near-lossless; 85–90 is a good
    #: balance of fidelity and size for embedding purposes.
    JPEG_QUALITY: int = 90

    def __init__(
        self,
        target_size: Optional[tuple[int, int]] = None,
        jpeg_quality: Optional[int] = None,
    ) -> None:
        self.target_size = target_size or self.TARGET_SIZE
        self.jpeg_quality = jpeg_quality if jpeg_quality is not None else self.JPEG_QUALITY

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(
        self,
        image_bytes: bytes,
        content_type: str = "image/jpeg",
    ) -> ImageResult:
        """
        Validate and preprocess raw image bytes.

        The returned ``ImageResult.image_bytes`` is a JPEG-encoded
        224×224 RGB image ready for ``model_client.embed_image()``.

        Args:
            image_bytes:  Raw bytes of the image file (e.g. from MinIO).
            content_type: MIME type of the image, e.g. ``"image/png"``.

        Returns:
            ``ImageResult`` with ``success=True`` on success, or
            ``success=False`` and a descriptive ``error`` on failure.
        """
        # --- Guard: empty bytes ----------------------------------------
        if not image_bytes:
            return self._failure(
                error="Empty image bytes provided",
                orig_width=0,
                orig_height=0,
                orig_mode="",
            )

        # --- Guard: unsupported MIME type ------------------------------
        if not is_image_supported(content_type):
            return self._failure(
                error=f"Unsupported image MIME type: {content_type!r}",
                orig_width=0,
                orig_height=0,
                orig_mode="",
            )

        # --- Open with Pillow ------------------------------------------
        try:
            img = Image.open(io.BytesIO(image_bytes))
            # For animated formats (GIF, WebP) grab the first frame only.
            img.load()
        except UnidentifiedImageError:
            return self._failure(
                error="Pillow could not identify the image format "
                      "(file may be corrupt or truncated)",
                orig_width=0,
                orig_height=0,
                orig_mode="",
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(
                error=f"Pillow failed to open image: {exc}",
                orig_width=0,
                orig_height=0,
                orig_mode="",
            )

        # Capture original dimensions and mode for logging
        orig_width, orig_height = img.size
        orig_mode = img.mode

        logger.debug(
            "Opened image: mode=%s size=%dx%d bytes=%d mime=%s",
            orig_mode,
            orig_width,
            orig_height,
            len(image_bytes),
            content_type,
        )

        # --- Convert to RGB -------------------------------------------
        # Handles: RGBA (drops alpha), L (grayscale), P (palette), etc.
        if img.mode != self.TARGET_MODE:
            img = img.convert(self.TARGET_MODE)
            logger.debug("Converted image mode %s → %s", orig_mode, self.TARGET_MODE)

        # --- Resize to target resolution ------------------------------
        if img.size != self.target_size:
            img = img.resize(self.target_size, Image.LANCZOS)
            logger.debug(
                "Resized image %dx%d → %dx%d",
                orig_width,
                orig_height,
                self.target_size[0],
                self.target_size[1],
            )

        # --- Encode to JPEG in-memory ---------------------------------
        buffer = io.BytesIO()
        img.save(buffer, format=self.OUTPUT_FORMAT, quality=self.jpeg_quality)
        output_bytes = buffer.getvalue()

        logger.info(
            "Image preprocessed: %s %dx%d → JPEG 224×224 (%d bytes input, %d bytes output)",
            content_type,
            orig_width,
            orig_height,
            len(image_bytes),
            len(output_bytes),
        )

        return ImageResult(
            image_bytes=output_bytes,
            content_type="image/jpeg",
            orig_width=orig_width,
            orig_height=orig_height,
            orig_mode=orig_mode,
            success=True,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _failure(
        error: str,
        orig_width: int,
        orig_height: int,
        orig_mode: str,
    ) -> ImageResult:
        """Return a failed ``ImageResult`` with a descriptive error."""
        logger.warning("Image preprocessing failed: %s", error)
        return ImageResult(
            image_bytes=b"",
            content_type="",
            orig_width=orig_width,
            orig_height=orig_height,
            orig_mode=orig_mode,
            success=False,
            error=error,
        )
