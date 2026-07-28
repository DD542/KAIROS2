"""Tesseract OCR over extracted keyframes. Consecutive identical/near-empty
frames are collapsed so a static slide yields one row, not dozens.

Tesseract ne dit jamais « je ne sais pas » : sur une image sans texte net, il
invente des mots. Non filtrés, ces débris se retrouvent indexés et remontent
dans les résultats avec un score élevé — le texte est court, donc son vecteur
est proche de beaucoup de choses. On s'appuie donc sur la CONFIANCE que
Tesseract associe à chaque mot pour ne garder que ce qu'il a réellement lu.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytesseract
from PIL import Image

from app.config import settings

log = logging.getLogger("kairos.ocr")

MIN_CHARS = 3  # ignore frames whose OCR yields almost nothing

_HAS_ALNUM = re.compile(r"[^\W_]", re.UNICODE)

# ISO 639-1 -> code Tesseract (ISO 639-2/T).
_ISO_TO_TESS = {
    "ar": "ara", "de": "deu", "en": "eng", "es": "spa", "fr": "fra",
    "it": "ita", "nl": "nld", "pt": "por", "ru": "rus", "tr": "tur",
}


@dataclass
class OcrItem:
    timestamp_ms: int
    text: str


@lru_cache
def _installed() -> frozenset[str]:
    try:
        return frozenset(pytesseract.get_languages(config=""))
    except Exception:  # noqa: BLE001 - version trop ancienne : on ne présume rien
        return frozenset()


def tesseract_lang(iso_code: str | None) -> str:
    """Modèle Tesseract à employer, avec repli si le paquet n'est pas installé.

    Lire du texte anglais avec le modèle français dégrade nettement le
    résultat ; mais réclamer un modèle absent fait échouer l'OCR entier. On
    vérifie donc ce qui est réellement disponible dans l'image.
    """
    want = _ISO_TO_TESS.get((iso_code or "").strip().lower()[:2], "")
    have = _installed()
    if want and (not have or want in have):
        return want
    if not have or "fra" in have:
        return "fra"
    return "eng"


def _clean(raw: str) -> str:
    # collapse whitespace, drop control chars
    text = re.sub(r"\s+", " ", raw).strip()
    return text




def _confident_text(image: Image.Image, lang: str) -> str:
    """Texte de l'image, limité aux mots que Tesseract dit avoir bien lus."""
    data = pytesseract.image_to_data(
        image, lang=lang, output_type=pytesseract.Output.DICT
    )
    words = []
    for word, conf in zip(data.get("text", []), data.get("conf", [])):
        try:
            score = float(conf)
        except (TypeError, ValueError):
            continue  # -1 / vide : Tesseract n'a pas évalué ce bloc
        w = (word or "").strip()
        # Un jeton sans lettre ni chiffre (« | », « — », « .:. ») est un
        # artefact de bordure ou de sous-titre, jamais du texte utile.
        if score >= settings.ocr_min_confidence and _HAS_ALNUM.search(w):
            words.append(w)
    return _clean(" ".join(words))


def ocr_keyframes(frames: list[tuple[int, Path]], language: str | None = None) -> list[OcrItem]:
    lang = tesseract_lang(language)
    items: list[OcrItem] = []
    last_text: str | None = None
    dropped = 0
    for ts_ms, path in frames:
        try:
            with Image.open(path) as img:
                text = _confident_text(img, lang)
        except Exception:  # noqa: BLE001 - une image illisible ne doit rien casser
            continue
        if len(text) < MIN_CHARS:
            dropped += 1
            continue
        # dedupe when the same slide is shown across several keyframes
        if last_text is not None and text == last_text:
            continue
        items.append(OcrItem(timestamp_ms=ts_ms, text=text))
        last_text = text
    if dropped:
        log.info("OCR (%s) : %s image(s) sans texte fiable écartée(s)", lang, dropped)
    return items
