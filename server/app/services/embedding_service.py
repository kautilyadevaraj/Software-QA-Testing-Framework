"""Embedding service — wraps sentence-transformers for vector generation."""

from __future__ import annotations

import logging
from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def _get_model() -> SentenceTransformer:
    """Lazy-load the embedding model (downloaded on first use, cached locally)."""
    settings = get_settings()
    model_name = settings.embedding_model
    logger.info("Loading embedding model '%s' …", model_name)
    model = SentenceTransformer(model_name)
    logger.info("Embedding model '%s' loaded (dim=%d).", model_name, model.get_sentence_embedding_dimension())
    return model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for a batch of texts.

    Returns a list of float vectors, one per input text.
    """
    if not texts:
        return []
    model = _get_model()
    embeddings = model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
    return [vec.tolist() for vec in embeddings]


def embed_text(text: str) -> list[float]:
    """Generate an embedding for a single text string."""
    return embed_texts([text])[0]
