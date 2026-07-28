"""Correspondance entre la langue détectée et l'analyseur lexical Postgres.

La transcription renvoie un code ISO ("fr", "en", "es"…). Postgres, lui, veut
le nom d'une configuration de recherche plein texte ("french", "english"…) qui
détermine la racinisation et les mots vides. Un mauvais choix dégrade la
recherche en silence : sans racinisation française, « chantait » ne trouve pas
« chanter ».
"""

from __future__ import annotations

# Configurations livrées en standard avec PostgreSQL.
_ISO_TO_PG = {
    "ar": "arabic",
    "da": "danish",
    "de": "german",
    "el": "greek",
    "en": "english",
    "es": "spanish",
    "fi": "finnish",
    "fr": "french",
    "hu": "hungarian",
    "id": "indonesian",
    "it": "italian",
    "lt": "lithuanian",
    "ne": "nepali",
    "nl": "dutch",
    "no": "norwegian",
    "pt": "portuguese",
    "ro": "romanian",
    "ru": "russian",
    "sv": "swedish",
    "ta": "tamil",
    "tr": "turkish",
}

# Repli : découpe en mots sans racinisation ni mots vides. Correct pour toute
# langue non gérée (japonais, chinois…) — moins fin, mais jamais faux.
FALLBACK = "simple"


def pg_config(iso_code: str | None) -> str:
    """Configuration Postgres à utiliser pour une langue donnée."""
    if not iso_code:
        return FALLBACK
    return _ISO_TO_PG.get(iso_code.strip().lower()[:2], FALLBACK)


def supported_configs() -> set[str]:
    """Toutes les configurations que Kairos peut produire."""
    return set(_ISO_TO_PG.values()) | {FALLBACK}
