"""Kairos backend — FastAPI application (V2).

Kairos is a semantic-search layer on top of the RTVC_Stockage API. It exposes:
  - POST /webhook/rtvc-media-created  (ingest / index a media)
  - GET  /search                      (hybrid audio+OCR semantic search)
plus playback helpers that proxy RTVC's signed stream-token, and the demo UI.
"""

from __future__ import annotations

import base64
import secrets
import time
from collections import defaultdict
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app import migrate
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

# Anti-force brute : au-delà de N échecs sur la fenêtre, l'IP est temporisée.
# Stockage en mémoire (suffisant pour une instance ; passer à Redis si plusieurs).
_MAX_FAILS = 10
_BAN_SECONDS = 300
_fails: dict[str, list[float]] = defaultdict(list)
_fails_lock = Lock()

# Le comptage doit être PARTAGÉ : avec API_WORKERS=2, un compteur par process
# double silencieusement le nombre d'essais tolérés, et le plafond ne veut plus
# rien dire. Redis est déjà là pour Celery ; on s'en sert. S'il est
# indisponible, on retombe sur le compteur mémoire — dégradé mais jamais
# bloquant pour l'authentification.
try:
    import redis as _redis_lib

    _redis = _redis_lib.Redis.from_url(settings.celery_broker_url, socket_timeout=0.5)
    _redis.ping()
except Exception:  # noqa: BLE001
    _redis = None


def _client_ip(request: Request) -> str:
    # derrière un reverse proxy (DSM, Cloudflare), l'IP réelle est dans XFF
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _too_many_fails(ip: str) -> bool:
    if _redis is not None:
        try:
            return int(_redis.get(f"kairos:fails:{ip}") or 0) >= _MAX_FAILS
        except Exception:  # noqa: BLE001 - Redis tombé : on continue en mémoire
            pass
    now = time.time()
    with _fails_lock:
        recent = [t for t in _fails[ip] if now - t < _BAN_SECONDS]
        _fails[ip] = recent
        return len(recent) >= _MAX_FAILS


def _record_fail(ip: str) -> None:
    if _redis is not None:
        try:
            key = f"kairos:fails:{ip}"
            # La fenêtre repart à chaque échec : un attaquant qui insiste reste
            # bloqué, un utilisateur qui s'est trompé est libéré au bout de 5 min.
            pipe = _redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, _BAN_SECONDS)
            pipe.execute()
            return
        except Exception:  # noqa: BLE001
            pass
    with _fails_lock:
        _fails[ip].append(time.time())


@app.middleware("http")
async def _password_gate(request: Request, call_next):
    pwd = settings.kairos_password
    if pwd and request.url.path not in _AUTH_EXEMPT:
        ip = _client_ip(request)
        if _too_many_fails(ip):
            return Response(status_code=429, content="Trop de tentatives. Réessayez plus tard.")

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
            _record_fail(ip)
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Kairos"'},
            )
    response = await call_next(request)
    # En-têtes de sécurité (défense en profondeur)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


app.add_middleware(GZipMiddleware, minimum_size=600)

# CORS : par défaut AUCUNE origine externe autorisée. Avec l'authentification
# HTTP Basic (que le navigateur rejoue automatiquement), un "*" permettrait à
# n'importe quel site de piloter Kairos au nom de l'utilisateur connecté (CSRF).
# Renseigner CORS_ORIGINS uniquement si une app tierce doit appeler l'API.
_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
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
    # Colonnes et index ajoutés après coup : create_all ne les poserait pas sur
    # une base déjà remplie.
    migrate.run(engine)


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
