"""
image_handler.py — Image preprocessing pipeline for the ingestion worker.

Validates and preprocesses raw image bytes so they are ready to be sent to the
model server's ``POST /embed/image`` endpoint via ``model_client.embed_image()``.

Pipeline:
    raw bytes → validate → size guard → open with Pillow
              → EXIF orientation fix → center-crop to square
              → resize to 224×224 (LANCZOS) → encode to JPEG → ImageResult

Supported input formats:
    JPEG, PNG

Output is always a JPEG-encoded 224×224 RGB image, regardless of the
input format.  This keeps the payload to the model server small and avoids
any issues with alpha channels or palette-indexed colours.

Aspect-ratio strategy — center-crop:
    The image is first cropped to a square around its centre, then resized to
    224×224.  This is the standard preprocessing for CLIP/ViT-based vision
    models: subjects are almost always centred, and it avoids the black padding
    dead-space that letterboxing would introduce.

EXIF metadata:
    JPEG/TIFF EXIF data is extracted and surfaced in ``ImageResult.exif_metadata``
    so the ingestion worker can use it to build richer BM25 text (e.g. camera
    model, capture date, GPS location).  Orientation is automatically corrected
    before any resizing step.

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
        # result.exif_metadata contains camera, date, GPS, etc.
"""

import io
import logging
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Supported input MIME types
# ---------------------------------------------------------------------------

SUPPORTED_IMAGE_TYPES: set[str] = {
    "image/jpeg",
    "image/png",
}

# ---------------------------------------------------------------------------
# Default file-size guard (bytes).  Override via ImageHandler(max_file_bytes=…).
# ---------------------------------------------------------------------------

