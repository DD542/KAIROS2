"""The Kairos V2 pipeline as a Celery task.

Kairos does NOT transcode or stream — RTVC does. Per RTVC media_id:
  1. ensure HLS is ready on RTVC (generate-hls + poll hls-status)
  2. download the raw source from RTVC (signed-url / stream)
  3. extract audio (local FFmpeg) -> Vosk transcription
  4. extract keyframes (local FFmpeg) -> Tesseract OCR
  5. embeddings (audio + visual) -> pgvector
  6. mark ready

Idempotent: each sub-step is skipped if its rows already exist for the media_id,
so re-delivery of the webhook is safe.
"""

from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("kairos.pipeline")

from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.embeddings import embed_many
from app.models import Embedding, OcrText, ProcessedMedia, Transcription
from app.pipeline import ocr, transcode, transcribe
from app.rtvc import get_rtvc
from app.worker.celery_app import celery_app


def _work_dir(rtvc_id: int) -> Path:
    return Path(settings.data_dir) / "work" / str(rtvc_id)


def _count(db, model, rtvc_id: int) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.rtvc_id == rtvc_id)) or 0


# Un segment d'un ou deux mots ("non", "cas", "en") ne porte pas de sens
# exploitable : son vecteur est instable et il pollue le classement. On le garde
# dans la transcription (affichage) mais on ne l'indexe pas.
MIN_INDEX_CHARS = 15
MIN_INDEX_WORDS = 3


def _worth_indexing(text: str) -> bool:
    t = (text or "").strip()
    return len(t) >= MIN_INDEX_CHARS and len(t.split()) >= MIN_INDEX_WORDS


def _index_media(db, media_id: int, video: Path, work: Path) -> tuple[int, int]:
    """Shared AI core: audio -> Vosk, keyframes -> OCR, both -> pgvector.

    Returns (nb_transcriptions, nb_ocr). Each sub-step is skipped when its rows
    already exist for this media, so re-runs are safe.
    """
    if _count(db, Transcription, media_id) == 0:
        wav = transcode.extract_audio_wav(video, work / "audio.wav")
        segments = transcribe.transcribe(wav)
        db.add_all(
            Transcription(rtvc_id=media_id, start_ms=s.start_ms, end_ms=s.end_ms, text=s.text)
            for s in segments
        )
        db.commit()

    if _count(db, OcrText, media_id) == 0:
        # L'OCR (visuel) est secondaire : s'il échoue (image illisible, format
        # exotique…), on continue quand même — la transcription audio, elle,
        # est déjà en base. Un média reste ainsi cherchable par la parole.
        try:
            frames = transcode.extract_keyframes(video, work / "keyframes")
            ocr_items = ocr.ocr_keyframes(frames)
            db.add_all(
                OcrText(rtvc_id=media_id, timestamp_ms=o.timestamp_ms, text=o.text)
                for o in ocr_items
            )
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            log.warning("media=%s : OCR ignoré (%s)", media_id, exc)

    if _count(db, Embedding, media_id) == 0:
        trans = [t for t in db.execute(
            select(Transcription).where(Transcription.rtvc_id == media_id)
        ).scalars().all() if _worth_indexing(t.text)]
        ocrs = [o for o in db.execute(
            select(OcrText).where(OcrText.rtvc_id == media_id)
        ).scalars().all() if _worth_indexing(o.text)]

        for t, vec in zip(trans, embed_many([t.text for t in trans])):
            db.add(Embedding(
                rtvc_id=media_id, source="audio",
                start_ms=t.start_ms, end_ms=t.end_ms, text=t.text, embedding=vec,
            ))
        kf_ms = settings.keyframe_interval_seconds * 1000
        for o, vec in zip(ocrs, embed_many([o.text for o in ocrs])):
            db.add(Embedding(
                rtvc_id=media_id, source="visual",
                start_ms=o.timestamp_ms, end_ms=o.timestamp_ms + kf_ms,
                text=o.text, embedding=vec,
            ))
        db.commit()

    return _count(db, Transcription, media_id), _count(db, OcrText, media_id)


def _ingest_file(db, pm, media_id: int, src: Path, max_seconds: int | None) -> dict:
    """Étapes communes une fois le fichier disponible localement."""
    t0 = time.monotonic()
    log.info("indexation media=%s source=%s : debut", media_id, pm.source)
    work = _work_dir(media_id)
    playback = Path(settings.data_dir) / "playback" / f"{media_id}.mp4"
    if not playback.exists():
        transcode.make_playback_mp4(src, playback, max_seconds)
    pm.playback_path = str(playback)
    pm.duration_ms = transcode.probe_duration_ms(playback)
    db.commit()

    n_tr, n_ocr = _index_media(db, media_id, playback, work)

    pm.status = "ready"
    pm.processed_at = datetime.now(timezone.utc)
    db.commit()
    shutil.rmtree(work, ignore_errors=True)
    dt = round(time.monotonic() - t0, 1)
    log.info("indexation media=%s : OK en %ss (%s segments, %s ocr)",
             media_id, dt, n_tr, n_ocr)
    return {"media_id": media_id, "status": "ready",
            "transcriptions": n_tr, "ocr_texts": n_ocr, "duree_s": dt}


