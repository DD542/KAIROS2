# Dossier technique — Kairos

**Recherche sémantique multimodale sur vidéo, greffée sur l'API RTVC.**
Document de synthèse compilant l'ensemble des fichiers techniques du projet.

- Dépôt Git : 17 commits, dernier `178304a`
- Code : ~3 200 lignes (backend Python + frontend JS/CSS)
- Stack : FastAPI · PostgreSQL/pgvector · Celery/Redis · faster-whisper · Tesseract · Docker

---

## 1. Architecture générale

Kairos est une **couche IA** posée sur la plateforme vidéo existante **RTVC**
(`api.rtvc-media.com`). Principe : *RTVC gère la vidéo (stockage, upload,
streaming), Kairos gère l'intelligence (transcription, OCR, recherche)*.

```
                          ┌──────────────────────────────┐
        upload / stream   │   RTVC_Stockage API           │
        auth / NAS    ───▶│   (NAS Synology + MinIO)       │
                          └──────┬─────────────▲───────────┘
   nas/browse, nas/download      │             │  stream-token (lecture)
                          ┌──────▼─────────────┴───────────┐
                          │   Kairos backend (FastAPI)      │
                          │   /search  /media  /ingest       │
                          └──────┬───────────────┬──────────┘
                    Celery/Redis │               │ SQLAlchemy
                          ┌──────▼──────┐  ┌──────▼──────────────────┐
                          │  Worker      │  │  PostgreSQL + pgvector   │
                          │  Whisper+OCR │  │  processed_media /       │
                          │  +embeddings │  │  transcriptions / ocr /  │
                          └──────────────┘  │  embeddings (HNSW)       │
                                            └──────────────────────────┘
   Frontend (Video.js) ── /search ─▶ Kairos ── /media/{id}/video ─▶ lecture
```

