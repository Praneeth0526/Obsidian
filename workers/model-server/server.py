"""
server.py — FastAPI application for the shared Model Server.

Exposes three HTTP endpoints consumed by both the ingestion worker and the
search worker:

    GET  /health        — liveness probe; returns 200 once models are loaded.
    POST /embed/text    — embeds a JSON list of strings → list of 384-dim vectors.
    POST /embed/image   — embeds a multipart JPEG image → single 384-dim vector.

Startup sequence:
    Both TextEmbedder and ImageEmbedder are loaded during the FastAPI lifespan
    event so the heavy model downloads only happen once, before the first
    request is served.  Subsequent requests are fully in-memory.

Environment variables:
    TEXT_EMBEDDER_MODEL        Override text model (default: all-MiniLM-L6-v2).
    TEXT_EMBEDDER_DEVICE       Force device: cpu | cuda | mps (default: auto).
    TEXT_EMBEDDER_BATCH_SIZE   Encode batch size for text (default: 64).
    IMAGE_EMBEDDER_MODEL       Override image model (default: nomic-embed-vision-v1.5).
    IMAGE_EMBEDDER_DEVICE      Force device: cpu | cuda | mps (default: auto).
"""

import logging
from contextlib import asynccontextmanager
from typing import Annotated

import structlog
from fastapi import FastAPI, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from image_embedder import ImageEmbedder
from text_embedder import TextEmbedder

# ---------------------------------------------------------------------------
# Logging — structured JSON via structlog
# ---------------------------------------------------------------------------

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Embedder singletons
# ---------------------------------------------------------------------------

_text_embedder = TextEmbedder()
_image_embedder = ImageEmbedder()


# ---------------------------------------------------------------------------
# Lifespan — load models once before serving traffic
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load both embedding models at startup; nothing special on shutdown."""
    log.info("model_server.startup", status="loading_models")
    _text_embedder.load()
    _image_embedder.load()
    log.info(
        "model_server.startup",
        status="ready",
        text_dim=_text_embedder.dimension,
        image_dim=_image_embedder.dimension,
    )
    yield
    log.info("model_server.shutdown")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="HPE Search Model Server",
    description=(
        "Centralised embedding service. "
        "Exposes /embed/text and /embed/image backed by "
        "all-MiniLM-L6-v2 and nomic-embed-vision-v1.5."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class EmbedTextRequest(BaseModel):
    texts: list[str] = Field(
        ...,
        min_length=1,
        description="Non-empty list of strings to embed.",
        examples=[["hello world", "another sentence"]],
    )


class EmbedTextResponse(BaseModel):
    embeddings: list[list[float]] = Field(
        ...,
        description="One 384-dim vector per input string, in the same order.",
    )
    count: int = Field(..., description="Number of embeddings returned.")


class EmbedImageResponse(BaseModel):
    embedding: list[float] = Field(
        ...,
        description="Single 384-dim vector for the submitted image.",
    )
    dimension: int = Field(..., description="Vector length (always 384).")


class HealthResponse(BaseModel):
    status: str = Field(..., examples=["healthy"])
    text_model_loaded: bool
    image_model_loaded: bool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    tags=["ops"],
)
def health() -> HealthResponse:
    """
    Return 200 when both models are loaded and ready to serve requests.

    Returns 503 if either model is not yet loaded (e.g. during a slow
    cold-start). Kubernetes / Docker health checks should poll this endpoint.
    """
    text_ok = _text_embedder.is_loaded
    image_ok = _image_embedder.is_loaded

    if not text_ok or not image_ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "loading",
                "text_model_loaded": text_ok,
                "image_model_loaded": image_ok,
            },
        )

    return HealthResponse(
        status="healthy",
        text_model_loaded=text_ok,
        image_model_loaded=image_ok,
    )


@app.post(
    "/embed/text",
    response_model=EmbedTextResponse,
    summary="Embed a batch of text strings",
    tags=["embed"],
)
def embed_text(request: EmbedTextRequest) -> EmbedTextResponse:
    """
    Embed a list of text strings into 384-dim vectors.

    Called by the ingestion worker (model_client.embed_texts) and the search
    worker to vectorise a query.  The response preserves input order.
    """
    if not request.texts:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'texts' must be a non-empty list.",
        )

    log.info("embed_text.request", count=len(request.texts))
    try:
        vectors = _text_embedder.embed(request.texts)
    except Exception as exc:  # noqa: BLE001
        log.error("embed_text.error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Text embedding failed: {exc}",
        ) from exc

    log.info("embed_text.response", count=len(vectors), dim=len(vectors[0]) if vectors else 0)
    return EmbedTextResponse(embeddings=vectors, count=len(vectors))


@app.post(
    "/embed/image",
    response_model=EmbedImageResponse,
    summary="Embed a preprocessed JPEG image",
    tags=["embed"],
)
async def embed_image(
    file: Annotated[UploadFile, File(description="224×224 RGB JPEG produced by ImageHandler.")],
) -> EmbedImageResponse:
    """
    Embed a single image into a 384-dim vector.

    The image MUST already be a 224×224 RGB JPEG (preprocessed by
    ImageHandler in the ingestion worker).  Sending un-resized or
    non-JPEG images will result in a 422 error.

    Called by model_client.embed_image() via multipart/form-data upload.
    """
    if file.content_type not in {"image/jpeg", "image/png"}:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported content type '{file.content_type}'. "
                "Only image/jpeg and image/png are accepted."
            ),
        )

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded image file is empty.",
        )

    log.info("embed_image.request", content_type=file.content_type, bytes=len(image_bytes))
    try:
        vector = _image_embedder.embed(image_bytes)
    except ValueError as exc:
        log.warning("embed_image.validation_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        log.error("embed_image.error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Image embedding failed: {exc}",
        ) from exc

    log.info("embed_image.response", dim=len(vector))
    return EmbedImageResponse(embedding=vector, dimension=len(vector))
