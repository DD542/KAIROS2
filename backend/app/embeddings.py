"""Lazy singleton wrapper around the sentence-transformers model.

Loaded once per process. Both the worker (indexing) and the API (query
embedding) import from here so the same model / dimension is guaranteed.
"""

from __future__ import annotations

from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.config import settings


@lru_cache
def _model() -> SentenceTransformer:
    return SentenceTransformer(settings.embedding_model)


def embed_one(text: str) -> list[float]:
    vec = _model().encode(text, normalize_embeddings=True)
    return vec.tolist()


def embed_many(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    vecs = _model().encode(texts, normalize_embeddings=True, batch_size=32)
    return [v.tolist() for v in vecs]
