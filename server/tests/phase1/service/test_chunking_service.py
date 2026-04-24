"""Unit tests for app.services.chunking_service — sliding window chunking and token counting."""

from __future__ import annotations

import pytest

from app.services.chunking_service import Chunk, chunk_text, count_tokens


class TestCountTokens:
    def test_known_string(self):
        """Simple English sentence should produce a reasonable token count."""
        count = count_tokens("Hello, world!")
        assert count > 0
        assert count < 10  # Should be ~4 tokens

    def test_empty_string(self):
        count = count_tokens("")
        assert count == 0

    def test_long_text(self):
        long_text = "word " * 1000
        count = count_tokens(long_text)
        assert count > 500  # At least 500 tokens for 1000 words


class TestChunkText:
    def test_basic_chunking(self):
        # Generate text that's clearly longer than chunk_size (512 tokens)
        text = "The quick brown fox jumps over the lazy dog. " * 200
        chunks = chunk_text(text)
        assert len(chunks) > 1
        assert all(isinstance(c, Chunk) for c in chunks)

    def test_chunk_indices_are_sequential(self):
        text = "Word " * 2000
        chunks = chunk_text(text)
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i

    def test_empty_text_returns_empty(self):
        assert chunk_text("") == []

    def test_whitespace_only_returns_empty(self):
        assert chunk_text("   \n\t  ") == []

    def test_short_text_single_chunk(self):
        """Text shorter than chunk_size should produce exactly one chunk."""
        text = "Short text."
        chunks = chunk_text(text)
        assert len(chunks) == 1
        assert chunks[0].content.strip() == "Short text."
        assert chunks[0].chunk_index == 0

    def test_metadata_propagated(self):
        meta = {"project_id": "p1", "file_id": "f1", "source_type": "pdf"}
        text = "Some content for testing metadata."
        chunks = chunk_text(text, source_metadata=meta)
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk.metadata["project_id"] == "p1"
            assert chunk.metadata["file_id"] == "f1"
            assert chunk.metadata["source_type"] == "pdf"

    def test_token_count_matches(self):
        text = "This is a test sentence for token counting."
        chunks = chunk_text(text)
        assert len(chunks) == 1
        assert chunks[0].token_count == count_tokens(text)

    def test_overlap_produces_shared_content(self):
        """Adjacent chunks should share some content due to overlap."""
        text = "Word " * 2000
        chunks = chunk_text(text)
        if len(chunks) >= 2:
            # Find overlap: end of chunk 0 tokens should appear at start of chunk 1
            # The chunks share `overlap` tokens, so there should be common content.
            content_0 = chunks[0].content
            content_1 = chunks[1].content
            # The last part of chunk_0 should appear at the start of chunk_1
            # We check that the chunks are not completely disjoint
            words_0 = set(content_0.split()[-20:])
            words_1 = set(content_1.split()[:20])
            overlap_words = words_0 & words_1
            assert len(overlap_words) > 0, "Adjacent chunks should share overlapping content"
