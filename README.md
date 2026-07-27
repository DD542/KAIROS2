# Kairos

**Couche de recherche sémantique multimodale** (audio + OCR) greffée au-dessus de
l'API **RTVC_Stockage**. Posez une question en langage naturel → Kairos renvoie
le **timestamp exact** dans la vidéo et lance la lecture (HLS RTVC) pile à cet
endroit.

*RTVC gère la vidéo (upload, transcodage/HLS, streaming, auth). Kairos gère
l'IA (Vosk + Tesseract + pgvector).* Rien n'est réécrit ; on se branche sur les
endpoints RTVC. 100 % souverain : audio et images ne quittent pas le serveur.

## Stack

| Rôle | Techno |
|------|--------|
| Backend API | FastAPI |
| Base vectorielle | PostgreSQL + pgvector |
| File d'attente | Celery + Redis |
| Transcription | **faster-whisper** (modèle `small`, FR, CPU int8) |
| OCR | Tesseract (FR) |
| Embeddings | sentence-transformers `all-MiniLM-L6-v2` (384 d) |
| Vidéo / HLS / stream | **délégué à RTVC** (`api.rtvc-media.com`) |
| Frontend | Video.js (HLS via stream-token RTVC) |

> **Transcription.** Kairos utilise **faster-whisper** (CTranslate2, int8) :
> précis sur le français réel, ponctuation/casse correctes, tout en local sur
> CPU. Modèle réglable via `WHISPER_MODEL` (`base`/`small`/`medium`). Le module
> `app/pipeline/transcribe.py` respecte le contrat `list[Segment]`.

## Démarrage

```bash
cp .env.example .env          # renseignez RTVC_USERNAME / RTVC_PASSWORD
docker compose up --build
```

Le 1er build télécharge le modèle Vosk FR (~45 Mo) + MiniLM. Ensuite :

- Frontend / recherche : http://localhost:8090/
- Swagger : http://localhost:8090/docs
- Health : http://localhost:8090/health

## Configuration (`.env`)

| Variable | Rôle |
|----------|------|
| `RTVC_BASE_URL` | base de l'API RTVC (défaut `https://api.rtvc-media.com`) |
| `RTVC_USERNAME` / `RTVC_PASSWORD` | identifiants OAuth2 pour `/auth/login` |
| `RTVC_OTP_CODE` | code 2FA optionnel |
| `HLS_POLL_INTERVAL` / `HLS_POLL_TIMEOUT` | attente de la fin du HLS RTVC |
| `WEBHOOK_SECRET` | si défini, le webhook exige l'en-tête `X-Webhook-Secret` |

## Workflow

