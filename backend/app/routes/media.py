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


def _segments(db: Session, rtvc_id: int) -> list[dict]:
    from app.models import Transcription
    rows = db.execute(
        select(Transcription.start_ms, Transcription.end_ms, Transcription.text)
        .where(Transcription.rtvc_id == rtvc_id)
        .order_by(Transcription.start_ms)
    ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/media/{rtvc_id}/segments")
def media_segments(rtvc_id: int, db: Session = Depends(get_db)):
    """Transcription horodatée d'un média (pour le sous-titrage du lecteur)."""
    return _segments(db, rtvc_id)


def _fmt_ts(ms: int, sep: str) -> str:
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{milli:03d}"


@router.get("/media/{rtvc_id}/transcript.{fmt}")
def export_transcript(rtvc_id: int, fmt: str, db: Session = Depends(get_db)):
    """Exporte la transcription en sous-titres (srt/vtt) ou texte (txt)."""
    from fastapi.responses import PlainTextResponse
    if fmt not in ("srt", "vtt", "txt"):
        raise HTTPException(status_code=400, detail="format: srt, vtt ou txt")
    segs = _segments(db, rtvc_id)
    if not segs:
        raise HTTPException(status_code=404, detail="aucune transcription")

    if fmt == "txt":
        body = "\n".join(s["text"] for s in segs) + "\n"
        media_type = "text/plain"
    elif fmt == "srt":
        blocks = []
        for i, s in enumerate(segs, 1):
            blocks.append(
                f"{i}\n{_fmt_ts(s['start_ms'], ',')} --> {_fmt_ts(s['end_ms'], ',')}\n{s['text']}\n"
            )
        body = "\n".join(blocks)
        media_type = "application/x-subrip"
    else:  # vtt
        lines = ["WEBVTT", ""]
        for s in segs:
            lines.append(f"{_fmt_ts(s['start_ms'], '.')} --> {_fmt_ts(s['end_ms'], '.')}")
            lines.append(s["text"])
            lines.append("")
        body = "\n".join(lines)
        media_type = "text/vtt"

    return PlainTextResponse(
        body, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="kairos-{rtvc_id}.{fmt}"'},
    )


@router.delete("/media/{rtvc_id}")
def delete_media(rtvc_id: int, db: Session = Depends(get_db)):
    """Supprime un média et toutes ses données IA (+ fichier de lecture)."""
    from app.models import Embedding, OcrText, Transcription
    from sqlalchemy import delete as sql_delete

    pm = db.get(ProcessedMedia, rtvc_id)
    if pm is None:
        raise HTTPException(status_code=404, detail="media inconnu")
    for model in (Transcription, OcrText, Embedding):
        db.execute(sql_delete(model).where(model.rtvc_id == rtvc_id))
    if pm.playback_path:
        try:
            Path(pm.playback_path).unlink(missing_ok=True)
        except OSError:
            pass
    db.delete(pm)
    db.commit()
    return {"deleted": rtvc_id}


@router.post("/media/retry-failed")
def retry_failed(db: Session = Depends(get_db)):
    """Relance tous les médias en échec d'un coup."""
    from app.worker.tasks import process_local, process_rtvc_nas
    failed = db.execute(
        select(ProcessedMedia).where(ProcessedMedia.status == "failed")
    ).scalars().all()
    n = 0
    for pm in failed:
        if not pm.local_path:
            continue
        pm.status = "pending"
        pm.error = None
        db.commit()
        if pm.source == "rtvc-nas":
            process_rtvc_nas.delay(pm.rtvc_id, pm.local_path, pm.title or str(pm.rtvc_id))
        else:
            process_local.delay(pm.rtvc_id, pm.local_path, pm.title or str(pm.rtvc_id))
        n += 1
    return {"relances": n}


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
