# Kairos — Architecture (V2, intégration RTVC)

Kairos est une **couche de recherche sémantique** greffée au-dessus de l'API
**RTVC_Stockage** (`https://api.rtvc-media.com`). RTVC gère l'upload, le
transcodage/HLS, le streaming et l'auth. Kairos n'ajoute que : **transcription
(Vosk)**, **OCR (Tesseract)** et **indexation vectorielle (pgvector)**.

> Principe : *RTVC gère la vidéo, Kairos gère l'IA.* On ne réécrit ni le
> stockage, ni le streaming, ni l'upload.

## Vue d'ensemble

```
                          ┌──────────────────────────────┐
        upload / HLS /    │   RTVC_Stockage API           │
        stream / auth ───▶│   (NAS Synology + MinIO)       │
                          └──────┬─────────────▲───────────┘
   generate-hls / hls-status     │             │  stream-token (playback)
   signed-url (raw download)     │             │
                          ┌──────▼─────────────┴───────────┐
                          │   Kairos backend (FastAPI)      │
                          │   POST /webhook/rtvc-media-...   │
                          │   GET  /search                   │
                          │   GET  /video/{id}/stream-token  │
                          └──────┬───────────────┬──────────┘
                    Celery/Redis │               │ SQLAlchemy
                          ┌──────▼──────┐  ┌──────▼──────────────────┐
                          │  Worker      │  │  PostgreSQL + pgvector   │
                          │  Vosk + OCR  │  │  processed_media /       │
                          │  + embeddings│  │  transcriptions /        │
                          └──────────────┘  │  ocr_texts / embeddings  │
                                            └──────────────────────────┘
   Frontend Video.js ── /search ─▶ Kairos ── /video/{id}/stream-token ─▶ RTVC HLS
```

## Workflow (upload → recherche)

0. **Upload** : géré par RTVC (`POST /documents/upload`) → `media_id`.
1. **Notification** : RTVC appelle `POST /webhook/rtvc-media-created {media_id}`
   (ou déclenchement manuel `POST /process/{media_id}`). Kairos enfile une tâche.
2. **HLS (Step 3, clé de voûte)** : le worker appelle
   `GET /media/{media_id}/hls-status` ; si `pending`, `POST
   /documents/{media_id}/generate-hls`, puis polling jusqu'à `ready`.
3. **Récupération source** : `GET /media/{media_id}/signed-url` (lien MinIO
   direct ; fallback `GET /documents/{media_id}/stream`) → téléchargement local.
4. **Traitement IA (idempotent)** :
   - FFmpeg local extrait l'audio (WAV 16 kHz) → **Vosk** → `transcriptions`.
   - FFmpeg local extrait des keyframes → **Tesseract** → `ocr_texts`.
   - **MiniLM** (384 d) sur segments audio + textes OCR → `embeddings` (pgvector).
5. **Recherche** : `GET /search?q=...&media_id=...` → embedding requête →
   distance cosinus pgvector → liste de timestamps triés.
6. **Lecture** : le frontend appelle `GET /video/{id}/stream-token` (proxy de
   `GET /documents/{media_id}/stream-token`) → Video.js charge le master HLS et
   fait `currentTime(t)` au timestamp du résultat.

## Idempotence

`processed_media(rtvc_id)` est le registre. Chaque sous-étape vérifie ses propres
lignes (`transcriptions`, `ocr_texts`, `embeddings`) pour ce `rtvc_id` avant de
s'exécuter → une re-livraison du webhook ne double rien et reprend là où ça s'est
arrêté.

## Données (Kairos ne stocke que l'IA)

| Table            | Rôle |
|------------------|------|
| `processed_media`| registre d'idempotence : statut, hls_ready, processed_at |
| `transcriptions` | segments audio Vosk (`rtvc_id`, start/end ms, text) |
| `ocr_texts`      | textes à l'écran Tesseract (`rtvc_id`, timestamp_ms, text) |
| `embeddings`     | vecteurs 384 d, `source ∈ {audio, visual}`, `rtvc_id`, ts |

Clé partout : `rtvc_id` = `media_id` RTVC (entier). Le titre/métadonnées vivent
dans RTVC (récupérables via `/documents/library`).

## Contrat RTVC utilisé (résolu depuis `/openapi.json`)

| Besoin | Endpoint RTVC | Auth |
|--------|---------------|------|
| Login (OAuth2 password) | `POST /auth/login` (form) → `access_token` | — |
| Déclencher HLS | `POST /documents/{media_id}/generate-hls` | Bearer |
| Statut HLS | `GET /media/{media_id}/hls-status` | — |
| Lien fichier brut | `GET /media/{media_id}/signed-url` | Bearer |
| Flux brut (fallback) | `GET /documents/{media_id}/stream` (Range) | token |
| URL de lecture | `GET /documents/{media_id}/stream-token` | Bearer |
| Dispo NAS | `GET /nas/status` | Bearer |

> Les réponses de `hls-status`, `signed-url`, `stream-token` sont **non typées**
> dans l'OpenAPI. `app/rtvc.py` extrait les champs de façon défensive (plusieurs
> noms possibles) et journalise le payload brut : à ajuster si l'API réelle
> nomme différemment (`status`, `url`, `token`…).

## Sécurité & souveraineté

- Audio et images **ne quittent jamais** le serveur de traitement Kairos.
- Aucune API cloud tierce : uniquement Vosk + Tesseract en local.
- La sécurité d'accès au flux repose sur les **tokens RTVC** (`stream-token`),
  pas sur un mécanisme Kairos maison.
- Webhook protégeable par secret partagé (`X-Webhook-Secret`).

## Écart Vosk / Whisper

MVP local en **Vosk** (CPU, léger, souverain). Cible production **WhisperX**
(alignement ~100 ms) : réimplémenter `app/pipeline/transcribe.py` (même contrat
`list[Segment]`), le reste ne change pas.