1. **Upload** : via RTVC (`POST /documents/upload`) → `media_id`.
2. **Notifier Kairos** : `POST /webhook/rtvc-media-created {"media_id": 123}`
   (ou, pour tester, le bouton « Indexer » de l'UI / `POST /process/123`).
3. Le worker : vérifie/déclenche le **HLS RTVC**, télécharge la source, lance
   **Vosk** + **OCR**, génère les **embeddings**. Idempotent.
4. **Chercher** : `GET /search?q=...` → timestamps (audio + OCR mêlés).
5. **Lire** : clic → lecteur ouvert au timestamp, flux HLS via `stream-token` RTVC.

## API Kairos (voir `/docs`)

| Méthode | Endpoint | Rôle |
|---------|----------|------|
| POST | `/webhook/rtvc-media-created` | ingestion (RTVC → Kairos) |
| POST | `/process/{media_id}` | déclenchement manuel (test) |
| GET  | `/ingest/browse` | vidéos disponibles dans le dossier local |
| POST | `/ingest/local` | indexer un fichier local |
| GET  | `/media/{id}/video` | flux vidéo d'un média local (Range/seek) |
| GET  | `/media/{id}/segments` | transcription horodatée (sous-titres du lecteur) |
| GET  | `/media/{id}/thumbnail?t=` | vignette d'aperçu à un instant (cache disque) |
| GET  | `/media/{id}/transcript.{srt\|vtt\|txt}` | export sous-titres / texte |
| DELETE | `/media/{id}` | supprimer un média et ses données |
| POST | `/maintenance/cleanup` | supprimer les fichiers orphelins |
| POST | `/media/{id}/retry` | relancer une indexation échouée |
| GET  | `/stats` | compteurs d'exploitation (médias, index, durée moyenne) |
| GET  | `/search?q=&media_id=&limit=` | recherche sémantique hybride |
| GET  | `/media` | médias indexés + statut |
| GET  | `/media/{rtvc_id}/status` | statut d'indexation |
| GET  | `/video/{rtvc_id}/stream-token` | proxy playback RTVC (URL HLS) |
| GET  | `/video/{rtvc_id}` | lecteur Video.js |
| GET  | `/rtvc/nas-status` | dispo NAS via RTVC |

### Preuve de fonctionnement (curl)

```bash
# indexer le media RTVC 123
curl -X POST http://localhost:8090/process/123
# suivre le statut
curl http://localhost:8090/media/123/status
# chercher (une fois status=ready)
curl "http://localhost:8090/search?q=transform%C3%A9e%20de%20Fourier&media_id=123"
# -> renvoie start_seconds ; ouvrir http://localhost:8090/video/123?t=<start_seconds>
```

## Mode local (démo sans RTVC)

Kairos peut indexer un fichier vidéo posé sur le disque, sans passer par RTVC.
C'est la voie de démonstration : elle prouve toute la chaîne
**transcription → OCR → index → recherche → saut au timestamp**, même quand le
stockage RTVC est indisponible.

1. Indiquez le dossier contenant vos vidéos dans `.env` :
   ```
   MEDIA_INPUT_DIR=C:/Users/vous/Videos
   ```
2. `docker compose up --build`, puis ouvrez http://localhost:8090/
3. Section **« Indexer une vidéo »** : choisissez un fichier, réglez la durée à
   traiter (180 s suffit pour une démo), cliquez sur **Indexer**.
4. Une fois le statut `ready`, posez votre question dans la barre de recherche.

En ligne de commande :
```bash
curl -X POST http://localhost:8090/ingest/local \
  -H "Content-Type: application/json" \
  -d '{"path":"/media_in/ma-video.mp4","title":"Mon cours","max_seconds":180}'
```

> `max_seconds` limite la portion transcrite : Vosk tourne sur CPU, traiter
> 3 minutes prend quelques dizaines de secondes là où 2 h en prendraient bien
> plus. Les timestamps restent exacts sur la portion traitée.

## Déploiement (production)

Config production séparée (`docker-compose.prod.yml`) : redémarrage auto,
plusieurs workers API, pas de mode debug, secrets via `.env`.

```bash
cp .env.example .env      # renseigner POSTGRES_PASSWORD, RTVC_*, KAIROS_PORT
docker compose -f docker-compose.prod.yml up -d
```

Guide pas-à-pas pour le **NAS Synology (DS923+)** : [docs/DEPLOY-SYNOLOGY.md](docs/DEPLOY-SYNOLOGY.md)
(images, Container Manager, reverse proxy HTTPS, RAM ≥ 8 Go recommandée).

Guide **hébergement gratuit permanent (Oracle Cloud ARM, testeur à distance)** :
[docs/DEPLOY-ORACLE.md](docs/DEPLOY-ORACLE.md).

### Sécurité (production)

- **CORS fermé par défaut** : aucune origine tierce autorisée. N'ouvrir via
  `CORS_ORIGINS` (liste séparée par des virgules) que si une app externe doit
  appeler l'API — avec l'auth HTTP Basic, un `*` exposerait à du CSRF.
- **Anti-force brute** : 10 échecs d'authentification par IP → HTTP 429 pendant
  5 minutes (`X-Forwarded-For` pris en compte derrière un reverse proxy).
- **En-têtes** : `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`.

**Protection par mot de passe** : définir `KAIROS_PASSWORD` dans `.env`
(obligatoire dès que l'app est exposée sur Internet — le navigateur demande le
mot de passe une fois, `/health` reste ouvert pour la supervision).

## Diagnostic RTVC

Vérifie en une commande si l'API RTVC **et son NAS** sont opérationnels pour
l'indexation (login, nas/status, browse, bibliothèque, accès au contenu d'un
média, upload optionnel). Aucune dépendance (Python standard).

```bash
export RTVC_USERNAME=... RTVC_PASSWORD=...
python scripts/rtvc_diag.py --media-id 1
# options : --trigger-hls (déclenche le transcodage), --upload fichier.mp4 (test écriture NAS)
```

Verdict final : **NAS opérationnel** (Kairos peut indexer) ou **NAS ne sert pas
les octets** (à corriger côté RTVC avant traitement).

## Structure

```
backend/app/
  main.py              # FastAPI, routers, static
  config.py db.py      # settings (dont creds RTVC) + session
  models.py schemas.py # tables rtvc_id + DTO
  rtvc.py              # client RTVC (OAuth2, hls-status, signed-url, stream-token)
  embeddings.py search.py
  pipeline/            # transcode(local ffmpeg) / transcribe(Vosk) / ocr
  worker/              # celery_app + tasks.process_media (orchestration RTVC)
  routes/              # webhook, search, media (playback)
db/init.sql            # extension vector + tables (rtvc_id) + ivfflat
frontend/              # index, player (stream-token RTVC), app.js, player.js
docs/ARCHITECTURE.md
docker-compose.yml
```

## Performance

- **Index HNSW** (pgvector) : recherche vectorielle rapide et robuste à grande
  échelle — renvoie toujours des voisins (contrairement à ivfflat sur peu de
  données).
- **Torch CPU-only** : image backend nettement allégée (pas de libs CUDA).
- **Préchargement** du modèle d'embeddings au démarrage (API et worker) → 1re
  requête/indexation sans latence.
- **Compression gzip** des réponses ; fichiers statiques versionnés (cache).
- `min_score`, `API_WORKERS`, `WORKER_CONCURRENCY` réglables via `.env`.

## Dépannage (Windows / dev)

Si **Docker Desktop refuse de démarrer** (erreur sur `dockerInference` /
« Inference manager ») : désactiver Docker AI dans
`%APPDATA%\Docker\settings-store.json` (`"EnableDockerAI": false`) puis relancer.

## Notes / limites

- Réponses RTVC `hls-status` / `signed-url` / `stream-token` **non typées** dans
  l'OpenAPI → parsing défensif dans `app/rtvc.py`, à ajuster si besoin.
- Timestamps stockés en **ms** (entiers) ; l'API expose aussi `start_seconds`.
- Détection de scène (au lieu d'un intervalle fixe) et bascule Qdrant si le
  volume dépasse quelques milliers d'heures : pistes d'évolution.
