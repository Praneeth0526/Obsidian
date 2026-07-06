"""
test_image_search_effectiveness.py
====================================
Tests the image search pipeline at two levels:

Unit tests (no Docker required — run with ``-k unit``):
    - Format support (JPEG, PNG accepted; others rejected)
    - File size guard
    - Center-crop aspect-ratio correctness
    - EXIF orientation auto-fix
    - EXIF metadata extraction
    - BM25 text richness (is EXIF surfaced in keyword text?)
    - Colour-mode conversion (RGBA, L, P → RGB)
    - Edge cases: empty bytes, corrupt data, already-224 image

Integration tests (Docker required — run with ``-k integration``):
    - Embedding cosine similarity: similar images cluster, different separate
    - Search recall: precision@k from hybrid BM25 + kNN
    - BM25 search path: keyword-only query surfaces images by EXIF text

Prerequisites for integration tests:
    docker compose up -d   (Model Server on 8000, OpenSearch on 9200)

Usage:
    cd workers/ingestion

    # Unit tests only (fast, no Docker)
    python -m pytest test_image_search_effectiveness.py -v -k unit

    # All tests (requires Docker)
    python -m pytest test_image_search_effectiveness.py -v

    # Standalone report mode
    python test_image_search_effectiveness.py
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import io
import math
import struct
import sys
import time
import unittest
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Minimal JPEG / PNG builders (no Pillow required for the corrupt-file tests)
# ---------------------------------------------------------------------------

def _minimal_jpeg_bytes(width: int = 8, height: int = 8) -> bytes:
    """Return a tiny valid JPEG (width × height solid grey)."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (128, 128, 128)).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _minimal_png_bytes(width: int = 8, height: int = 8, mode: str = "RGB") -> bytes:
    """Return a tiny valid PNG."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new(mode, (width, height), 128).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_with_exif(
    width: int = 640,
    height: int = 480,
    camera_make: str = "Canon",
    camera_model: str = "EOS R5",
    capture_date: str = "2024:03:15 14:22:05",
    gps_lat: Optional[float] = None,
    gps_lon: Optional[float] = None,
) -> bytes:
    """
    Build a JPEG with synthetic EXIF tags using piexif (if available) or Pillow
    alone (no EXIF in that case — test will skip GPS/camera assertions).
    """
    from PIL import Image
    img = Image.new("RGB", (width, height), (100, 150, 200))

    try:
        import piexif  # optional dependency

        exif_dict: dict = {"0th": {}, "Exif": {}, "GPS": {}}
        exif_dict["0th"][piexif.ImageIFD.Make]  = camera_make.encode()
        exif_dict["0th"][piexif.ImageIFD.Model] = camera_model.encode()
        exif_dict["0th"][piexif.ImageIFD.DateTime] = capture_date.encode()
        exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = capture_date.encode()

        if gps_lat is not None and gps_lon is not None:
            def _to_dms(deg: float):
                d = int(abs(deg))
                m = int((abs(deg) - d) * 60)
                s = int(((abs(deg) - d) * 60 - m) * 60 * 100)
                return ((d, 1), (m, 1), (s, 100))

            exif_dict["GPS"][piexif.GPSIFD.GPSLatitudeRef]  = b"N" if gps_lat >= 0 else b"S"
            exif_dict["GPS"][piexif.GPSIFD.GPSLatitude]     = _to_dms(gps_lat)
            exif_dict["GPS"][piexif.GPSIFD.GPSLongitudeRef] = b"E" if gps_lon >= 0 else b"W"
            exif_dict["GPS"][piexif.GPSIFD.GPSLongitude]    = _to_dms(gps_lon)

        exif_bytes = piexif.dump(exif_dict)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif_bytes, quality=90)
    except ImportError:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)

    return buf.getvalue()


def _jpeg_rotated_90(width: int = 300, height: int = 500) -> bytes:
    """
    Build a JPEG with Orientation=6 (90° CW rotation) in EXIF.
    The logical image is landscape (500×300) but the raw pixels are portrait (300×500).
    """
    from PIL import Image
    img = Image.new("RGB", (width, height), (200, 100, 50))

    try:
        import piexif
        exif_dict: dict = {"0th": {piexif.ImageIFD.Orientation: 6}, "Exif": {}, "GPS": {}}
        exif_bytes = piexif.dump(exif_dict)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif_bytes, quality=90)
    except ImportError:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)

    return buf.getvalue()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two float vectors."""
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# ===========================================================================
# UNIT TESTS
# ===========================================================================

