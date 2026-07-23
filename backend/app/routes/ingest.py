"""Ingestion de vidéos locales.

En production, les médias viennent de RTVC. Cette voie permet d'indexer un
fichier posé sur le disque — indispensable pour démontrer la chaîne complète
(transcription → OCR → index → recherche → saut au timestamp) sans dépendre du
stockage RTVC.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import ProcessedMedia
from app.rtvc import get_rtvc
from app.schemas import ProcessResponse
from app.worker.tasks import process_local, process_rtvc_nas

router = APIRouter(tags=["ingestion locale"])

LOCAL_ID_BASE = 900000
VIDEO_EXT = {".mp4", ".ts", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".mpg", ".mpeg"}


class NasIngestRequest(BaseModel):
    nas_path: str
    title: str | None = None
    max_seconds: int | None = 180
    max_mb: int | None = 120


class LocalIngestRequest(BaseModel):
    path: str
    title: str | None = None
    max_seconds: int | None = 300  # limite la durée traitée (démo rapide)


def _input_root() -> Path:
    return Path(settings.media_input_dir)


@router.get("/ingest/browse")
def browse_inputs():
    """Liste les vidéos disponibles dans le dossier d'entrée monté."""
    root = _input_root()
    if not root.is_dir():
        return {"root": str(root), "files": [], "error": "dossier d'entrée introuvable"}
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in VIDEO_EXT:
            try:
                size = p.stat().st_size
            except OSError:
                continue
            files.append({
                "path": str(p),
                "name": p.name,
                "size_mb": round(size / (1024 * 1024), 1),
            })
    return {"root": str(root), "files": files}


@router.get("/ingest/rtvc/browse")
def browse_rtvc_nas(path: str = ""):
    """Explore le NAS RTVC (dossiers et vidéos) via l'API RTVC."""
    try:
        return get_rtvc().nas_browse(path)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"RTVC: {e}")


def _reserve_media(db: Session, path: str, title: str, source: str) -> int:
    """Réserve un identifiant et crée la ligne tout de suite.

    Créer la ligne ici — et non dans le worker — évite que deux demandes
    rapprochées calculent le même identifiant : la réservation est enregistrée
    en base avant qu'on réponde.
    """
    existing = db.execute(
        select(ProcessedMedia).where(ProcessedMedia.local_path == path)
    ).scalars().first()
    if existing is not None:
        return existing.rtvc_id

    max_id = db.scalar(
        select(func.max(ProcessedMedia.rtvc_id)).where(
            ProcessedMedia.rtvc_id >= LOCAL_ID_BASE
        )
    )
    media_id = (max_id or LOCAL_ID_BASE) + 1
    db.add(ProcessedMedia(rtvc_id=media_id, title=title, source=source,
                          local_path=path, status="pending"))
    db.commit()
    return media_id


@router.post("/ingest/rtvc", response_model=ProcessResponse)
def ingest_rtvc_nas(req: NasIngestRequest, db: Session = Depends(get_db)):
    """Indexe une vidéo du NAS RTVC à partir de son chemin de fichier."""
    title = req.title or Path(req.nas_path).stem
    media_id = _reserve_media(db, req.nas_path, title, "rtvc-nas")
    res = process_rtvc_nas.delay(
        media_id, req.nas_path, title, req.max_seconds, req.max_mb
    )
    return ProcessResponse(rtvc_id=media_id, task_id=res.id, status="queued")


@router.post("/ingest/local", response_model=ProcessResponse)
def ingest_local(req: LocalIngestRequest, db: Session = Depends(get_db)):
    """Indexe un fichier vidéo local et renvoie l'identifiant attribué."""
    src = Path(req.path)
    if not src.is_file():
        raise HTTPException(status_code=404, detail=f"fichier introuvable : {req.path}")

    title = req.title or src.stem
    media_id = _reserve_media(db, str(src), title, "local")
    res = process_local.delay(media_id, str(src), title, req.max_seconds)
    return ProcessResponse(rtvc_id=media_id, task_id=res.id, status="queued")
