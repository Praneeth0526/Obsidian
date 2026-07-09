"""
model_client.py — Local Jina CLIP v2 Embedding Client.
Replaced HTTP client with local model inference.
"""

import asyncio
import logging
import os
import tempfile
from typing import Optional

import torch
from transformers import AutoModel

logger = logging.getLogger(__name__)

# Device auto-detection
device = "cuda" if torch.cuda.is_available() else "cpu"

# Module-level singleton
_jina_model = None

def get_jina_model():
    global _jina_model
    if _jina_model is None:
        logger.info("Loading Jina CLIP v2 model on %s...", device)
        model_kwargs = {"trust_remote_code": True}
        if device == "cpu":
            model_kwargs["torch_dtype"] = torch.bfloat16
        _jina_model = AutoModel.from_pretrained(
            "jinaai/jina-clip-v2",
            **model_kwargs
        ).to(device)
        _jina_model.eval()
#New line  remove later
	logger.info("Jina model device: %s", next(_jina_model.parameters()).device)
	if torch.cuda.is_available():
		logger.info("GPU memory allocated: %.2f GB",torch.cuda.memory_allocated() / 1024**3)
#till here
    return _jina_model

def embed_text(texts: list[str]) -> list[list[float]]:
    """
    Embed list of texts into 512-dim vectors using Jina CLIP v2.

    TODO: All existing indexed documents need to be re-embedded and re-indexed
    with the new model, since Jina CLIP v2 vectors are not compatible with
    the old CLIP ViT-B/32 vectors even at the same dimension.
    """
    if not texts:
        return []
    model = get_jina_model()
    with torch.no_grad():
        embeddings = model.encode_text(texts, truncate_dim=512, batch_size=8)
        if isinstance(embeddings, torch.Tensor):
            embeddings = embeddings.cpu().detach().numpy()
        return embeddings.tolist()

def embed_image(image_paths: list[str]) -> list[list[float]]:
    """
    Embed list of image paths into 512-dim vectors using Jina CLIP v2.
    """
    if not image_paths:
        return []
    model = get_jina_model()
    with torch.no_grad():
        embeddings = model.encode_image(image_paths, truncate_dim=512)
        if isinstance(embeddings, torch.Tensor):
            embeddings = embeddings.cpu().detach().numpy()
        return embeddings.tolist()

class ModelClient:
    """
    Wrapper class to match the ingestion worker's usage:
      self.model_client.embed_texts(...)
      self.model_client.embed_image(...)
    """
    def __init__(self, *args, **kwargs):
        # Trigger model load on initialization so it happens once at startup
        get_jina_model()

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        # Run in threadpool to avoid blocking async event loop
        return await asyncio.to_thread(embed_text, texts)

    async def embed_image(self, image_bytes: bytes, content_type: str = "image/jpeg") -> list[float]:
        # Save bytes to a temp file, call embed_image(image_paths), and return the single vector.
        def _embed_bytes():
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp.write(image_bytes)
                tmp_path = tmp.name
            try:
                vectors = embed_image([tmp_path])
                return vectors[0]
            finally:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
        return await asyncio.to_thread(_embed_bytes)

    async def health_check(self) -> bool:
        return get_jina_model() is not None

    async def close(self) -> None:
        pass
