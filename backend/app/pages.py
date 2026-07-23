"""Rendu des pages HTML avec versionnage des fichiers statiques.

Le navigateur garde CSS et JS en cache : après une modification, il continue
d'exécuter l'ancienne version (l'explorateur RTVC restait bloqué sur
« Connexion… » pour cette raison). On ajoute donc à chaque référence
``/static/<fichier>`` un paramètre ``?v=<date de modification>`` : l'URL change
dès que le fichier change, ce qui force le rechargement — et seulement alors.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.config import settings

_ASSET_RE = re.compile(r'(/static/([A-Za-z0-9_.-]+))')


def render_page(filename: str) -> str | None:
    """Lit une page du frontend et versionne ses fichiers statiques."""
    root = Path(settings.frontend_dir)
    page = root / filename
    if not page.is_file():
        return None

    def _stamp(match: re.Match) -> str:
        url, asset = match.group(1), match.group(2)
        target = root / asset
        try:
            return f"{url}?v={int(target.stat().st_mtime)}"
        except OSError:
            return url

    return _ASSET_RE.sub(_stamp, page.read_text(encoding="utf-8"))
