"""Évolutions de schéma appliquées au démarrage.

``Base.metadata.create_all`` crée les tables absentes mais n'ajoute jamais une
colonne à une table existante. Ces instructions comblent l'écart, sur une base
déjà remplie, sans outil de migration supplémentaire. Toutes sont idempotentes :
les rejouer à chaque démarrage ne coûte rien.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger("kairos.migrate")

# Extensions optionnelles : leur absence dégrade une fonction, elle ne doit
# jamais empêcher le démarrage (l'utilisateur peut ne pas être superutilisateur).
_EXTENSIONS = ("vector", "unaccent", "pg_trgm")

_STATEMENTS = (
    # Recherche hybride : moitié lexicale de l'index.
    "ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS lang text",
    "ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS tsv tsvector",
    "CREATE INDEX IF NOT EXISTS idx_embeddings_tsv ON embeddings USING gin (tsv)",
    # Suggestions : sans cet index, la complétion fait un balayage complet et
    # devient perceptible autour de quelques dizaines de milliers de segments.
    "CREATE INDEX IF NOT EXISTS idx_embeddings_text_trgm "
    "ON embeddings USING gin (text gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_media_title_trgm "
    "ON processed_media USING gin (title gin_trgm_ops)",
    # La bibliothèque se liste toujours par date décroissante.
    "CREATE INDEX IF NOT EXISTS idx_media_created_at "
    "ON processed_media (created_at DESC)",
)


def run(engine: Engine) -> None:
    for ext in _EXTENSIONS:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"CREATE EXTENSION IF NOT EXISTS {ext}"))
        except Exception as exc:  # noqa: BLE001
            log.warning("extension %s indisponible (%s) — fonction dégradée", ext, exc)

    for stmt in _STATEMENTS:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception as exc:  # noqa: BLE001
            # Typiquement : l'extension requise par un index manque. On continue,
            # le code de recherche sait fonctionner sans.
            log.warning("migration ignorée (%s) : %s", exc, stmt.split(" ON ")[0])
