"""
chunker.py — LangChain-based text chunking for the ingestion pipeline.

Splits extracted text into overlapping chunks suitable for embedding by
``all-MiniLM-L6-v2`` (256 word-piece token limit).  Each chunk carries
positional metadata (index, character offsets, total count) so downstream
consumers can reconstruct context or highlight search results.

Usage:
    chunker = TextChunker(chunk_size=512, chunk_overlap=50)
    chunks  = chunker.chunk(extracted_text)
    for c in chunks:
        print(c.chunk_index, c.text[:80], c.start_char, c.end_char)
"""

import logging
from dataclasses import dataclass
from typing import Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ChunkResult:
    """Container for a single text chunk with positional metadata."""

    text: str
    chunk_index: int
    start_char: int
    end_char: int
    total_chunks: int


# ---------------------------------------------------------------------------
# Text Chunker
# ---------------------------------------------------------------------------

class TextChunker:
    """
    Wraps LangChain's ``RecursiveCharacterTextSplitter`` with configurable
    parameters and enriches every chunk with positional metadata.

    Parameters:
        chunk_size:     Maximum number of characters per chunk (default 512).
        chunk_overlap:  Number of overlapping characters between consecutive
                        chunks (default 50, ~10% of chunk_size).
        min_chunk_size: Chunks shorter than this are discarded to avoid
                        noise from whitespace or extraction artifacts
                        (default 20).
        separators:     Ordered list of separators used by the recursive
                        splitter.  Defaults to LangChain's built-in
                        hierarchy: paragraphs → lines → words → chars.
    """

    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 50,
        min_chunk_size: int = 20,
        separators: Optional[list[str]] = None,
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_chunk_size = min_chunk_size

        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=separators or ["\n\n", "\n", " ", ""],
            strip_whitespace=True,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chunk(self, text: str) -> list[ChunkResult]:
        """
        Split *text* into overlapping chunks with positional metadata.

        Args:
            text: The full extracted text to be chunked.

        Returns:
            A list of ``ChunkResult`` objects ordered by ``chunk_index``.
            Returns an empty list if the input is empty, whitespace-only,
            or shorter than ``min_chunk_size``.
        """
        # --- Guard: empty / whitespace-only input ----------------------
        if not text or not text.strip():
            logger.debug("chunk() called with empty or whitespace-only text")
            return []

        stripped = text.strip()

        # --- Guard: text too short to be useful ------------------------
        if len(stripped) < self.min_chunk_size:
            logger.debug(
                "Text length (%d) below min_chunk_size (%d) — skipping",
                len(stripped),
                self.min_chunk_size,
            )
            return []

        # --- Split using LangChain ------------------------------------
        raw_chunks: list[str] = self._splitter.split_text(stripped)

        # --- Filter out tiny fragments --------------------------------
        raw_chunks = [c for c in raw_chunks if len(c) >= self.min_chunk_size]

        if not raw_chunks:
            logger.warning("All chunks were below min_chunk_size after splitting")
            return []

        # --- Compute character offsets --------------------------------
        total = len(raw_chunks)
        results: list[ChunkResult] = []
        search_from = 0

        for idx, chunk_text in enumerate(raw_chunks):
            start = stripped.find(chunk_text, search_from)
            if start == -1:
                # Fallback: search from the beginning (shouldn't happen,
                # but protects against edge cases with overlapping chunks).
                start = stripped.find(chunk_text)
            end = start + len(chunk_text) if start != -1 else -1

            results.append(
                ChunkResult(
                    text=chunk_text,
                    chunk_index=idx,
                    start_char=start,
                    end_char=end,
                    total_chunks=total,
                )
            )

            # Advance search position — but only to the end minus overlap,
            # so the next chunk (which overlaps) is found at the right spot.
            if start != -1:
                search_from = start + len(chunk_text) - self.chunk_overlap

        logger.info(
            "Chunked text into %d chunks (chunk_size=%d, overlap=%d)",
            total,
            self.chunk_size,
            self.chunk_overlap,
        )

        return results