# Reprise auto en cas d'échec transitoire (RTVC momentanément down, réseau…) :
# 3 tentatives espacées (30 s, 60 s), puis on abandonne définitivement.
_RETRY = dict(autoretry_for=(Exception,), max_retries=2,
              retry_backoff=30, retry_backoff_max=300, retry_jitter=True)


LOCAL_ID_BASE = 900000  # identifiants internes des médias hors bibliothèque RTVC


@celery_app.task(bind=True, name="kairos.index_all_rtvc")
def index_all_rtvc(self, root: str = "", max_seconds: int | None = 180,
                   max_mb: int | None = None) -> dict:
    """Scanne récursivement un dossier NAS et met en file l'indexation de
    chaque vidéo pas encore indexée. Une seule tâche « chef » qui délègue une
    tâche par vidéo — la recherche couvrira ensuite tout le dossier."""
    from pathlib import PurePosixPath
    rtvc = get_rtvc()
    paths = rtvc.list_videos_recursive(root)
    log.info("index-all root=%r : %s vidéos trouvées", root, len(paths))

    db = SessionLocal()
    queued = 0
    try:
        existing = {
            p for (p,) in db.execute(select(ProcessedMedia.local_path)).all() if p
        }
        max_id = db.scalar(
            select(func.max(ProcessedMedia.rtvc_id)).where(
                ProcessedMedia.rtvc_id >= LOCAL_ID_BASE
            )
        ) or LOCAL_ID_BASE
        for path in paths:
            if path in existing:
                continue  # déjà indexé (idempotence)
            max_id += 1
            title = PurePosixPath(path).stem
            db.add(ProcessedMedia(rtvc_id=max_id, title=title, source="rtvc-nas",
                                  local_path=path, status="pending"))
            db.commit()
            process_rtvc_nas.delay(max_id, path, title, max_seconds, max_mb)
            queued += 1
        return {"root": root, "trouvees": len(paths), "mises_en_file": queued}
    finally:
        db.close()


