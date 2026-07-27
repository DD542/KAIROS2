"""Media listing, processing status, and RTVC-backed playback."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import ProcessedMedia
from app.pages import render_page
from app.rtvc import RTVCError, get_rtvc
from app.schemas import MediaOut, StreamTokenResponse
from app.worker.celery_app import celery_app

router = APIRouter(tags=["media"])


@router.get("/media", response_model=list[MediaOut])
def list_media(db: Session = Depends(get_db)):
    return db.execute(
        select(ProcessedMedia).order_by(ProcessedMedia.created_at.desc())
    ).scalars().all()


@router.get("/media/{rtvc_id}/status", response_model=MediaOut)
def media_status(rtvc_id: int, db: Session = Depends(get_db)):
    pm = db.get(ProcessedMedia, rtvc_id)
    if pm is None:
        raise HTTPException(status_code=404, detail="media not indexed")
    return pm


@router.post("/media/{rtvc_id}/retry", response_model=MediaOut)
def retry_media(rtvc_id: int, db: Session = Depends(get_db)):
    """Relance l'indexation d'un média (typiquement après un échec)."""
    pm = db.get(ProcessedMedia, rtvc_id)
    if pm is None:
        raise HTTPException(status_code=404, detail="media inconnu")
    if not pm.local_path:
        raise HTTPException(status_code=400, detail="chemin source inconnu")

    # import ici pour éviter un import circulaire au chargement du module
    from app.worker.tasks import process_local, process_rtvc_nas

    pm.status = "pending"
    pm.error = None
    db.commit()
    if pm.source == "rtvc-nas":
        process_rtvc_nas.delay(rtvc_id, pm.local_path, pm.title or str(rtvc_id))
    else:
        process_local.delay(rtvc_id, pm.local_path, pm.title or str(rtvc_id))
    db.refresh(pm)
    return pm


@router.get("/tasks/{task_id}")
def task_status(task_id: str):
    res = celery_app.AsyncResult(task_id)
    return {"task_id": task_id, "state": res.state, "info": res.info if res.ready() else None}


@router.get("/rtvc/nas-status")
def nas_status():
    try:
        return get_rtvc().nas_status()
    except RTVCError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/video/{rtvc_id}/stream-token", response_model=StreamTokenResponse)
def stream_token(rtvc_id: int):
    """Proxy RTVC's stream-token so the frontend gets a signed HLS URL."""
    try:
        url, raw = get_rtvc().stream_token(rtvc_id)
    except RTVCError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return StreamTokenResponse(rtvc_id=rtvc_id, master_url=url, raw=raw if isinstance(raw, dict) else {"raw": raw})


@router.get("/media/{rtvc_id}/video")
def media_video(rtvc_id: int, db: Session = Depends(get_db)):
    """Sert la vidéo transcodée d'un média local (lecture + seek via Range)."""
    pm = db.get(ProcessedMedia, rtvc_id)
    if pm is None or not pm.playback_path:
        raise HTTPException(status_code=404, detail="vidéo indisponible")
    path = Path(pm.playback_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="fichier de lecture introuvable")
    return FileResponse(path, media_type="video/mp4")


@router.get("/video/{rtvc_id}", response_class=HTMLResponse)
def player_page(rtvc_id: int):
    page = render_page("player.html")
    if page is None:
        raise HTTPException(status_code=500, detail="page du lecteur introuvable")
    return HTMLResponse(page)
