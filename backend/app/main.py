"""Kairos backend — FastAPI application (V2).

Kairos is a semantic-search layer on top of the RTVC_Stockage API. It exposes:
  - POST /webhook/rtvc-media-created  (ingest / index a media)
  - GET  /search                      (hybrid audio+OCR semantic search)
plus playback helpers that proxy RTVC's signed stream-token, and the demo UI.
"""

from __future__ import annotations

import base64
import secrets
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import settings
from app.db import Base, engine
from app.pages import render_page
from app.routes import ingest, media, search, stats, webhook

app = FastAPI(
    title="Kairos",
    version="2.0.0",
    description="Couche de recherche sémantique (faster-whisper + OCR + pgvector) au-dessus de l'API RTVC.",
)

# ---- Protection par mot de passe (si KAIROS_PASSWORD est défini) ----------
# HTTP Basic : le navigateur mémorise le mot de passe après la 1re saisie, donc
# la vidéo, les fichiers statiques et les appels /search passent ensuite sans
# friction. /health reste ouvert (healthchecks Docker / supervision).
_AUTH_EXEMPT = {"/health"}


@app.middleware("http")
async def _password_gate(request: Request, call_next):
    pwd = settings.kairos_password
    if pwd and request.url.path not in _AUTH_EXEMPT:
        header = request.headers.get("authorization", "")
        ok = False
        if header.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8", "replace")
                # accepte n'importe quel nom d'utilisateur ; seul le mot de
                # passe compte (comparaison à temps constant)
                candidate = decoded.split(":", 1)[1] if ":" in decoded else decoded
                ok = secrets.compare_digest(candidate, pwd)
            except Exception:  # noqa: BLE001
                ok = False
        if not ok:
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Kairos"'},
            )
    return await call_next(request)


app.add_middleware(GZipMiddleware, minimum_size=600)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _warm_embeddings() -> None:
    """Précharge le modèle d'embeddings pour que la 1re recherche soit rapide."""
    try:
        from app.embeddings import embed_one
        embed_one("warmup")
    except Exception:  # noqa: BLE001 - un échec de warmup ne doit pas bloquer l'API
        pass


@app.on_event("startup")
def _init_db() -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(bind=engine)


app.include_router(webhook.router)
app.include_router(search.router)
app.include_router(media.router)
app.include_router(ingest.router)
app.include_router(stats.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    page = render_page("index.html")
    if page is None:
        return HTMLResponse("<h1>Kairos</h1><p>Frontend non monté.</p>")
    return HTMLResponse(page)


_frontend = Path(settings.frontend_dir)
if _frontend.is_dir():
    app.mount("/static", StaticFiles(directory=str(_frontend)), name="static")
