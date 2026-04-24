"""Text chunking service — sliding window strategy with configurable token sizes."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache

import tiktoken

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    """A single text chunk with source metadata."""
    content: str
    token_count: int
    chunk_index: int
    metadata: dict = field(default_factory=dict)


@lru_cache
def _get_encoding() -> tiktoken.Encoding:
    """Return a cached tiktoken encoder (cl100k_base — GPT-4 family)."""
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Count the number of tokens in a text string."""
    return len(_get_encoding().encode(text))


def chunk_text(
    text: str,
    *,
    source_metadata: dict | None = None,
) -> list[Chunk]:
    """Split text into overlapping chunks using a sliding window over tokens.

    Uses the chunk_size_tokens and chunk_overlap_tokens from app settings.

    Parameters
    ----------
    text:
        The full text to chunk.
    source_metadata:
        A dict of metadata to attach to every chunk (merged into chunk.metadata).

    Returns
    -------
    list[Chunk]
        Ordered list of chunks.
    """
    if not text or not text.strip():
        return []

    settings = get_settings()
    chunk_size = settings.chunk_size_tokens
    overlap = settings.chunk_overlap_tokens
    stride = chunk_size - overlap  # 512 - 64 = 448

    enc = _get_encoding()
    tokens = enc.encode(text)

    if len(tokens) == 0:
        return []

    chunks: list[Chunk] = []
    base_meta = source_metadata or {}
    idx = 0

    for start in range(0, len(tokens), stride):
        chunk_tokens = tokens[start : start + chunk_size]
        chunk_content = enc.decode(chunk_tokens)
        chunk = Chunk(
            content=chunk_content,
            token_count=len(chunk_tokens),
            chunk_index=idx,
            metadata={**base_meta},
        )
        chunks.append(chunk)
        idx += 1

        # If this chunk already reached the end of the token stream, stop
        if start + chunk_size >= len(tokens):
            break

    logger.info(
        "Chunked %d tokens into %d chunks (size=%d, overlap=%d).",
        len(tokens), len(chunks), chunk_size, overlap,
    )
    return chunks
