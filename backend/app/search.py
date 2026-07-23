"""Hybrid semantic search over the unified pgvector index.

One query embedding is compared against both audio (Vosk) and visual (OCR)
vectors in the same table, keyed by RTVC media_id. Results ordered by cosine
distance; distance is converted to similarity for the response.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.embeddings import embed_one
from app.models import Embedding, ProcessedMedia


def search(
    db: Session,
    query: str,
    limit: int = 10,
    media_id: int | None = None,
    min_score: float | None = None,
) -> list[dict]:
    qvec = embed_one(query)
    distance = Embedding.embedding.cosine_distance(qvec)

    # Fetch a few extra rows so the min-score filter still yields ``limit`` hits.
    stmt = (
        select(
            Embedding.rtvc_id,
            ProcessedMedia.title,
            Embedding.source,
            Embedding.start_ms,
            Embedding.end_ms,
            Embedding.text,
            distance.label("distance"),
        )
        .join(ProcessedMedia, ProcessedMedia.rtvc_id == Embedding.rtvc_id)
        .where(ProcessedMedia.status == "ready")
        .order_by(distance)
        .limit(limit * 3)
    )
    if media_id is not None:
        stmt = stmt.where(Embedding.rtvc_id == media_id)

    threshold = settings.search_min_score if min_score is None else min_score

    hits: list[dict] = []
    for r in db.execute(stmt).mappings().all():
        score = 1.0 - float(r["distance"])
        if score < threshold:
            continue
        start_s = r["start_ms"] / 1000.0
        hits.append(
            {
                "rtvc_id": r["rtvc_id"],
                "title": r["title"],
                "source": r["source"],
                "start_ms": r["start_ms"],
                "start_seconds": round(start_s, 3),
                "end_ms": r["end_ms"],
                "text": r["text"],
                "score": score,
                "deep_link": f"/video/{r['rtvc_id']}?t={start_s:.3f}",
            }
        )
        if len(hits) >= limit:
            break
    return hits
