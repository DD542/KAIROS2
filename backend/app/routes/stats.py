"""Statistiques d'exploitation (supervision légère)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Embedding, OcrText, ProcessedMedia, Transcription

router = APIRouter(tags=["stats"])


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    """Compteurs globaux : médias par statut, volumes indexés, durée moyenne
    de traitement. Utile pour surveiller et pour la démo."""
    by_status = dict(
        db.execute(
            select(ProcessedMedia.status, func.count()).group_by(ProcessedMedia.status)
        ).all()
    )
    by_source = dict(
        db.execute(
            select(ProcessedMedia.source, func.count()).group_by(ProcessedMedia.source)
        ).all()
    )
    # durée moyenne de traitement (created_at -> processed_at), en secondes
    avg_seconds = db.scalar(
        select(func.avg(
            func.extract("epoch", ProcessedMedia.processed_at)
            - func.extract("epoch", ProcessedMedia.created_at)
        )).where(ProcessedMedia.processed_at.isnot(None))
    )
    return {
        "medias": {
            "total": sum(by_status.values()),
            "par_statut": by_status,
            "par_source": by_source,
        },
        "index": {
            "transcriptions": db.scalar(select(func.count()).select_from(Transcription)),
            "ocr_texts": db.scalar(select(func.count()).select_from(OcrText)),
            "vecteurs": db.scalar(select(func.count()).select_from(Embedding)),
        },
        "traitement": {
            "duree_moyenne_s": round(float(avg_seconds), 1) if avg_seconds else None,
        },
    }