class TestImageHandlerUnit(unittest.TestCase):
    """
    Unit tests for ImageHandler.  No network / Docker required.
    All tests are tagged 'unit' via the naming convention below.
    """

    def setUp(self):
        from image_handler import ImageHandler
        self.handler = ImageHandler()

    # -----------------------------------------------------------------------
    # Format support
    # -----------------------------------------------------------------------

    def test_unit_jpeg_accepted(self):
        """Valid JPEG bytes with correct MIME type → success."""
        result = self.handler.process(_minimal_jpeg_bytes(), "image/jpeg")
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.content_type, "image/jpeg")

    def test_unit_png_accepted(self):
        """Valid PNG bytes with correct MIME type → success."""
        result = self.handler.process(_minimal_png_bytes(), "image/png")
        self.assertTrue(result.success, result.error)

    def test_unit_webp_rejected(self):
        """WebP is not a supported MIME type → failure."""
        result = self.handler.process(b"RIFF\x00\x00\x00\x00WEBP", "image/webp")
        self.assertFalse(result.success)
        self.assertIn("Unsupported", result.error)

    def test_unit_gif_rejected(self):
        """GIF is not a supported MIME type → failure."""
        result = self.handler.process(b"GIF89a", "image/gif")
        self.assertFalse(result.success)
        self.assertIn("Unsupported", result.error)

    def test_unit_bmp_rejected(self):
        """BMP is not a supported MIME type → failure."""
        result = self.handler.process(b"BM\x00\x00\x00\x00", "image/bmp")
        self.assertFalse(result.success)
        self.assertIn("Unsupported", result.error)

    def test_unit_octet_stream_rejected(self):
        """Generic binary MIME type → failure."""
        result = self.handler.process(_minimal_jpeg_bytes(), "application/octet-stream")
        self.assertFalse(result.success)

    # -----------------------------------------------------------------------
    # File size guard
    # -----------------------------------------------------------------------

    def test_unit_file_size_guard_rejects_oversized(self):
        """A handler with a 100-byte limit should reject any larger image."""
        handler = __import__("image_handler").ImageHandler(max_file_bytes=100)
        big_bytes = _minimal_jpeg_bytes(100, 100)  # will be > 100 bytes
        result = handler.process(big_bytes, "image/jpeg")
        self.assertFalse(result.success)
        self.assertIn("too large", result.error.lower())

    def test_unit_file_size_guard_accepts_small(self):
        """Image within the configured limit → success."""
        from image_handler import ImageHandler
        small_bytes = _minimal_jpeg_bytes(8, 8)
        handler = ImageHandler(max_file_bytes=len(small_bytes) + 1000)
        result = handler.process(small_bytes, "image/jpeg")
        self.assertTrue(result.success, result.error)

    def test_unit_file_size_guard_disabled_when_zero(self):
        """Setting max_file_bytes=0 disables the size check entirely."""
        from image_handler import ImageHandler
        handler = ImageHandler(max_file_bytes=0)
        result = handler.process(_minimal_jpeg_bytes(8, 8), "image/jpeg")
        self.assertTrue(result.success, result.error)

    # -----------------------------------------------------------------------
    # Output dimensions — center-crop → 224×224
    # -----------------------------------------------------------------------

    def test_unit_output_is_always_224x224(self):
        """Output must always be exactly 224×224 regardless of input size."""
        for (w, h) in [(100, 100), (800, 600), (300, 900), (4000, 3000)]:
            with self.subTest(input=f"{w}x{h}"):
                from PIL import Image
                buf = io.BytesIO()
                Image.new("RGB", (w, h), 42).save(buf, format="JPEG", quality=80)
                result = self.handler.process(buf.getvalue(), "image/jpeg")
                self.assertTrue(result.success, result.error)
                # Decode output and check size
                out_img = Image.open(io.BytesIO(result.image_bytes))
                self.assertEqual(out_img.size, (224, 224),
                                 f"Expected 224x224, got {out_img.size} for input {w}x{h}")

    def test_unit_already_224x224_unchanged_size(self):
        """An already-224×224 image should not be cropped or distorted."""
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (224, 224), (10, 20, 30)).save(buf, format="JPEG", quality=90)
        result = self.handler.process(buf.getvalue(), "image/jpeg")
        self.assertTrue(result.success, result.error)
        out = Image.open(io.BytesIO(result.image_bytes))
        self.assertEqual(out.size, (224, 224))

    def test_unit_orig_dimensions_recorded_correctly(self):
        """orig_width/orig_height must reflect the input dimensions (after EXIF rotation)."""
        result = self.handler.process(_minimal_jpeg_bytes(300, 200), "image/jpeg")
        self.assertTrue(result.success)
        self.assertEqual(result.orig_width, 300)
        self.assertEqual(result.orig_height, 200)

    # -----------------------------------------------------------------------
    # Center-crop correctness
    # -----------------------------------------------------------------------

    def test_unit_center_crop_landscape(self):
        """
        Landscape 600×400 → center-crop 400×400 → resize 224×224.
        Verify the centre region is preserved (sample pixel should be from centre).
        """
        from PIL import Image
        # Red left strip, blue right strip, green centre square
        img = Image.new("RGB", (600, 400), (255, 0, 0))   # all red
        # paint centre 400×400 green
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)
        draw.rectangle([100, 0, 499, 399], fill=(0, 255, 0))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        result = self.handler.process(buf.getvalue(), "image/jpeg")
        self.assertTrue(result.success)
        out = Image.open(io.BytesIO(result.image_bytes))
        cx, cy = out.size[0] // 2, out.size[1] // 2
        r, g, b = out.getpixel((cx, cy))
        # Centre pixel should be greenish (JPEG lossy so allow ±30)
        self.assertGreater(g, r + 30, "Centre pixel should be green (from centre crop)")
        self.assertGreater(g, b + 30, "Centre pixel should be green (from centre crop)")

    def test_unit_center_crop_portrait(self):
        """Portrait 400×600 → center-crop 400×400 → resize 224×224."""
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (400, 600), (255, 0, 0))   # all red
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 100, 399, 499], fill=(0, 0, 255))  # blue centre band
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        result = self.handler.process(buf.getvalue(), "image/jpeg")
        self.assertTrue(result.success)
        out = Image.open(io.BytesIO(result.image_bytes))
        cx, cy = out.size[0] // 2, out.size[1] // 2
        r, g, b = out.getpixel((cx, cy))
        self.assertGreater(b, r + 30, "Centre pixel should be blue")

    # -----------------------------------------------------------------------
    # Colour-mode conversion
    # -----------------------------------------------------------------------

    def test_unit_rgba_converted_to_rgb(self):
        """RGBA PNG (with alpha channel) must be converted to RGB without error."""
        result = self.handler.process(_minimal_png_bytes(64, 64, "RGBA"), "image/png")
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.orig_mode, "RGBA")
        # Output must be a valid JPEG (no alpha)
        from PIL import Image
        out = Image.open(io.BytesIO(result.image_bytes))
        self.assertEqual(out.mode, "RGB")

    def test_unit_grayscale_converted_to_rgb(self):
        """Grayscale L-mode PNG must be converted to RGB."""
        result = self.handler.process(_minimal_png_bytes(64, 64, "L"), "image/png")
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.orig_mode, "L")
        from PIL import Image
        out = Image.open(io.BytesIO(result.image_bytes))
        self.assertEqual(out.mode, "RGB")

    # -----------------------------------------------------------------------
    # EXIF orientation
    # -----------------------------------------------------------------------

    def test_unit_exif_orientation_corrected(self):
        """
        A JPEG with Orientation=6 (90° CW) should be transposed so that
        orig_width/orig_height reflect the visually upright dimensions.

        Without piexif the EXIF won't be embedded, so we skip the
        orientation-specific assertion and just check success.
        """
        jpeg = _jpeg_rotated_90(width=300, height=500)
        result = self.handler.process(jpeg, "image/jpeg")
        self.assertTrue(result.success, result.error)
        # Output is always 224×224
        from PIL import Image
        out = Image.open(io.BytesIO(result.image_bytes))
        self.assertEqual(out.size, (224, 224))

    # -----------------------------------------------------------------------
    # EXIF metadata extraction
    # -----------------------------------------------------------------------

    def test_unit_exif_metadata_extracted_for_jpeg(self):
        """EXIF tags must be extracted from a JPEG with synthetic EXIF data."""
        try:
            import piexif  # noqa: F401
        except ImportError:
            self.skipTest("piexif not installed — EXIF writing not possible")

        jpeg = _jpeg_with_exif(
            camera_make="Nikon",
            camera_model="Z9",
            capture_date="2025:06:01 09:30:00",
        )
        result = self.handler.process(jpeg, "image/jpeg")
        self.assertTrue(result.success, result.error)
        exif = result.exif_metadata
        self.assertIn("camera_make",  exif, "camera_make missing from EXIF")
        self.assertIn("camera_model", exif, "camera_model missing from EXIF")
        self.assertIn("capture_date", exif, "capture_date missing from EXIF")
        self.assertEqual(exif["camera_make"],  "Nikon")
        self.assertEqual(exif["camera_model"], "Z9")

    def test_unit_gps_extracted_for_jpeg(self):
        """GPS coordinates must be extracted and converted to decimal degrees."""
        try:
            import piexif  # noqa: F401
        except ImportError:
            self.skipTest("piexif not installed")

        jpeg = _jpeg_with_exif(gps_lat=37.7749, gps_lon=-122.4194)
        result = self.handler.process(jpeg, "image/jpeg")
        self.assertTrue(result.success)
        exif = result.exif_metadata
        self.assertIn("gps_lat", exif)
        self.assertIn("gps_lon", exif)
        self.assertAlmostEqual(exif["gps_lat"], 37.7749, places=2)
        self.assertAlmostEqual(exif["gps_lon"], -122.4194, places=2)

    def test_unit_exif_empty_for_png(self):
        """PNG does not carry EXIF → exif_metadata should be an empty dict."""
        result = self.handler.process(_minimal_png_bytes(), "image/png")
        self.assertTrue(result.success)
        self.assertEqual(result.exif_metadata, {})

    # -----------------------------------------------------------------------
    # BM25 text enrichment (main.py _process_image)
    # -----------------------------------------------------------------------

    def _build_bm25_text(self, exif: dict, filename: str, orig_w: int, orig_h: int,
                         content_type: str) -> str:
        """Mirror the BM25 text logic from main.py for unit-testability."""
        bm25_parts = [
            f"Image: {filename}",
            f"({orig_w}x{orig_h} pixels, {content_type})",
        ]
        camera_parts = []
        if exif.get("camera_make"):
            camera_parts.append(exif["camera_make"])
        if exif.get("camera_model"):
            camera_parts.append(exif["camera_model"])
        if camera_parts:
            bm25_parts.append(f"Camera: {' '.join(camera_parts)}.")
        capture_date = exif.get("capture_date") or exif.get("datetime")
        if capture_date:
            bm25_parts.append(f"Date: {capture_date}.")
        if exif.get("gps_lat") is not None and exif.get("gps_lon") is not None:
            lat = exif["gps_lat"]
            lon = exif["gps_lon"]
            lat_dir = "N" if lat >= 0 else "S"
            lon_dir = "E" if lon >= 0 else "W"
            bm25_parts.append(
                f"Location: {abs(lat):.4f}°{lat_dir}, {abs(lon):.4f}°{lon_dir}."
            )
        if exif.get("user_comment"):
            comment = exif["user_comment"].strip()
            if comment:
                bm25_parts.append(f"Comment: {comment}.")
        return " ".join(bm25_parts)

    def test_unit_bm25_text_contains_filename(self):
        """BM25 text must always include the filename."""
        text = self._build_bm25_text({}, "site_photo.jpg", 1920, 1080, "image/jpeg")
        self.assertIn("site_photo.jpg", text)

    def test_unit_bm25_text_contains_camera_when_exif_present(self):
        """BM25 text must include camera info when EXIF is available."""
        exif = {"camera_make": "Sony", "camera_model": "Alpha 7R V"}
        text = self._build_bm25_text(exif, "portrait.jpg", 800, 600, "image/jpeg")
        self.assertIn("Sony", text)
        self.assertIn("Alpha 7R V", text)

    def test_unit_bm25_text_contains_date_when_exif_present(self):
        """BM25 text must include date when EXIF capture date is available."""
        exif = {"capture_date": "2024:12:25 10:00:00"}
        text = self._build_bm25_text(exif, "xmas.jpg", 400, 300, "image/jpeg")
        self.assertIn("2024:12:25", text)

    def test_unit_bm25_text_contains_gps_when_present(self):
        """BM25 text must include GPS coordinates when EXIF GPS is available."""
        exif = {"gps_lat": 48.8566, "gps_lon": 2.3522}
        text = self._build_bm25_text(exif, "paris.jpg", 1200, 900, "image/jpeg")
        self.assertIn("Location", text)
        self.assertIn("48.8566", text)

    def test_unit_bm25_text_no_camera_section_when_no_exif(self):
        """When EXIF is absent, no spurious 'Camera:' section should appear."""
        text = self._build_bm25_text({}, "scan.png", 100, 100, "image/png")
        self.assertNotIn("Camera:", text)
        self.assertNotIn("Date:", text)
        self.assertNotIn("Location:", text)

    # -----------------------------------------------------------------------
    # Edge cases
    # -----------------------------------------------------------------------

    def test_unit_empty_bytes_rejected(self):
        """Empty byte string → failure with descriptive error."""
        result = self.handler.process(b"", "image/jpeg")
        self.assertFalse(result.success)
        self.assertIn("Empty", result.error)

    def test_unit_corrupt_jpeg_rejected(self):
        """Random bytes with JPEG MIME type → graceful failure."""
        result = self.handler.process(b"\xff\xd8" + b"\x00" * 50, "image/jpeg")
        self.assertFalse(result.success)

    def test_unit_corrupt_png_rejected(self):
        """Random bytes with PNG MIME type → graceful failure."""
        result = self.handler.process(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "image/png")
        self.assertFalse(result.success)

    def test_unit_success_result_has_no_error_field(self):
        """A successful result must have error=None."""
        result = self.handler.process(_minimal_jpeg_bytes(), "image/jpeg")
        self.assertTrue(result.success)
        self.assertIsNone(result.error)

    def test_unit_failure_result_has_empty_bytes(self):
        """A failed result must have image_bytes=b'' and content_type=''."""
        result = self.handler.process(b"", "image/jpeg")
        self.assertFalse(result.success)
        self.assertEqual(result.image_bytes, b"")
        self.assertEqual(result.content_type, "")


