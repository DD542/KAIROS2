"""Accès en direct à la base externe "Transcription Pipeline" (Mike, Supabase).

Kairos ne transcrit plus lui-même (ni Whisper ni Vosk) : chaque recherche
interroge cette base EN DIRECT, à la demande. L'analyse texte (recherche plein
texte Postgres) se fait CÔTÉ BASE, via `to_tsvector`/`plainto_tsquery` calculés
à la volée — rien n'est copié ni ré-indexé localement. Accès strictement
lecture seule (le compte fourni n'a que SELECT).

Clé de jointure : ``transcription.transcripts.media_id`` == ``public.medias.id``
== le ``rtvc_id`` que Kairos utilise partout ailleurs. Vérifié en croisant
`GET /documents/library` (API RTVC, authentifiée) avec cette base : 13/13
médias correspondent exactement (même id, même titre, même nas_path) —
c'est la base RTVC_Stockage elle-même (ou une réplique), avec un schéma
`transcription` ajouté par-dessus.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import psycopg

from app.config import settings

log = logging.getLogger("kairos.transcription_db")

# Mêmes codes que app.lang.pg_config, mais en regconfig SQL : la base externe
# n'a que fr/en/pt à ce jour, "simple" couvre tout le reste sans jamais échouer.
_LANG_CASE_SQL = """
    CASE t.language
        WHEN 'fr' THEN 'french'
        WHEN 'en' THEN 'english'
        WHEN 'pt' THEN 'portuguese'
        ELSE 'simple'
    END::regconfig
"""


def _connect() -> psycopg.Connection:
    return psycopg.connect(settings.transcription_db_dsn, connect_timeout=5)


def get_transcript_language(rtvc_id: int) -> str | None:
    """Langue de la transcription principale d'un média, si elle existe.

    Best-effort, utilisé uniquement pour choisir le bon analyseur OCR/lexical
    local — aucun texte n'est rapatrié ici."""
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT language FROM transcription.transcripts "
                "WHERE media_id = %s AND status = 'done' "
                "ORDER BY is_primary_language DESC NULLS LAST, id LIMIT 1",
                (rtvc_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception as exc:  # noqa: BLE001 - base externe indisponible : dégrade proprement
        log.warning("transcription_db indisponible (langue rtvc_id=%s): %s", rtvc_id, exc)
        return None


def _segments_from_json(transcript_json: dict | None) -> list[dict]:
    if not isinstance(transcript_json, dict):
        return []
    out = []
    for seg in transcript_json.get("segments") or []:
        try:
            out.append({
                "start_ms": int(round(float(seg["start"]) * 1000)),
                "end_ms": int(round(float(seg["end"]) * 1000)),
                "text": (seg.get("text") or "").strip(),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def get_segments(rtvc_id: int, language: str | None = None) -> list[dict]:
    """Transcription horodatée d'un média, lue EN DIRECT (sous-titres lecteur,
    export SRT/VTT/TXT). Renvoie [] si aucune transcription 'done' n'existe."""
    try:
        with _connect() as conn, conn.cursor() as cur:
            if language:
                cur.execute(
                    "SELECT transcript_json FROM transcription.transcripts "
                    "WHERE media_id = %s AND status = 'done' AND language = %s "
                    "ORDER BY id LIMIT 1",
                    (rtvc_id, language),
                )
            else:
                cur.execute(
                    "SELECT transcript_json FROM transcription.transcripts "
                    "WHERE media_id = %s AND status = 'done' "
                    "ORDER BY is_primary_language DESC NULLS LAST, id LIMIT 1",
                    (rtvc_id,),
                )
            row = cur.fetchone()
            return _segments_from_json(row[0]) if row else []
    except Exception as exc:  # noqa: BLE001
        log.warning("transcription_db indisponible (segments rtvc_id=%s): %s", rtvc_id, exc)
        return []


_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _best_segment(segments: list[dict], query: str) -> dict | None:
    """Premier segment contenant un des mots de la requête (repli : le 1er)."""
    if not segments:
        return None
    terms = [w.casefold() for w in _WORD_RE.findall(query) if len(w) >= 2]
    for seg in segments:
        low = seg["text"].casefold()
        if any(t in low for t in terms):
            return seg
    return segments[0]


def stats() -> dict:
    """Compteurs de la base externe (supervision), best-effort."""
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT status, COUNT(*) FROM transcription.transcripts GROUP BY status"
            )
            by_status = dict(cur.fetchall())
            return {"disponible": True, "par_statut": by_status}
    except Exception as exc:  # noqa: BLE001
        log.warning("transcription_db indisponible (stats): %s", exc)
        return {"disponible": False, "par_statut": {}}


def search(query: str, limit: int = 10, media_id: int | None = None) -> list[dict]:
    """Recherche plein texte EN DIRECT dans la base externe.

    L'analyse (correspondance texte, classement) est calculée côté Postgres
    (`to_tsvector`/`plainto_tsquery`, construits à la volée — pas d'index à
    créer, la base est en lecture seule). Seule l'extraction du segment
    précis (pour le timestamp) se fait en Python, sur les quelques lignes déjà
    retenues par la base.
    """
    sql = f"""
        SELECT t.media_id, m.title, t.language, t.transcript_json,
               ts_rank_cd(
                   to_tsvector({_LANG_CASE_SQL}, t.transcript_txt),
                   plainto_tsquery({_LANG_CASE_SQL}, %(q)s)
               ) AS rank
        FROM transcription.transcripts t
        JOIN public.medias m ON m.id = t.media_id
        WHERE t.status = 'done'
          AND to_tsvector({_LANG_CASE_SQL}, t.transcript_txt)
              @@ plainto_tsquery({_LANG_CASE_SQL}, %(q)s)
          {"AND t.media_id = %(media_id)s" if media_id is not None else ""}
        ORDER BY rank DESC
        LIMIT %(limit)s
    """
    params: dict[str, Any] = {"q": query, "limit": limit}
    if media_id is not None:
        params["media_id"] = media_id

    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001 - base externe indisponible : recherche dégradée
        log.warning("transcription_db indisponible (search): %s", exc)
        return []

    hits: list[dict] = []
    for rtvc_id, title, language, transcript_json, rank in rows:
        segments = _segments_from_json(transcript_json)
        seg = _best_segment(segments, query)
        if seg is None:
            continue
        hits.append({
            "rtvc_id": rtvc_id,
            "title": title,
            "source": "audio",
            "start_ms": seg["start_ms"],
            "end_ms": seg["end_ms"],
            "text": seg["text"],
            "score": float(rank),
            "lang": language,
        })
    return hits
