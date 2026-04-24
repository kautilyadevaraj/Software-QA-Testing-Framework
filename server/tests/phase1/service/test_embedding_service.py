"""Unit tests for app.services.embedding_service — vector generation (model mocked)."""

from __future__ import annotations

import numpy as np
from unittest.mock import MagicMock, patch

from app.services.embedding_service import embed_text, embed_texts


# ---------------------------------------------------------------------------
# We mock the SentenceTransformer model so tests don't download weights.
# ---------------------------------------------------------------------------

def _mock_model():
    model = MagicMock()
    model.get_sentence_embedding_dimension.return_value = 384

    def _encode(texts, **kwargs):
        # Return fake 384-dim vectors
        return np.random.rand(len(texts), 384).astype(np.float32)

    model.encode = _encode
    return model


class TestEmbedTexts:
    @patch("app.services.embedding_service._get_model")
    def test_returns_correct_shape(self, mock_get_model):
        mock_get_model.return_value = _mock_model()

        texts = ["Hello world", "Another sentence", "Third one"]
        result = embed_texts(texts)

        assert len(result) == 3
        assert all(len(vec) == 384 for vec in result)
        assert all(isinstance(vec, list) for vec in result)

    @patch("app.services.embedding_service._get_model")
    def test_empty_input(self, mock_get_model):
        mock_get_model.return_value = _mock_model()
        result = embed_texts([])
        assert result == []

    @patch("app.services.embedding_service._get_model")
    def test_single_text(self, mock_get_model):
        mock_get_model.return_value = _mock_model()
        result = embed_texts(["One text"])
        assert len(result) == 1
        assert len(result[0]) == 384


class TestEmbedText:
    @patch("app.services.embedding_service._get_model")
    def test_returns_single_vector(self, mock_get_model):
        mock_get_model.return_value = _mock_model()
        result = embed_text("Hello world")
        assert isinstance(result, list)
        assert len(result) == 384