Workflow : **Ingestion** (RTVC ou fichier local) → **Transcription**
(faster-whisper, multilingue) → **OCR** (Tesseract, texte à l'écran) →
**Vecteurs** (embeddings multilingues, pgvector/HNSW) → **Recherche
sémantique** → **Lecture au timestamp exact**.

Documents associés : [ARCHITECTURE.md](ARCHITECTURE.md) (détail des flux et du
contrat API RTVC), [DEPLOY-SYNOLOGY.md](DEPLOY-SYNOLOGY.md) et
[DEPLOY-ORACLE.md](DEPLOY-ORACLE.md) (déploiement).

---

## 2. Infrastructure & configuration

### `docker-compose.yml` — environnement de développement
Quatre services : `db` (PostgreSQL+pgvector), `redis` (file d'attente),
`backend` (API FastAPI, rechargement à chaud), `worker` (Celery, traitement
IA). Port 5000 exposé. Volume `media_in` pour les vidéos locales.

### `docker-compose.prod.yml` — environnement de production
Différences : pas de bind-mount du code (image figée), `restart:
unless-stopped`, plusieurs workers uvicorn, healthcheck HTTP, secrets
obligatoires via `.env` (`POSTGRES_PASSWORD` sans défaut).

### `backend/Dockerfile`
Image Python 3.11 slim + FFmpeg + Tesseract (FR) + torch **CPU-only**
(évite les libs CUDA inutiles : image passée de 11,6 Go à 1,63 Go) +
faster-whisper + modèle d'embeddings multilingue, tous **pré-téléchargés**
dans l'image (fonctionnement hors-ligne, pas de latence au démarrage).

### `.env.example`
Modèle des variables : identifiants RTVC, `KAIROS_PASSWORD` (protection
d'accès), `CORS_ORIGINS` (sécurité), réglages Whisper, ports.

### `db/init.sql`
Schéma PostgreSQL : 4 tables (`processed_media`, `transcriptions`,
`ocr_texts`, `embeddings`) + extension `pgvector` + **index HNSW** sur les
embeddings (recherche vectorielle rapide, contrairement à `ivfflat` qui peut
renvoyer zéro résultat sur peu de données).

---

## 3. Backend — cœur applicatif

### `backend/app/main.py`
Point d'entrée FastAPI. Contient la **sécurité** :
- middleware d'authentification HTTP Basic (`KAIROS_PASSWORD`)
- **anti-force-brute** : 10 échecs par IP → blocage 429 pendant 5 min
- **CORS fermé par défaut** (une ouverture `*` combinée à l'auth Basic
  exposerait à une attaque CSRF)
- en-têtes de sécurité (`X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`)
- préchargement du modèle d'embeddings au démarrage (latence nulle au 1er
  appel)

### `backend/app/config.py`
Centralise tous les réglages : identifiants RTVC, modèle Whisper
(`whisper_model`, `whisper_language` — vide = détection automatique),
modèle d'embeddings, seuils de recherche, sécurité.

### `backend/app/models.py`
Tables SQLAlchemy. `ProcessedMedia` (registre d'idempotence : statut, langue
détectée, chemin), `Transcription`, `OcrText`, `Embedding` (vecteur 384
dimensions, index HNSW).

### `backend/app/rtvc.py`
Client de l'API RTVC. Points clés :
- authentification OAuth2 avec **rafraîchissement proactif du jeton**
  (décodage du JWT pour anticiper l'expiration, plutôt que réagir à un 401)
- `download_nas_file()` : la route qui fonctionne réellement en production
  (`/nas/download`), avec repli automatique en téléchargement complet si
  une portion tronquée échoue au transcodage
- `list_videos_recursive()` : parcours récursif du NAS pour l'indexation en
  masse
- **cache court (60 s)** des dossiers explorés, pour masquer les pannes
  passagères (5xx) du serveur RTVC

### `backend/app/search.py`
Moteur de recherche sémantique : distance cosinus sur les vecteurs,
**diversité** (maximum 3 résultats par vidéo) et **dédoublonnage** (fusion
des passages du même média à moins de 12 secondes d'écart).

### `backend/app/embeddings.py`
Génère les vecteurs de sens avec `sentence-transformers` (modèle
multilingue — voir §6).

### `backend/app/pages.py`
Sert les pages HTML avec **versionnage automatique** des fichiers CSS/JS
(`?v=<date de modification>`), pour que le navigateur ne serve jamais une
version périmée en cache.

---

## 4. Pipeline de traitement IA (`backend/app/pipeline/`)

| Fichier | Fonction |
|---|---|
| `transcode.py` | FFmpeg : extraction audio (WAV 16 kHz), keyframes, copie de lecture MP4, **vignettes d'aperçu** (génération à la demande, cache disque) |
| `transcribe.py` | Transcription **faster-whisper** (modèle `small`, quantifié int8, CPU) ; **détection automatique de la langue** ; regroupement en passages cohérents (~8 s) |
| `ocr.py` | Lecture du texte affiché à l'écran via Tesseract, avec déduplication des slides répétées |

---

## 5. Orchestration asynchrone (`backend/app/worker/`)

### `tasks.py` — le cœur du pipeline
Fonction partagée `_index_media()` utilisée par toutes les voies
d'ingestion (RTVC, locale) : transcription → OCR (non bloquant : un échec
OCR n'empêche pas la transcription d'être indexée) → génération des
vecteurs. **Idempotent** : chaque étape vérifie si elle a déjà été faite.

Tâches Celery : `process_rtvc_nas`, `process_local`, `index_all_rtvc`
(scan récursif + mise en file en masse), avec **reprise automatique**
(3 tentatives, délai croissant) en cas d'échec transitoire.

### `celery_app.py`
Configuration de la file d'attente Redis, concurrence limitée à 1
(Whisper est déjà multi-thread), préchargement du modèle dans chaque worker.

---

## 6. Choix techniques justifiés

| Besoin | Choix | Pourquoi |
|---|---|---|
| Transcription | **faster-whisper** (`small`, int8, CPU) | Remplace Vosk : ponctuation, casse, chiffres corrects ; local et souverain |
| Langue transcription | **détection automatique** | Une vidéo en anglais/espagnol est transcrite correctement (avant : forcée en FR) |
| Embeddings | `paraphrase-multilingual-MiniLM-L12-v2` (384d) | **Recherche cross-langue vérifiée** : question en italien/espagnol/allemand/anglais/portugais/néerlandais → 77-84 % sur du contenu français. Limite connue : alphabets non-latins (arabe, chinois, russe) peu fiables avec ce modèle compact — un modèle plus lourd (`multilingual-e5-large`) serait nécessaire |
| Base vectorielle | PostgreSQL + pgvector (HNSW) | Pas de service supplémentaire ; HNSW toujours des résultats (vs ivfflat) |
| Récupération vidéo RTVC | `/nas/download` (chemin fichier) | Seule route qui fonctionne réellement — `signed-url` et `/stream` échouent en production |
| Sécurité | Mot de passe unique + CORS fermé + anti-brute-force | Adapté à un déploiement mono-client ; comptes multi-utilisateurs identifiés comme évolution future |

---

## 7. API exposée (voir aussi `/docs` — Swagger interactif)

| Méthode | Endpoint | Rôle |
|---|---|---|
| GET | `/search` | Recherche sémantique (paramètres : `q`, `media_id`, `limit`) |
| GET | `/media` | Liste de la bibliothèque |
| GET | `/media/{id}/video` | Flux vidéo (Range/seek) |
| GET | `/media/{id}/segments` | Transcription horodatée (sous-titres) |
| GET | `/media/{id}/thumbnail?t=` | Vignette d'aperçu à un instant |
| GET | `/media/{id}/transcript.{srt\|vtt\|txt}` | Export sous-titres/texte |
| DELETE | `/media/{id}` | Suppression (cascade complète) |
| POST | `/media/{id}/retry`, `/media/retry-failed` | Relance après échec |
| POST | `/ingest/rtvc`, `/ingest/local` | Indexer une vidéo |
| POST | `/ingest/rtvc/index-all` | Indexation en masse (dossier récursif) |
| GET | `/ingest/rtvc/browse` | Explorateur du NAS RTVC |
| POST | `/webhook/rtvc-media-created` | Notification automatique (RTVC → Kairos) |
| GET | `/stats` | Statistiques d'exploitation + espace disque |
| POST | `/maintenance/cleanup` | Nettoyage des fichiers orphelins |

---

## 8. Frontend

| Fichier | Rôle |
|---|---|
| `index.html` / `app.js` | Recherche (avec filtre Parlé/À l'écran, surlignage des termes, vignettes), bibliothèque (filtre, suppression, relance), explorateurs RTVC/local |
| `player.html` / `player.js` | Lecteur avec saut au timestamp, sous-titres activables, export, copie de lien horodaté |
| `style.css` | Thème clair/sombre automatique, responsive mobile |

Navigation : la recherche vit dans l'URL (`?q=...`) — le bouton retour du
navigateur ramène aux résultats après consultation d'une vidéo.

---

## 9. Outils & tests

- **`scripts/rtvc_diag.py`** — diagnostic autonome de l'API RTVC (auth, NAS,
  accès aux fichiers), verdict clair en une commande
- **`scripts/tunnel.ps1`** — expose Kairos publiquement via Cloudflare
  Tunnel (démonstration à distance)
- **`backend/tests/test_rtvc.py`** — tests unitaires sur la logique pure du
  client RTVC (normalisation des statuts, extraction de champs, décodage JWT)

---

## 10. Sécurité — récapitulatif

- Authentification par mot de passe (HTTP Basic), obligatoire dès exposition
  sur Internet
- Anti-force-brute (429 après 10 échecs/IP)
- CORS fermé par défaut (protection CSRF)
- En-têtes de sécurité standards
- Secrets exclus du dépôt Git (`.env` dans `.gitignore`)
- Aucune donnée envoyée à un cloud tiers : transcription et OCR **100 %
  locaux**

---

## 11. Limites connues & pistes d'évolution

- **Comptes multi-utilisateurs** : non implémenté (mot de passe unique
  aujourd'hui) — nécessaire pour un vrai SaaS multi-clients
- **Alphabets non-latins** en recherche : fiabilité faible avec le modèle
  d'embeddings actuel
- **Résumés/chapitres automatiques**, **diarisation** (qui parle) : pistes
  identifiées, nécessitent un LLM local ou WhisperX
- **CI/CD, Prometheus/Grafana, Sentry** : observabilité de base en place
  (`/stats`, logs), outils dédiés non déployés

---

*Document généré à partir de l'état du dépôt au commit `178304a`.*
