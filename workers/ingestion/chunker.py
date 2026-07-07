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
        chunk_size: int = 600,
        chunk_overlap: int = 60,
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
            # add_start_index=True tells LangChain to store the character
            # offset of each chunk inside its Document metadata.  This is
            # more reliable than computing offsets ourselves with str.find()
            # which can mis-locate repeated text spans.
            add_start_index=True,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chunk(self, text: str) -> list[ChunkResult]:
        """
        Split *text* into overlapping chunks with positional metadata.

        Character offsets are sourced directly from LangChain's
        ``create_documents()`` output (via ``add_start_index=True``), which
        tracks the exact byte position of each chunk as it splits.  This
        avoids the fragile ``str.find()`` approach that can return wrong
        offsets when the same phrase appears multiple times in a document.

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

        # --- Split using LangChain — returns Documents with start_index -----
        # create_documents() with add_start_index=True populates
        # doc.metadata["start_index"] with the character offset at which
        # the chunk begins inside the original (stripped) text.
        docs = self._splitter.create_documents([stripped])

        # --- Filter out tiny fragments --------------------------------
        docs = [d for d in docs if len(d.page_content) >= self.min_chunk_size]

        if not docs:
            logger.warning("All chunks were below min_chunk_size after splitting")
            return []

        total = len(docs)
        results: list[ChunkResult] = []

        for idx, doc in enumerate(docs):
            chunk_text  = doc.page_content
            start_char  = doc.metadata.get("start_index", -1)
            end_char    = start_char + len(chunk_text) if start_char != -1 else -1

            results.append(
                ChunkResult(
                    text=chunk_text,
                    chunk_index=idx,
                    start_char=start_char,
                    end_char=end_char,
                    total_chunks=total,
                )
            )

        logger.info(
            "Chunked text into %d chunks (chunk_size=%d, overlap=%d)",
            total,
            self.chunk_size,
            self.chunk_overlap,
        )

        return results
