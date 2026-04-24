"""Qdrant vector-store client — singleton connection and collection bootstrap."""

from __future__ import annotations

import logging
from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, VectorParams

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# all-MiniLM-L6-v2 produces 384-dimensional vectors
VECTOR_SIZE = 384


@lru_cache
def get_qdrant_client() -> QdrantClient:
    """Return a reusable Qdrant client (created once per process)."""
    settings = get_settings()
    client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    logger.info("Connected to Qdrant at %s:%s", settings.qdrant_host, settings.qdrant_port)
    return client


def ensure_collection() -> None:
    """Create the vector collection if it does not already exist."""
    settings = get_settings()
    client = get_qdrant_client()
    collection_name = settings.qdrant_collection

    try:
        client.get_collection(collection_name)
        logger.info("Qdrant collection '%s' already exists.", collection_name)
    except (UnexpectedResponse, Exception):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        logger.info("Created Qdrant collection '%s'.", collection_name)