@celery_app.task(bind=True, name="kairos.process_rtvc_nas", **_RETRY)
def process_rtvc_nas(self, media_id: int, nas_path: str, title: str,
                     max_seconds: int | None = 180,
                     max_mb: int | None = None) -> dict:
    """Indexe une vidéo stockée sur le NAS RTVC, via son chemin de fichier.

    C'est la voie qui fonctionne réellement : /nas/download livre les octets
    là où signed-url et /documents/{id}/stream échouent.
    """
    db = SessionLocal()
    try:
        pm = db.get(ProcessedMedia, media_id)
        if pm is None:
            pm = ProcessedMedia(rtvc_id=media_id, title=title, source="rtvc-nas",
                                local_path=nas_path)
            db.add(pm)
        pm.status = "processing"
        pm.error = None
        db.commit()

        src = _work_dir(media_id) / "source"
        rtvc = get_rtvc()

        # Si l'utilisateur impose un cap, on le respecte tel quel. Sinon, on
        # télécharge d'abord une portion (rapide) et on ne rapatrie le fichier
        # ENTIER que si le transcodage échoue (cas des .mp4 dont l'index est en
        # fin de fichier, illisibles s'ils sont tronqués). Fast pour la plupart,
        # fiable pour tous.
        cap_bytes = (max_mb * 1024 * 1024) if max_mb else settings.download_probe_mb * 1024 * 1024
        capped = max_mb is None  # on a le droit de retélécharger en entier

        if not src.exists():
            rtvc.download_nas_file(nas_path, src, max_bytes=cap_bytes)
        try:
            return _ingest_file(db, pm, media_id, src, max_seconds)
        except RuntimeError as ffmpeg_err:
            if not capped:
                raise
            log.warning("media=%s : transcodage KO sur portion (%s) -> "
                        "retelechargement complet", media_id, ffmpeg_err)
            db.rollback()
            src.unlink(missing_ok=True)
            (Path(settings.data_dir) / "playback" / f"{media_id}.mp4").unlink(missing_ok=True)
            rtvc.download_nas_file(nas_path, src, max_bytes=None)  # entier
            pm = db.get(ProcessedMedia, media_id)
            pm.status = "processing"
            pm.error = None
            db.commit()
            return _ingest_file(db, pm, media_id, src, max_seconds)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        pm = db.get(ProcessedMedia, media_id)
        if pm is not None:
            pm.status = "failed"
            pm.error = str(exc)
            db.commit()
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name="kairos.process_local", **_RETRY)
def process_local(self, media_id: int, src_path: str, title: str,
                  max_seconds: int | None = None) -> dict:
    """Index a video straight from disk — no RTVC involved.

    Lets the full Kairos promise (question -> exact timestamp -> jump) be
    demonstrated even when RTVC's storage backend is unavailable.
    """
    db = SessionLocal()
    work = _work_dir(media_id)
    try:
        pm = db.get(ProcessedMedia, media_id)
        if pm is None:
            pm = ProcessedMedia(rtvc_id=media_id, title=title, source="local",
                                local_path=src_path)
            db.add(pm)
        pm.status = "processing"
        pm.error = None
        db.commit()

        src = Path(src_path)
        if not src.exists():
            raise FileNotFoundError(f"fichier introuvable: {src}")

        # Browser-playable copy (also trims the clip when max_seconds is set);
        # every later step works on this file so timestamps stay consistent.
        playback = Path(settings.data_dir) / "playback" / f"{media_id}.mp4"
        if not playback.exists():
            transcode.make_playback_mp4(src, playback, max_seconds)
        pm.playback_path = str(playback)
        pm.duration_ms = transcode.probe_duration_ms(playback)
        db.commit()

        n_tr, n_ocr = _index_media(db, media_id, playback, work)

        pm.status = "ready"
        pm.processed_at = datetime.now(timezone.utc)
        db.commit()
        shutil.rmtree(work, ignore_errors=True)
        return {"media_id": media_id, "status": "ready",
                "transcriptions": n_tr, "ocr_texts": n_ocr}
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        pm = db.get(ProcessedMedia, media_id)
        if pm is not None:
            pm.status = "failed"
            pm.error = str(exc)
            db.commit()
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name="kairos.process_media", **_RETRY)
def process_media(self, media_id: int, title: str | None = None) -> dict:
    db = SessionLocal()
    rtvc = get_rtvc()
    work = _work_dir(media_id)
    try:
        pm = db.get(ProcessedMedia, media_id)
        if pm is None:
            pm = ProcessedMedia(rtvc_id=media_id, title=title)
            db.add(pm)
        elif title and not pm.title:
            pm.title = title

        if pm.status == "ready":
            return {"rtvc_id": media_id, "status": "ready", "skipped": True}

        pm.status = "processing"
        pm.error = None
        db.commit()

        # 1. ensure RTVC has produced HLS (this is the Step-3 keystone)
        rtvc.wait_for_hls(media_id)
        pm.hls_ready = True
        db.commit()

        # 2. download the raw source for local AI processing
        src = work / "source"
        need_media = _count(db, Transcription, media_id) == 0 or \
            _count(db, OcrText, media_id) == 0
        if need_media:
            rtvc.download_source(media_id, src)
            pm.duration_ms = transcode.probe_duration_ms(src)
            db.commit()

        # 3. audio -> Vosk (idempotent)
        if _count(db, Transcription, media_id) == 0:
            wav = transcode.extract_audio_wav(src, work / "audio.wav")
            segments = transcribe.transcribe(wav)
            db.add_all(
                Transcription(rtvc_id=media_id, start_ms=s.start_ms, end_ms=s.end_ms, text=s.text)
                for s in segments
            )
            db.commit()

        # 4. keyframes -> Tesseract OCR (idempotent)
        if _count(db, OcrText, media_id) == 0:
            frames = transcode.extract_keyframes(src, work / "keyframes")
            ocr_items = ocr.ocr_keyframes(frames)
            db.add_all(
                OcrText(rtvc_id=media_id, timestamp_ms=o.timestamp_ms, text=o.text)
                for o in ocr_items
            )
            db.commit()

        # 5. embeddings (audio + visual) -> pgvector (idempotent)
        if _count(db, Embedding, media_id) == 0:
            trans = db.execute(
                select(Transcription).where(Transcription.rtvc_id == media_id)
            ).scalars().all()
            ocrs = db.execute(
                select(OcrText).where(OcrText.rtvc_id == media_id)
            ).scalars().all()

            audio_vecs = embed_many([t.text for t in trans])
            visual_vecs = embed_many([o.text for o in ocrs])

            for t, vec in zip(trans, audio_vecs):
                db.add(Embedding(
                    rtvc_id=media_id, source="audio",
                    start_ms=t.start_ms, end_ms=t.end_ms, text=t.text, embedding=vec,
                ))
            kf_ms = settings.keyframe_interval_seconds * 1000
            for o, vec in zip(ocrs, visual_vecs):
                db.add(Embedding(
                    rtvc_id=media_id, source="visual",
                    start_ms=o.timestamp_ms, end_ms=o.timestamp_ms + kf_ms,
                    text=o.text, embedding=vec,
                ))
            db.commit()

        # 6. done
        pm.status = "ready"
        pm.processed_at = datetime.now(timezone.utc)
        db.commit()

        shutil.rmtree(work, ignore_errors=True)
        return {
            "rtvc_id": media_id,
            "status": "ready",
            "transcriptions": _count(db, Transcription, media_id),
            "ocr_texts": _count(db, OcrText, media_id),
        }
    except Exception as exc:  # noqa: BLE001 - record and re-raise for Celery
        db.rollback()
        pm = db.get(ProcessedMedia, media_id)
        if pm is not None:
            pm.status = "failed"
            pm.error = str(exc)
            db.commit()
        raise
    finally:
        db.close()
