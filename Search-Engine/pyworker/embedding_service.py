"""
Embedding Service - Generates vector embeddings locally using Jina CLIP v2.
"""

import os
from typing import List
import torch
from transformers import AutoModel
import numpy as np

# Device auto-detection
device = "cuda" if torch.cuda.is_available() else "cpu"

# Module-level singleton
_jina_model = None

def get_jina_model():
    global _jina_model
    if _jina_model is None:
        print(f"[+] Loading Jina CLIP v2 model on {device}...")
        model_kwargs = {"trust_remote_code": True}
        if device == "cpu":
            model_kwargs["torch_dtype"] = torch.bfloat16
        _jina_model = AutoModel.from_pretrained(
            "jinaai/jina-clip-v2",
            **model_kwargs
        ).to(device)
        _jina_model.eval()
    return _jina_model

def embed_text(texts: List[str]) -> List[List[float]]:
    """
    Embed list of texts into 512-dim vectors using Jina CLIP v2.
    """
    if not texts:
        return []
    model = get_jina_model()
    with torch.no_grad():
        embeddings = model.encode_text(texts, truncate_dim=512)
        if isinstance(embeddings, torch.Tensor):
            embeddings = embeddings.cpu().detach().numpy()
        return embeddings.tolist()

def embed_image(image_paths: List[str]) -> List[List[float]]:
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

class EmbeddingService:
    """Service for generating text embeddings using local Jina CLIP v2."""

    _instance = None

    def __new__(cls):
        """Singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # Trigger model load on initialization so it happens once at startup
        get_jina_model()

    def encode(self, text: str) -> List[float]:
        """
        Encode text into a vector embedding using Jina CLIP v2.
        """
        if not text or not text.strip():
            # Return zero vector for empty text
            return [0.0] * 512

        try:
            vectors = embed_text([text.strip()])
            return vectors[0]
        except Exception as e:
            print(f"[!] Embedding failed: {e}")
            return [0.0] * 512

    def get_dimension(self) -> int:
        return 512