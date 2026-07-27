"""Hybrid semantic search over the unified pgvector index.

One query embedding is compared against both audio (Whisper) and visual (OCR)
vectors in the same table, keyed by media_id. Results ordered by cosine
distance; distance -> similarity for the response.

Post-processing improves perceived quality:
  - two hits from the SAME video within a few seconds are collapsed (they point
    at the same moment) — the best-scored one is kept;
  - at most ``per_video`` hits per video, so results stay diverse across the
    library instead of flooding with one long video.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.embeddings import embed_one
from app.models import Embedding, ProcessedMedia

# Deux passages du même média à moins de X ms sont considérés comme le même
# moment. Fenêtre volontairement large pour éviter les quasi-doublons.
_COLLAPSE_MS = 12_000


def search(
    db: Session,
    query: str,
    limit: int = 10,
    media_id: int | None = None,
    min_score: float | None = None,
    per_video: int = 3,
) -> list[dict]:
    qvec = embed_one(query)
    distance = Embedding.embedding.cosine_distance(qvec)

    # On récupère large : le post-traitement (dédup + diversité) réduit ensuite.
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
        .limit(limit * 6)
    )
    if media_id is not None:
        stmt = stmt.where(Embedding.rtvc_id == media_id)

    threshold = settings.search_min_score if min_score is None else min_score

    kept: list[dict] = []
    per_video_count: dict[int, int] = {}
    kept_starts: dict[int, list[int]] = {}  # moments déjà retenus par vidéo

    for r in db.execute(stmt).mappings().all():
        score = 1.0 - float(r["distance"])
        if score < threshold:
            continue
        vid = r["rtvc_id"]
        start_ms = r["start_ms"]

        # dédup : trop proche d'un passage déjà retenu de la même vidéo ?
        if any(abs(start_ms - s) < _COLLAPSE_MS for s in kept_starts.get(vid, [])):
            continue
        # diversité : plafond de résultats par vidéo
        if per_video_count.get(vid, 0) >= per_video:
            continue

        start_s = start_ms / 1000.0
        kept.append(
            {
                "rtvc_id": vid,
                "title": r["title"],
                "source": r["source"],
                "start_ms": start_ms,
                "start_seconds": round(start_s, 3),
                "end_ms": r["end_ms"],
                "text": r["text"],
                "score": score,
                "deep_link": f"/video/{vid}?t={start_s:.3f}",
            }
        )
        per_video_count[vid] = per_video_count.get(vid, 0) + 1
        kept_starts.setdefault(vid, []).append(start_ms)
        if len(kept) >= limit:
            break
    return kept
