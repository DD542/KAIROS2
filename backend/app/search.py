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

import re
import unicodedata

from sqlalchemy import func, select, text
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


# Une suggestion doit tenir sur une ligne : on coupe à la frontière d'un mot
# plutôt qu'au milieu, sinon la liste déroulante affiche des moitiés de mots.
_SUGGEST_MAX_CHARS = 70


# Contexte affiché AVANT le terme saisi. Sans lui, un mot situé en fin de
# segment produisait une suggestion réduite à ce seul mot — inutile.
_SUGGEST_LEAD_CHARS = 26


def _phrase_around(text: str, needle: str) -> str:
    """Extrait une expression lisible centrée sur le terme saisi."""
    flat = " ".join((text or "").split())
    folded, idx = _fold_map(flat)
    hit = folded.find(_fold(needle))
    if hit < 0:
        return flat[:_SUGGEST_MAX_CHARS]
    pos = idx[hit]

    # on recule d'un peu de contexte, en s'arrêtant sur un début de mot
    start = max(pos - _SUGGEST_LEAD_CHARS, 0)
    if start:
        nxt = flat.find(" ", start)
        start = nxt + 1 if 0 <= nxt < pos else pos
    lead = "…" if start > 0 else ""

    snippet = flat[start:start + _SUGGEST_MAX_CHARS]
    if len(flat) - start > _SUGGEST_MAX_CHARS:
        snippet = snippet.rsplit(" ", 1)[0] + "…"
    return (lead + snippet.strip(" ,.;:!?")).strip()


def _fold_map(s: str) -> tuple[str, list[int]]:
    """Minuscules sans accents, + position d'origine de chaque caractère replié.

    Personne ne tape les accents dans une barre de recherche ; exiger la forme
    accentuée revenait à cacher la moitié du contenu d'une bibliothèque
    francophone. La table d'index permet de reporter une correspondance trouvée
    sur le texte replié vers le texte affiché, sans supposer que les deux ont la
    même longueur (une ligature comme « œ » se déplie en deux caractères).
    """
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(s or ""):
        for c in unicodedata.normalize("NFD", ch):
            if unicodedata.combining(c):
                continue
            out.append(c.lower())
            idx.append(i)
    return "".join(out), idx


def _fold(s: str) -> str:
    return _fold_map(s)[0]


def _starts_a_word(text: str, needle: str) -> bool:
    """Le terme saisi commence-t-il un mot du texte ?

    Sans ce filtre, taper « dieu » proposait « adieu » : une correspondance de
    sous-chaîne au milieu d'un autre mot, que l'utilisateur ne reconnaît pas
    comme sa frappe.
    """
    return re.search(r"(?<!\w)" + re.escape(_fold(needle)), _fold(text)) is not None


_unaccent_flag: bool | None = None


def _unaccent_available(db: Session) -> bool:
    """L'extension unaccent est-elle installée ? (test fait une seule fois)"""
    global _unaccent_flag
    if _unaccent_flag is None:
        try:
            _unaccent_flag = bool(db.execute(
                select(func.count()).select_from(text("pg_extension"))
                .where(text("extname = 'unaccent'"))
            ).scalar())
        except Exception:  # noqa: BLE001 - en cas de doute, on reste littéral
            db.rollback()
            _unaccent_flag = False
    return _unaccent_flag


def suggest(db: Session, query: str, limit: int = 8) -> list[dict]:
    """Complétions tirées du contenu réellement indexé.

    Deux familles, dans cet ordre d'utilité : les titres de vidéos qui
    correspondent (l'utilisateur cherche souvent « la vidéo sur X »), puis des
    bouts de phrases prononcées ou affichées à l'écran. Volontairement lexical
    (et non vectoriel) : pendant la frappe, il faut une réponse en quelques
    millisecondes, et l'utilisateur veut voir le mot qu'il vient de taper.
    """
    q = query.strip()
    if len(q) < 2:
        return []
    # échappe les jokers LIKE pour qu'un « % » saisi reste un caractère normal
    pattern = "%" + re.sub(r"([%_\\])", r"\\\1", q) + "%"
    # Le filtre SQL ignore les accents quand l'extension unaccent est présente
    # (installée au démarrage) ; sinon on retombe sur une comparaison littérale.
    unaccent = _unaccent_available(db)

    def like(col):
        if unaccent:
            return func.unaccent(col).ilike(func.unaccent(pattern))
        return col.ilike(pattern)

    out: list[dict] = []
    seen: set[str] = set()

    titles = db.execute(
        select(ProcessedMedia.rtvc_id, ProcessedMedia.title)
        .where(ProcessedMedia.status == "ready")
        .where(like(ProcessedMedia.title))
        .order_by(func.length(ProcessedMedia.title))
        .limit(limit)
    ).mappings().all()
    for t in titles:
        key = (t["title"] or "").lower()
        if key and key not in seen:
            seen.add(key)
            out.append({"text": t["title"], "kind": "titre", "media_id": t["rtvc_id"]})

    if len(out) < limit:
        rows = db.execute(
            select(Embedding.text, Embedding.source)
            .join(ProcessedMedia, ProcessedMedia.rtvc_id == Embedding.rtvc_id)
            .where(ProcessedMedia.status == "ready")
            .where(like(Embedding.text))
            .order_by(func.length(Embedding.text))
            # on ratisse large : le filtrage qualité se fait ensuite en Python,
            # là où on peut exiger une vraie frontière de mot.
            .limit(limit * 10)
        ).mappings().all()
        for r in rows:
            if not _starts_a_word(r["text"], q):
                continue
            phrase = _phrase_around(r["text"], q)
            # une proposition d'un seul mot n'apprend rien de plus que ce que
            # l'utilisateur vient de taper : on ne l'affiche pas.
            if len(phrase.split()) < 2:
                continue
            key = phrase.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({"text": phrase, "kind": r["source"], "media_id": None})
            if len(out) >= limit:
                break

    return out[:limit]
