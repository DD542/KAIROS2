"""Statistiques d'exploitation (supervision légère)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Embedding, OcrText, ProcessedMedia, Transcription

router = APIRouter(tags=["stats"])


@router.post("/maintenance/cleanup")
def cleanup(db: Session = Depends(get_db)):
    """Supprime les fichiers orphelins (vidéos de lecture et vignettes dont le
    média n'existe plus en base) et les dossiers de travail résiduels.

    À lancer après des suppressions ; évite que le disque se remplisse
    silencieusement en production.
    """
    import shutil
    from pathlib import Path

    from app.config import settings

    known = {int(i) for (i,) in db.execute(select(ProcessedMedia.rtvc_id)).all()}
    root = Path(settings.data_dir)
    freed = 0
    removed: list[str] = []

    def _media_id_of(name: str) -> int | None:
        stem = name.split(".")[0].split("_")[0]
        return int(stem) if stem.isdigit() else None

    for folder in ("playback", "thumbs"):
        d = root / folder
        if not d.is_dir():
            continue
        for f in d.iterdir():
            mid = _media_id_of(f.name)
            if f.is_file() and mid is not None and mid not in known:
                freed += f.stat().st_size
                f.unlink(missing_ok=True)
                removed.append(f"{folder}/{f.name}")

    # dossiers de travail : temporaires, supprimables dès qu'ils traînent
    work = root / "work"
    if work.is_dir():
        for d in work.iterdir():
            mid = _media_id_of(d.name)
            if d.is_dir() and (mid is None or mid not in known):
                shutil.rmtree(d, ignore_errors=True)
                removed.append(f"work/{d.name}")

    return {
        "fichiers_supprimes": len(removed),
        "espace_libere_mo": round(freed / (1024 * 1024), 1),
        "details": removed[:20],
    }


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
        "disque": _disk_usage(),
    }


def _disk_usage() -> dict:
    """Espace occupé par les fichiers générés + espace libre restant."""
    import shutil as _shutil
    from pathlib import Path

    from app.config import settings

    root = Path(settings.data_dir)
    sizes = {}
    for folder in ("playback", "thumbs", "work"):
        d = root / folder
        total = 0
        if d.is_dir():
            for f in d.rglob("*"):
                if f.is_file():
                    try:
                        total += f.stat().st_size
                    except OSError:
                        pass
        sizes[folder + "_mo"] = round(total / (1024 * 1024), 1)
    try:
        usage = _shutil.disk_usage(root)
        sizes["libre_go"] = round(usage.free / (1024 ** 3), 1)
    except OSError:
        pass
    return sizes