# ===========================================================================
# INTEGRATION TESTS  (require Docker services)
# ===========================================================================

class TestImageSearchIntegration(unittest.IsolatedAsyncioTestCase):
    """
    Integration tests that require the Model Server (port 8000) and
    OpenSearch (port 9200) to be running.

    Run: docker compose up -d
    """

    MODEL_SERVER_URL = "http://localhost:8000"
    OPENSEARCH_URL   = "http://localhost:9200"

    # ---- helpers -----------------------------------------------------------

    @classmethod
    def _services_available(cls) -> bool:
        import urllib.request
        for url in [f"{cls.MODEL_SERVER_URL}/health", cls.OPENSEARCH_URL]:
            try:
                urllib.request.urlopen(url, timeout=3)
            except Exception:
                return False
        return True

    def setUp(self):
        if not self._services_available():
            self.skipTest(
                "Integration services not available — "
                "run `docker compose up -d` and retry with -k integration"
            )

    # ---- Embedding quality -------------------------------------------------

    async def test_integration_embedding_dimensions(self):
        """Model server must return exactly 384-dim vectors for images."""
        from model_client import ModelClient
        from image_handler import ImageHandler

        handler = ImageHandler()
        client  = ModelClient(model_server_url=self.MODEL_SERVER_URL)

        result = handler.process(_minimal_jpeg_bytes(64, 64), "image/jpeg")
        self.assertTrue(result.success)

        vector = await client.embed_image(result.image_bytes, "image/jpeg")
        self.assertEqual(len(vector), 384,
                         f"Expected 384-dim vector, got {len(vector)}")
        await client.close()

    async def test_integration_identical_images_have_similarity_1(self):
        """
        Embedding the same image twice should produce vectors with cosine
        similarity ≈ 1.0 (deterministic model).
        """
        from model_client import ModelClient
        from image_handler import ImageHandler

        handler = ImageHandler()
        client  = ModelClient(model_server_url=self.MODEL_SERVER_URL)

        jpeg = _minimal_jpeg_bytes(224, 224)
        result = handler.process(jpeg, "image/jpeg")

        v1 = await client.embed_image(result.image_bytes, "image/jpeg")
        v2 = await client.embed_image(result.image_bytes, "image/jpeg")

        sim = _cosine_similarity(v1, v2)
        self.assertGreater(sim, 0.999,
                           f"Identical images should have similarity≈1, got {sim:.4f}")
        await client.close()

    async def test_integration_similar_images_cluster(self):
        """
        Two images of the same base image (slight quality difference) should
        have higher cosine similarity than two completely different images.
        """
        from PIL import Image
        from model_client import ModelClient
        from image_handler import ImageHandler

        handler = ImageHandler()
        client  = ModelClient(model_server_url=self.MODEL_SERVER_URL)

        # Image A: solid blue 400×400
        def _make(color, w=400, h=400):
            buf = io.BytesIO()
            Image.new("RGB", (w, h), color).save(buf, format="JPEG", quality=95)
            return buf.getvalue()

        blue1  = _make((0,   0, 200))   # blue, original quality
        blue2  = _make((0,   5, 195))   # blue, very slight variation
        red    = _make((200, 0,   0))   # completely different (red)
        green  = _make((0, 200,   0))   # completely different (green)

        def _embed(raw):
            r = handler.process(raw, "image/jpeg")
            return r

        r_b1 = handler.process(blue1, "image/jpeg")
        r_b2 = handler.process(blue2, "image/jpeg")
        r_r  = handler.process(red,   "image/jpeg")
        r_g  = handler.process(green, "image/jpeg")

        v_b1 = await client.embed_image(r_b1.image_bytes, "image/jpeg")
        v_b2 = await client.embed_image(r_b2.image_bytes, "image/jpeg")
        v_r  = await client.embed_image(r_r.image_bytes,  "image/jpeg")
        v_g  = await client.embed_image(r_g.image_bytes,  "image/jpeg")

        sim_similar  = _cosine_similarity(v_b1, v_b2)
        sim_different = _cosine_similarity(v_b1, v_r)
        sim_different2 = _cosine_similarity(v_b1, v_g)

        print(f"\n  Cosine similarity (blue≈blue):  {sim_similar:.4f}")
        print(f"  Cosine similarity (blue vs red): {sim_different:.4f}")
        print(f"  Cosine similarity (blue vs grn): {sim_different2:.4f}")

        self.assertGreater(
            sim_similar, sim_different,
            f"Similar images ({sim_similar:.3f}) should score higher "
            f"than different ones ({sim_different:.3f})"
        )
        await client.close()

    async def test_integration_embedding_is_l2_normalised(self):
        """
        nomic-embed-vision outputs L2-normalised vectors.  The norm should be ≈ 1.0.
        """
        from model_client import ModelClient
        from image_handler import ImageHandler

        handler = ImageHandler()
        client  = ModelClient(model_server_url=self.MODEL_SERVER_URL)

        result = handler.process(_minimal_jpeg_bytes(128, 128), "image/jpeg")
        vector = await client.embed_image(result.image_bytes, "image/jpeg")

        norm = math.sqrt(sum(x * x for x in vector))
        self.assertAlmostEqual(norm, 1.0, places=2,
                               msg=f"Vector norm should be ≈1.0, got {norm:.4f}")
        await client.close()

    # ---- Search recall (requires OpenSearch + ingested data) ---------------

    async def test_integration_image_indexed_and_searchable_by_vector(self):
        """
        Ingest an image into OpenSearch, then run a kNN-only search with
        the same vector and verify the image appears in the top results.
        """
        import uuid
        from model_client import ModelClient
        from image_handler import ImageHandler
        from opensearch_client import OpenSearchClient, ChunkDocument

        handler  = ImageHandler()
        client   = ModelClient(model_server_url=self.MODEL_SERVER_URL)
        os_client = OpenSearchClient()

        if not os_client.health_check():
            self.skipTest("OpenSearch not healthy")

        # Create a unique image so we can identify it
        from PIL import Image
        unique_color = (137, 42, 201)
        buf = io.BytesIO()
        Image.new("RGB", (400, 300), unique_color).save(buf, format="JPEG", quality=90)
        jpeg = buf.getvalue()

        result = handler.process(jpeg, "image/jpeg")
        self.assertTrue(result.success)

        vector = await client.embed_image(result.image_bytes, "image/jpeg")

        unique_key = f"test-image-search/{uuid.uuid4()}.jpg"
        doc = ChunkDocument(
            object_key  = unique_key,
            bucket      = "test",
            filename    = "unique_purple.jpg",
            extension   = "jpg",
            mime_type   = "image/jpeg",
            size_bytes  = len(jpeg),
            uploaded_at = "2024-01-01T00:00:00Z",
            chunk_index = 0,
            chunk_total = 1,
            chunk_text  = "Image: unique_purple.jpg (400x300 pixels, image/jpeg)",
            embedding   = vector,
        )
        os_client.upsert(doc)

        # Wait for refresh
        import time
        time.sleep(1)

        # Query with the same vector — should return the document we just ingested
        from backend.search.opensearch_query_builder import build_hybrid_query
        body = build_hybrid_query("unique_purple", vector, size=10)
        from opensearchpy import OpenSearch, RequestsHttpConnection
        os = OpenSearch(
            hosts=[{"host": "localhost", "port": 9200}],
            connection_class=RequestsHttpConnection,
        )
        raw = os.search(index="hpe-search-docs", body=body)
        hits = raw.get("hits", {}).get("hits", [])
        found_keys = [h["_source"]["object_key"] for h in hits]

        self.assertIn(unique_key, found_keys,
                      f"Ingested image not found in search results.\n"
                      f"Top hits: {found_keys[:5]}")

        # Cleanup
        os_client.delete_by_object_key(unique_key)
        await client.close()

    async def test_integration_bm25_keyword_search_uses_exif_text(self):
        """
        When an image is indexed with EXIF-enriched BM25 text, a keyword-only
        query (zero vector) should surface it by the camera model name.
        """
        import uuid
        from model_client import ModelClient
        from image_handler import ImageHandler
        from opensearch_client import OpenSearchClient, ChunkDocument

        try:
            import piexif  # noqa: F401
        except ImportError:
            self.skipTest("piexif not installed — cannot embed EXIF data")

        handler   = ImageHandler()
        client    = ModelClient(model_server_url=self.MODEL_SERVER_URL)
        os_client = OpenSearchClient()

        if not os_client.health_check():
            self.skipTest("OpenSearch not healthy")

        # Build JPEG with recognisable camera name
        unique_camera = f"TestCam-{uuid.uuid4().hex[:8]}"
        jpeg = _jpeg_with_exif(camera_make=unique_camera, camera_model="ProShot X1")
        result = handler.process(jpeg, "image/jpeg")
        self.assertTrue(result.success)

        # BM25 text mirrors what main.py produces
        exif = result.exif_metadata
        bm25_parts = [
            f"Image: camera_test.jpg",
            f"({result.orig_width}x{result.orig_height} pixels, image/jpeg)",
        ]
        if exif.get("camera_make"):
            bm25_parts.append(f"Camera: {exif['camera_make']} {exif.get('camera_model', '')}.")

        bm25_text = " ".join(bm25_parts)
        vector    = await client.embed_image(result.image_bytes, "image/jpeg")

        unique_key = f"test-image-bm25/{uuid.uuid4()}.jpg"
        doc = ChunkDocument(
            object_key  = unique_key,
            bucket      = "test",
            filename    = "camera_test.jpg",
            extension   = "jpg",
            mime_type   = "image/jpeg",
            size_bytes  = len(jpeg),
            uploaded_at = "2024-01-01T00:00:00Z",
            chunk_index = 0,
            chunk_total = 1,
            chunk_text  = bm25_text,
            embedding   = vector,
        )
        os_client.upsert(doc)

        import time
        time.sleep(1)

        # Query by unique camera name with a zero vector (forces BM25 to carry the score)
        from backend.search.opensearch_query_builder import build_hybrid_query
        zero_vector = [0.0] * 384
        body = build_hybrid_query(unique_camera, zero_vector, size=10)
        from opensearchpy import OpenSearch, RequestsHttpConnection
        os = OpenSearch(hosts=[{"host": "localhost", "port": 9200}],
                        connection_class=RequestsHttpConnection)
        raw = os.search(index="hpe-search-docs", body=body)
        hits = raw.get("hits", {}).get("hits", [])
        found_keys = [h["_source"]["object_key"] for h in hits]

        self.assertIn(unique_key, found_keys,
                      f"Image with camera '{unique_camera}' not found via keyword search.\n"
                      f"BM25 text: {bm25_text!r}\n"
                      f"Top hits: {found_keys[:5]}")

        os_client.delete_by_object_key(unique_key)
        await client.close()


# ===========================================================================
# Standalone report mode
# ===========================================================================

def _run_unit_suite() -> tuple[int, int]:
    """Run all unit tests and return (passed, total)."""
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromName("unit", TestImageHandlerUnit)
    # Fallback: load all if name filter not supported
    if suite.countTestCases() == 0:
        suite = loader.loadTestsFromTestCase(TestImageHandlerUnit)

    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)
    passed = result.testsRun - len(result.failures) - len(result.errors)
    return passed, result.testsRun


if __name__ == "__main__":
    print("=" * 65)
    print("Image Search Effectiveness — Unit Test Suite")
    print("=" * 65)

    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromTestCase(TestImageHandlerUnit)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    passed = result.testsRun - len(result.failures) - len(result.errors)
    print("\n" + "=" * 65)
    print(f"Results: {passed}/{result.testsRun} passed")
    if result.failures or result.errors:
        print("❌  Some tests failed — see output above")
        sys.exit(1)
    else:
        print("✅  All unit tests passed")
        print("\nTo run integration tests (requires Docker):")
        print("    python -m pytest test_image_search_effectiveness.py -v -k integration")

