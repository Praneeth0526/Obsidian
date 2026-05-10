"""
Embedding Service - Generates vector embeddings using SentenceTransformers
"""
import os

os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
os.environ.setdefault("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

import numpy as np
from typing import List
from sentence_transformers import SentenceTransformer
from config import EMBEDDING_MODEL, EMBEDDING_DIMENSION


class EmbeddingService:
    """Service for generating text embeddings using SentenceTransformers."""

    _instance = None
    _model = None

    def __new__(cls):
        """Singleton pattern to ensure model is loaded only once."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize the embedding service."""
        if self._model is None:
            print(f"[*] Loading embedding model: {EMBEDDING_MODEL}")
            self._model = SentenceTransformer(EMBEDDING_MODEL)
            print(f"[+] Embedding model loaded (dimension: {EMBEDDING_DIMENSION})")

    def encode(self, text: str) -> List[float]:
        """
        Encode text into a vector embedding.

        Args:
            text: The text to encode

        Returns:
            List of floats representing the embedding vector
        """
        if not text or not text.strip():
            # Return zero vector for empty text
            return [0.0] * EMBEDDING_DIMENSION

        # Generate embedding
        embedding = self._model.encode(text.strip(), convert_to_numpy=True)

        # Convert to list of floats
        return embedding.tolist()

    def get_dimension(self) -> int:
        """Get the dimension of the embedding vectors."""
        return EMBEDDING_DIMENSION