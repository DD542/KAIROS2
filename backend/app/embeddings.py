"""Lazy singleton wrapper around the sentence-transformers model.

Loaded once per process. Both the worker (indexing) and the API (query
embedding) import from here so the same model / dimension is guaranteed.

Une question et un passage de transcription ne se ressemblent pas : l'une est
courte et interrogative, l'autre est une phrase parlée. Les meilleurs modèles
de recherche (famille E5, BGE…) demandent donc qu'on annonce le rôle du texte
par un préfixe. Les fonctions ci-dessous encapsulent cette différence pour que
personne n'ait à y penser ailleurs — et pour qu'un modèle sans préfixe (le
défaut historique) continue de fonctionner sans changement.
"""

from __future__ import annotations

from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.config import settings


@lru_cache
def _model() -> SentenceTransformer:
    return SentenceTransformer(settings.embedding_model)


def _encode(texts: list[str], prefix: str) -> list[list[float]]:
    if not texts:
        return []
    payload = [prefix + t for t in texts] if prefix else texts
    vecs = _model().encode(payload, normalize_embeddings=True, batch_size=32)
    return [v.tolist() for v in vecs]


def embed_query(text: str) -> list[float]:
    """Vecteur d'une question posée par l'utilisateur."""
    return _encode([text], settings.embedding_query_prefix)[0]


def embed_passages(texts: list[str]) -> list[list[float]]:
    """Vecteurs des passages indexés (transcription, texte à l'écran)."""
    return _encode(texts, settings.embedding_passage_prefix)


# --- compatibilité : anciens noms, sans distinction question/passage ---------
def embed_one(text: str) -> list[float]:
    return embed_query(text)


def embed_many(texts: list[str]) -> list[list[float]]:
    return embed_passages(texts)
