"""
Embedding Service - Generates vector embeddings by calling model-server API
"""
import os
import requests
from typing import List
from config import EMBEDDING_DIMENSION

MODEL_SERVER_URL = os.getenv("MODEL_SERVER_URL", "http://model-server:8000")

class EmbeddingService:
    """Service for generating text embeddings using model-server API."""

    _instance = None

    def __new__(cls):
        """Singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize the embedding service."""
        pass

    def encode(self, text: str) -> List[float]:
        """
        Encode text into a vector embedding using the model-server.

        Args:
            text: The text to encode

        Returns:
            List of floats representing the embedding vector
        """
        if not text or not text.strip():
            # Return zero vector for empty text
            return [0.0] * EMBEDDING_DIMENSION

        try:
            resp = requests.post(
                f"{MODEL_SERVER_URL}/embed/text",
                json={"texts": [text.strip()]},
                timeout=30.0
            )
            resp.raise_for_status()
            data = resp.json()
            return data["embeddings"][0]
        except Exception as e:
            print(f"[!] Embedding service request failed: {e}")
            return [0.0] * EMBEDDING_DIMENSION

    def get_dimension(self) -> int:
        """Get the dimension of the embedding vectors."""
        return EMBEDDING_DIMENSION