DEFAULT_MAX_FILE_BYTES: int = 50 * 1024 * 1024  # 50 MB


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
        image_bytes:    Preprocessed JPEG bytes, ready to POST to the model
                        server.  Empty on failure.
        content_type:   Always ``"image/jpeg"`` on success.
        orig_width:     Original image width in pixels (after EXIF orientation
                        correction, before cropping).
        orig_height:    Original image height in pixels (after EXIF orientation
                        correction, before cropping).
        orig_mode:      Original Pillow mode (e.g. ``"RGB"``, ``"RGBA"``,
                        ``"L"``, ``"P"``).
        exif_metadata:  Dict of EXIF fields extracted from the original image.
                        Keys are human-readable strings (e.g. ``"camera_make"``,
                        ``"capture_date"``, ``"gps_lat"``, ``"gps_lon"``).
                        Empty dict if no EXIF data is available.
        success:        False if validation or preprocessing failed.
        error:          Human-readable error message when ``success`` is False.
    """

    image_bytes:   bytes
    content_type:  str
    orig_width:    int
    orig_height:   int
    orig_mode:     str
    exif_metadata: dict          = field(default_factory=dict)
    success:       bool          = True
    error:         Optional[str] = None


# ---------------------------------------------------------------------------
# EXIF helpers
# ---------------------------------------------------------------------------

# Mapping from EXIF tag IDs to human-readable keys.
# Only a curated subset that is useful for BM25 enrichment is captured.
_EXIF_TAG_MAP: dict[int, str] = {
    271:   "camera_make",       # Make
    272:   "camera_model",      # Model
    306:   "datetime",          # DateTime
    36867: "capture_date",      # DateTimeOriginal
    36868: "digitised_date",    # DateTimeDigitized
    37386: "focal_length",      # FocalLength
    37510: "user_comment",      # UserComment
}

# GPS sub-tag IDs
_GPS_LAT_TAG = 2
_GPS_LON_TAG = 4
_GPS_LAT_REF = 1
_GPS_LON_REF = 3
_GPS_ALT_TAG = 6
_GPS_ALT_REF = 5


def _dms_to_decimal(dms) -> Optional[float]:
    """
    Convert an EXIF DMS value to decimal degrees.

    Pillow's ``_getexif()`` returns GPS values as plain float tuples:
        ``(degrees, minutes, seconds)`` e.g. ``(37.0, 46.0, 29.64)``

    Some other decoders return rational pairs:
        ``((D, N), (M, N), (S, N))`` e.g. ``((37, 1), (46, 1), (2964, 100))``

    Both forms are handled.
    """
    try:
        def _to_float(v):
            """
            Coerce a GPS DMS element to float.

            Pillow's _getexif() returns GPS values as IFDRational objects
            (PIL.TiffImagePlugin.IFDRational) that support float() but are
            not instances of int or float.  We call float() first, falling
            back to rational-pair arithmetic for other decoder formats.
            """
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
            # rational pair (numerator, denominator) from some decoders
            try:
                return v[0] / v[1]
            except Exception:
                raise TypeError(f"Cannot convert GPS DMS element to float: {v!r}")

        degrees = _to_float(dms[0])
        minutes = _to_float(dms[1])
        seconds = _to_float(dms[2])
        return degrees + (minutes / 60.0) + (seconds / 3600.0)
    except Exception:
        return None


def extract_exif(img: Image.Image) -> dict:
    """
    Extract a curated subset of EXIF metadata from a Pillow image.

    Works for JPEG images that carry EXIF data.  Returns an empty dict for
    PNG (which stores metadata differently) or images with no EXIF.

    Returns:
        A flat dict with string keys and string/float values, e.g.::

            {
                "camera_make":  "Canon",
                "camera_model": "EOS R5",
                "capture_date": "2024:03:15 14:22:05",
                "gps_lat":      37.7749,
                "gps_lon":      -122.4194,
                "gps_alt_m":    12.5,
            }
    """
    result: dict = {}
    try:
        exif_data = img._getexif()  # type: ignore[attr-defined]
    except AttributeError:
        # PIL image type doesn't expose _getexif (e.g. PNG loaded via Image.open)
        return result
    except Exception as exc:
        logger.debug("EXIF extraction skipped: %s", exc)
        return result

    if not exif_data:
        return result

    # --- Scalar EXIF tags ---------------------------------------------------
    for tag_id, key in _EXIF_TAG_MAP.items():
        value = exif_data.get(tag_id)
        if value is not None:
            try:
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="replace").strip("\x00")
                result[key] = str(value).strip()
            except Exception:
                pass

    # --- GPS sub-IFD --------------------------------------------------------
    gps_ifd = exif_data.get(34853)  # GPSInfo tag
    if gps_ifd:
        try:
            lat_raw = gps_ifd.get(_GPS_LAT_TAG)
            lon_raw = gps_ifd.get(_GPS_LON_TAG)
            lat_ref = gps_ifd.get(_GPS_LAT_REF, "N")
            lon_ref = gps_ifd.get(_GPS_LON_REF, "E")

            if lat_raw and lon_raw:
                lat = _dms_to_decimal(lat_raw)
                lon = _dms_to_decimal(lon_raw)
                if lat is not None and lon is not None:
                    if lat_ref == "S":
                        lat = -lat
                    if lon_ref == "W":
                        lon = -lon
                    result["gps_lat"] = round(lat, 6)
                    result["gps_lon"] = round(lon, 6)

            alt_raw = gps_ifd.get(_GPS_ALT_TAG)
            alt_ref = gps_ifd.get(_GPS_ALT_REF, 0)
            if alt_raw:
                alt_m = alt_raw[0] / alt_raw[1]
                if alt_ref == 1:
                    alt_m = -alt_m
                result["gps_alt_m"] = round(alt_m, 1)
        except Exception as exc:
            logger.debug("GPS extraction failed: %s", exc)

    return result


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
        ``(width, height)`` to resize every image to after center-cropping.
        Default ``(224, 224)`` matches the input expected by CLIP-style
        vision models such as ``nomic-embed-vision``.
    jpeg_quality : int
        JPEG encoding quality 1–95.  Default ``90`` gives a good
        quality-to-size ratio while keeping payloads small.
    max_file_bytes : int
        Maximum raw file size accepted.  Files larger than this are
        rejected before Pillow even attempts to open them.
        Default is ``50 * 1024 * 1024`` (50 MB).  Set to ``0`` to disable.
    """

    #: Output resolution expected by the vision model.
    TARGET_SIZE: tuple[int, int] = (224, 224)

    #: All output images are normalised to RGB JPEG regardless of input format.
    TARGET_MODE: str    = "RGB"
    OUTPUT_FORMAT: str  = "JPEG"

    #: JPEG encoding quality (1–95).
    JPEG_QUALITY: int = 90

    #: Default maximum raw file size (50 MB).
    MAX_FILE_BYTES: int = DEFAULT_MAX_FILE_BYTES

    def __init__(
        self,
        target_size:    Optional[tuple[int, int]] = None,
        jpeg_quality:   Optional[int]             = None,
        max_file_bytes: Optional[int]             = None,
    ) -> None:
        self.target_size    = target_size    or self.TARGET_SIZE
        self.jpeg_quality   = jpeg_quality   if jpeg_quality   is not None else self.JPEG_QUALITY
        self.max_file_bytes = max_file_bytes if max_file_bytes is not None else self.MAX_FILE_BYTES

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(
        self,
        image_bytes:  bytes,
        content_type: str = "image/jpeg",
    ) -> ImageResult:
        """
        Validate and preprocess raw image bytes.

        The returned ``ImageResult.image_bytes`` is a JPEG-encoded
        224×224 RGB image ready for ``model_client.embed_image()``.

        Processing steps:
            1. Reject empty bytes.
            2. Reject unsupported MIME types (only JPEG and PNG accepted).
            3. Reject files exceeding ``max_file_bytes``.
            4. Open with Pillow.
            5. Auto-correct EXIF orientation.
            6. Extract EXIF metadata.
            7. Convert colour mode to RGB.
            8. Center-crop to square (shortest dimension), then resize to
               ``target_size`` with LANCZOS resampling.
            9. Encode as JPEG.

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
                orig_width=0, orig_height=0, orig_mode="",
            )

        # --- Guard: unsupported MIME type ------------------------------
        if not is_image_supported(content_type):
            return self._failure(
                error=f"Unsupported image MIME type: {content_type!r}",
                orig_width=0, orig_height=0, orig_mode="",
            )

        # --- Guard: file size ------------------------------------------
        if self.max_file_bytes > 0 and len(image_bytes) > self.max_file_bytes:
            limit_mb  = self.max_file_bytes / (1024 * 1024)
            actual_mb = len(image_bytes) / (1024 * 1024)
            return self._failure(
                error=(
                    f"Image too large: {actual_mb:.1f} MB exceeds "
                    f"{limit_mb:.0f} MB limit"
                ),
                orig_width=0, orig_height=0, orig_mode="",
            )

        # --- Open with Pillow ------------------------------------------
        try:
            img = Image.open(io.BytesIO(image_bytes))
            img.load()
        except UnidentifiedImageError:
            return self._failure(
                error="Pillow could not identify the image format "
                      "(file may be corrupt or truncated)",
                orig_width=0, orig_height=0, orig_mode="",
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(
                error=f"Pillow failed to open image: {exc}",
                orig_width=0, orig_height=0, orig_mode="",
            )

        # --- Extract EXIF metadata ------------------------------------
        # IMPORTANT: must happen BEFORE exif_transpose().  Pillow's
        # exif_transpose() returns a plain Image object that loses the
        # JPEG-specific _getexif() method, making extract_exif() return {}.
        exif_metadata = extract_exif(img)
        if exif_metadata:
            logger.debug("EXIF keys: %s", list(exif_metadata.keys()))

        # --- Auto-correct EXIF orientation ----------------------------
        # Must happen before reading dimensions — EXIF orientation can
        # swap width/height, so orig_width/orig_height should reflect
        # the visually upright orientation.
        try:
            img = ImageOps.exif_transpose(img)
        except Exception as exc:
            logger.debug("EXIF transpose skipped: %s", exc)

        # Capture original dimensions and mode after orientation correction
        orig_width, orig_height = img.size
        orig_mode = img.mode

        logger.debug(
            "Opened image: mode=%s size=%dx%d bytes=%d mime=%s",
            orig_mode, orig_width, orig_height, len(image_bytes), content_type,
        )

        # --- Convert to RGB -------------------------------------------
        # Handles: RGBA (drops alpha), L (grayscale), P (palette), etc.
        if img.mode != self.TARGET_MODE:
            img = img.convert(self.TARGET_MODE)
            logger.debug("Converted mode %s → %s", orig_mode, self.TARGET_MODE)

        # --- Center-crop to square ------------------------------------
        # Crop to the largest centred square before resizing.  This
        # preserves the aspect ratio of the main subject (usually centred)
        # and avoids the black-bar dead-space of letterboxing.
        w, h = img.size
        if w != h:
            min_dim = min(w, h)
            left    = (w - min_dim) // 2
            top     = (h - min_dim) // 2
            img     = img.crop((left, top, left + min_dim, top + min_dim))
            logger.debug("Center-cropped %dx%d → %dx%d", w, h, min_dim, min_dim)

        # --- Resize to target resolution ------------------------------
        if img.size != self.target_size:
            img = img.resize(self.target_size, Image.LANCZOS)
            logger.debug(
                "Resized → %dx%d",
                self.target_size[0], self.target_size[1],
            )

        # --- Encode to JPEG in-memory ---------------------------------
        buffer = io.BytesIO()
        img.save(buffer, format=self.OUTPUT_FORMAT, quality=self.jpeg_quality)
        output_bytes = buffer.getvalue()

        logger.info(
            "Image preprocessed: %s %dx%d → JPEG 224×224 "
            "(%d bytes input, %d bytes output, exif_keys=%s)",
            content_type, orig_width, orig_height,
            len(image_bytes), len(output_bytes),
            list(exif_metadata.keys()) or "none",
        )

        return ImageResult(
            image_bytes   = output_bytes,
            content_type  = "image/jpeg",
            orig_width    = orig_width,
            orig_height   = orig_height,
            orig_mode     = orig_mode,
            exif_metadata = exif_metadata,
            success       = True,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _failure(
        error:       str,
        orig_width:  int,
        orig_height: int,
        orig_mode:   str,
    ) -> ImageResult:
        """Return a failed ``ImageResult`` with a descriptive error."""
        logger.warning("Image preprocessing failed: %s", error)
        return ImageResult(
            image_bytes   = b"",
            content_type  = "",
            orig_width    = orig_width,
            orig_height   = orig_height,
            orig_mode     = orig_mode,
            exif_metadata = {},
            success       = False,
            error         = error,
        )
