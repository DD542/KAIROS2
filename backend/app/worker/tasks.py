"""The Kairos V2 pipeline as a Celery task.

Kairos does NOT transcode or stream — RTVC does. Kairos does NOT transcribe
audio either anymore: speech search is served LIVE from the external
"Transcription Pipeline" database (see app/transcription_db.py), queried at
search time — never copied locally. Per RTVC media_id:
  1. ensure HLS is ready on RTVC (generate-hls + poll hls-status)
  2. download the raw source from RTVC (signed-url / stream) — only if OCR needs it
  3. best-effort: read the transcript LANGUAGE from the external DB (metadata only)
  4. extract keyframes (local FFmpeg) -> Tesseract OCR
  5. embeddings (visual only) -> pgvector
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

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.embeddings import embed_passages
from app.lang import pg_config
from app.models import Embedding, OcrText, ProcessedMedia
from app.pipeline import ocr, transcode
from app.rtvc import get_rtvc
from app import transcription_db
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


def _build_embeddings(db, media_id: int, force: bool = False) -> int:
    """Encode les textes OCR (visuel) d'un média : vecteur (sens) + index lexical.

    L'audio n'est PLUS embarqué ici : Kairos ne transcrit plus localement, la
    recherche sur la parole interroge la base externe "Transcription Pipeline"
    EN DIRECT (voir app/transcription_db.py), sans copie locale. Renvoie le
    nombre de vecteurs écrits.
    """
    if force:
        db.execute(sql_delete(Embedding).where(Embedding.rtvc_id == media_id))
        db.commit()
    elif _count(db, Embedding, media_id) > 0:
        return 0  # déjà fait (idempotence)

    ocrs = [o for o in db.execute(
        select(OcrText).where(OcrText.rtvc_id == media_id)
    ).scalars().all() if _worth_indexing(o.text)]

    pm = db.get(ProcessedMedia, media_id)
    lang = pm.language if pm is not None else None
    # Analyseur lexical choisi d'après la langue détectée : c'est lui qui fait
    # que « chantait » retrouve « chanter » en français.
    cfg = pg_config(lang)

    written = 0
    kf_ms = settings.keyframe_interval_seconds * 1000
    for o, vec in zip(ocrs, embed_passages([o.text for o in ocrs])):
        db.add(Embedding(
            rtvc_id=media_id, source="visual",
            start_ms=o.timestamp_ms, end_ms=o.timestamp_ms + kf_ms,
            text=o.text, embedding=vec,
            lang=lang, tsv=func.to_tsvector(cfg, o.text),
        ))
        written += 1
    db.commit()
    return written


def _index_media(db, media_id: int, video: Path, work: Path) -> tuple[int, int]:
    """Shared AI core: keyframes -> OCR -> pgvector. La transcription audio
    n'est PLUS faite ici (ni Whisper ni Vosk) : la recherche sur la parole
    interroge la base externe "Transcription Pipeline" en direct.

    Returns (nb_transcriptions_locales, nb_ocr). nb_transcriptions_locales est
    désormais toujours 0 — le nombre réel de segments dispo vient de la base
    externe et se lit au moment de la recherche, pas de l'indexation.
    """
    pm = db.get(ProcessedMedia, media_id)
    if pm is not None and pm.language is None:
        # Best-effort : récupère juste le CODE langue (pas le texte) depuis la
        # base externe, pour que l'OCR charge le bon modèle Tesseract.
        lang = transcription_db.get_transcript_language(media_id)
        if lang:
            pm.language = lang
            db.commit()
            log.info("media=%s : langue (base externe) = %s", media_id, lang)

    if _count(db, OcrText, media_id) == 0:
        # L'OCR (visuel) est secondaire : s'il échoue (image illisible, format
        # exotique…), on continue quand même — un média reste cherchable par la
        # parole via la base externe même sans texte à l'écran indexé.
        try:
            frames = transcode.extract_keyframes(video, work / "keyframes")
            pm = db.get(ProcessedMedia, media_id)
            ocr_items = ocr.ocr_keyframes(
                frames, language=pm.language if pm is not None else None
            )
            db.add_all(
                OcrText(rtvc_id=media_id, timestamp_ms=o.timestamp_ms, text=o.text)
                for o in ocr_items
            )
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            log.warning("media=%s : OCR ignoré (%s)", media_id, exc)

    _build_embeddings(db, media_id)

    n_transcript_ext = len(transcription_db.get_segments(media_id))
    return n_transcript_ext, _count(db, OcrText, media_id)


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
def index_all_rtvc(self, root: str = "", max_seconds: int | None = None,
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


@celery_app.task(bind=True, name="kairos.reindex_embeddings")
def reindex_embeddings(self, media_id: int | None = None) -> dict:
    """Ré-encode les textes déjà transcrits, sans repasser par ffmpeg ni Whisper.

    C'est ce qui rend un changement de modèle d'embeddings praticable : la
    partie coûteuse (transcription, OCR) est conservée, seule l'étape rapide
    est refaite. Sert aussi à peupler l'index lexical d'une bibliothèque
    indexée avant l'arrivée de la recherche hybride.
    """
    db = SessionLocal()
    try:
        ids = [media_id] if media_id is not None else [
            i for (i,) in db.execute(
                select(ProcessedMedia.rtvc_id).where(ProcessedMedia.status == "ready")
            ).all()
        ]
        total = 0
        for mid in ids:
            try:
                total += _build_embeddings(db, mid, force=True)
            except Exception as exc:  # noqa: BLE001 - un média ne bloque pas les autres
                db.rollback()
                log.warning("reindex media=%s ignoré (%s)", mid, exc)
        log.info("reindex : %s vecteurs réécrits sur %s média(s)", total, len(ids))
        return {"medias": len(ids), "vecteurs": total}
    finally:
        db.close()


@celery_app.task(bind=True, name="kairos.reocr", **_RETRY)
def reocr(self, media_id: int | None = None) -> dict:
    """Relit le texte à l'écran depuis la copie de lecture conservée sur disque.

    Les médias indexés avant le filtrage par confiance de Tesseract portent des
    suites de lettres inventées. Courtes et sans voisinage sémantique naturel,
    elles obtiennent un bon score sur à peu près n'importe quelle question et
    faussent tout le classement.

    On les relit plutôt que de deviner lesquelles sont fausses : le fichier de
    lecture est toujours là, donc ni téléchargement ni transcription à refaire —
    seulement ffmpeg et Tesseract. La transcription audio n'est pas touchée.
    """
    db = SessionLocal()
    try:
        stmt = select(ProcessedMedia).where(ProcessedMedia.playback_path.isnot(None))
        if media_id is not None:
            stmt = stmt.where(ProcessedMedia.rtvc_id == media_id)
        medias = db.execute(stmt).scalars().all()

        traites, avant, apres = 0, 0, 0
        for pm in medias:
            video = Path(pm.playback_path)
            if not video.is_file():
                continue
            work = _work_dir(pm.rtvc_id) / "reocr"
            try:
                frames = transcode.extract_keyframes(video, work / "keyframes")
                items = ocr.ocr_keyframes(frames, language=pm.language)
            except Exception as exc:  # noqa: BLE001 - un média n'arrête pas les autres
                log.warning("reocr media=%s ignoré (%s)", pm.rtvc_id, exc)
                continue
            finally:
                shutil.rmtree(work, ignore_errors=True)

            avant += _count(db, OcrText, pm.rtvc_id)
            db.execute(sql_delete(OcrText).where(OcrText.rtvc_id == pm.rtvc_id))
            db.execute(sql_delete(Embedding).where(
                Embedding.rtvc_id == pm.rtvc_id, Embedding.source == "visual"
            ))
            db.add_all(
                OcrText(rtvc_id=pm.rtvc_id, timestamp_ms=o.timestamp_ms, text=o.text)
                for o in items
            )
            db.commit()
            apres += len(items)
            traites += 1
            # les vecteurs « à l'écran » viennent d'être supprimés : on les
            # reconstruit à partir du texte relu
            _build_embeddings(db, pm.rtvc_id, force=True)

        log.info("reocr : %s média(s), %s → %s entrées à l'écran", traites, avant, apres)
        return {"medias": traites, "avant": avant, "apres": apres}
    finally:
        db.close()


@celery_app.task(bind=True, name="kairos.recover_stuck")
def recover_stuck(self, older_than_minutes: int = 60) -> dict:
    """Relance les médias restés bloqués en « processing ».

    Un worker tué en plein travail (redémarrage, plus de mémoire) laisse un
    média dans cet état pour toujours : il n'apparaît ni comme prêt, ni comme
    échoué, donc personne ne le relance. On ne touche qu'aux médias assez
    anciens pour ne pas interrompre un traitement réellement en cours.
    """
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
    db = SessionLocal()
    try:
        stuck = db.execute(
            select(ProcessedMedia)
            .where(ProcessedMedia.status == "processing")
            .where(ProcessedMedia.updated_at < cutoff)
        ).scalars().all()
        n = 0
        for pm in stuck:
            if not pm.local_path:
                continue
            pm.status = "pending"
            pm.error = None
            db.commit()
            title = pm.title or str(pm.rtvc_id)
            if pm.source == "rtvc-nas":
                process_rtvc_nas.delay(pm.rtvc_id, pm.local_path, title)
            else:
                process_local.delay(pm.rtvc_id, pm.local_path, title)
            n += 1
        if n:
            log.info("reprise : %s média(s) bloqués relancés", n)
        return {"relances": n}
    finally:
        db.close()


VIDEO_EXT = {".mp4", ".ts", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".mpg", ".mpeg"}


def _next_local_id(db) -> int:
    return (db.scalar(
        select(func.max(ProcessedMedia.rtvc_id)).where(
            ProcessedMedia.rtvc_id >= LOCAL_ID_BASE
        )
    ) or LOCAL_ID_BASE) + 1


@celery_app.task(bind=True, name="kairos.autosync")
def autosync(self) -> dict:
    """Balaye les sources configurées et met en file ce qui n'est pas indexé.

    Pensé pour une installation posée sur un système qui possède DÉJÀ sa
    bibliothèque : plus rien à déclencher à la main, Kairos rattrape le stock
    puis suit les ajouts. Idempotent — un média déjà connu (même ``local_path``)
    est ignoré, donc le passage périodique ne refait jamais le même travail.
    L'indexation manuelle reste disponible et utilise les mêmes tâches.
    """
    if not settings.autosync_enabled:
        return {"actif": False}

    db = SessionLocal()
    queued: list[str] = []
    try:
        known = {p for (p,) in db.execute(select(ProcessedMedia.local_path)).all() if p}
        budget = settings.autosync_batch

        # --- source 1 : dossier local monté -------------------------------
        if settings.autosync_local and budget > 0:
            root = Path(settings.media_input_dir)
            if root.is_dir():
                for f in sorted(root.rglob("*")):
                    if budget <= 0:
                        break
                    if not f.is_file() or f.suffix.lower() not in VIDEO_EXT:
                        continue
                    path = str(f)
                    if path in known:
                        continue
                    media_id = _next_local_id(db)
                    db.add(ProcessedMedia(rtvc_id=media_id, title=f.stem,
                                          source="local", local_path=path,
                                          status="pending"))
                    db.commit()
                    process_local.delay(media_id, path, f.stem, None)
                    known.add(path)
                    queued.append(path)
                    budget -= 1

        # --- source 2 : NAS RTVC (seulement si une racine est configurée) ---
        if settings.autosync_rtvc_root and budget > 0:
            from pathlib import PurePosixPath
            try:
                paths = get_rtvc().list_videos_recursive(settings.autosync_rtvc_root)
            except Exception as exc:  # noqa: BLE001 - RTVC HS : on réessaiera
                log.warning("autosync : NAS RTVC injoignable (%s)", exc)
                paths = []
            for path in paths:
                if budget <= 0:
                    break
                if path in known:
                    continue
                media_id = _next_local_id(db)
                title = PurePosixPath(path).stem
                db.add(ProcessedMedia(rtvc_id=media_id, title=title,
                                      source="rtvc-nas", local_path=path,
                                      status="pending"))
                db.commit()
                process_rtvc_nas.delay(media_id, path, title, None, None)
                known.add(path)
                queued.append(path)
                budget -= 1

        if queued:
            log.info("autosync : %s nouveau(x) média(s) mis en file", len(queued))
        return {"actif": True, "mises_en_file": len(queued), "chemins": queued[:10]}
    finally:
        db.close()


@celery_app.task(bind=True, name="kairos.process_rtvc_nas", **_RETRY)
def process_rtvc_nas(self, media_id: int, nas_path: str, title: str,
                     max_seconds: int | None = None,
                     max_mb: int | None = None) -> dict:
    """Indexe une vidéo stockée sur le NAS RTVC, via son chemin de fichier.

    C'est la voie qui fonctionne réellement : /nas/download livre les octets
    là où signed-url et /documents/{id}/stream échouent.
    """
    # 0 = « pas de limite » (même convention que l'interface). Sans ça, un 0
    # transmis tel quel retombait sur le plafond de sondage et la vidéo servie
    # au lecteur était tronquée.
    max_seconds = max_seconds or None
    max_mb = max_mb or None

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

        # 2. download the raw source for local AI processing (OCR only now)
        src = work / "source"
        if _count(db, OcrText, media_id) == 0:
            rtvc.download_source(media_id, src)
            pm.duration_ms = transcode.probe_duration_ms(src)
            db.commit()

        # 3. langue (best-effort, depuis la base externe "Transcription
        #    Pipeline" — Kairos ne transcrit plus lui-même, voir _index_media)
        if pm.language is None:
            lang = transcription_db.get_transcript_language(media_id)
            if lang:
                pm.language = lang
                db.commit()

        # 4. keyframes -> Tesseract OCR (idempotent)
        if _count(db, OcrText, media_id) == 0:
            frames = transcode.extract_keyframes(src, work / "keyframes")
            ocr_items = ocr.ocr_keyframes(frames, language=pm.language)
            db.add_all(
                OcrText(rtvc_id=media_id, timestamp_ms=o.timestamp_ms, text=o.text)
                for o in ocr_items
            )
            db.commit()

        # 5. embeddings (audio + visual) -> pgvector (idempotent)
        _build_embeddings(db, media_id)

        # 6. done
        pm.status = "ready"
        pm.processed_at = datetime.now(timezone.utc)
        db.commit()

        shutil.rmtree(work, ignore_errors=True)
        return {
            "rtvc_id": media_id,
            "status": "ready",
            # transcription: plus stockée localement, lue en direct à la recherche.
            "transcript_segments_externes": len(transcription_db.get_segments(media_id)),
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
