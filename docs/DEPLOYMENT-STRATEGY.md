# Stratégie de déploiement multi-plateforme

Ce document explique comment Kairos peut être déployé sur **plusieurs plateformes sans conflit**.

---

## 🎯 Principe fondamental

**Chaque plateforme a ses propres fichiers de configuration. AUCUN ne modifie les autres.**

```
Kairos/
├── docker-compose.yml              ← DEV LOCAL (docker compose up)
├── docker-compose.prod.yml         ← GCP (docker-compose -f docker-compose.prod.yml up)
├── Dockerfile                      ← FLY.IO (flyctl deploy)
├── fly.toml                        ← FLY.IO CONFIG (flyctl secrets set, flyctl deploy)
├── backend/
│   ├── Dockerfile                  ← BUILD BACKEND (utilisé par compose)
│   ├── app/
│   └── ...
└── docs/
    ├── DEPLOY-LOCAL.md
    ├── DEPLOY-GCP.md
    ├── DEPLOY-FLYIO.md
    ├── DEPLOY-SYNOLOGY.md
    └── DEPLOYMENT-STRATEGY.md ← TU ES ICI
```

---

## 📋 Détail de chaque fichier

### 1. **docker-compose.yml** — Dev local

**Utilisé par** : `docker-compose up`

**Contient** :
- ✅ Services : api, worker, db, redis
- ✅ Volumes locaux (bind-mount du code pour hot-reload)
- ✅ Ports exposés sur `localhost`
- ✅ Environment variables de dev

**Caractéristiques** :
- Hot-reload : oui (`--reload`)
- Bind-mounts : oui (`./backend:/app`)
- Idéal pour : développement itératif

**Modification** : **JAMAIS**

---

### 2. **docker-compose.prod.yml** — Production (multi-plateforme)

**Utilisé par** : `docker-compose -f docker-compose.prod.yml up` (GCP, Synology, Oracle, etc.)

**Contient** :
- ✅ Services : api, worker, db, redis (identiques à dev)
- ✅ Pas de bind-mounts (volumes persistants uniquement)
- ✅ Healthchecks
- ✅ restart: unless-stopped
- ✅ Pas de hot-reload

**Caractéristiques** :
- Hot-reload : non
- Bind-mounts : non (volumes persistants)
- Idéal pour : tout déploiement classique (GCP VM, Synology, etc.)

**Modification** : **JAMAIS**

---

### 3. **backend/Dockerfile** — Build backend

**Utilisé par** : `docker-compose` et `docker-compose.prod.yml` (directive `build: ./backend`)

**Contient** :
- ✅ Base image Python 3.11-slim
- ✅ FFmpeg, Tesseract, torch (CPU), Whisper, sentence-transformers
- ✅ Port 8000
- ✅ CMD uvicorn (API only, pas worker)

**Modification** : **SI BESOIN DE CHANGER LES DÉPENDANCES** (et tu dois aussi vérifier que Fly.io marche toujours)

---

### 4. **Dockerfile** (à la racine) — Fly.io build

**Utilisé par** : `flyctl deploy` (Fly.io uniquement)

**Contient** :
- ✅ Identique au `backend/Dockerfile`
- ✅ Mais copie depuis la racine (Dockerfile est à la racine pour Fly.io)
- ✅ COPY . . (copie le code entier, y compris backend/, frontend/, db/)

**Pourquoi duplicé ?** Fly.io s'attend à un `Dockerfile` à la racine. On ne peut pas le changer sans casser `docker-compose.yml`.

**Modification** : **SEULEMENT si tu modifies `backend/Dockerfile`** (mets à jour les deux identiquement)

---

### 5. **fly.toml** — Config Fly.io

**Utilisé par** : `flyctl launch`, `flyctl deploy`, `flyctl secrets set`

**Contient** :
- ✅ App name, région
- ✅ Build context, Dockerfile path
- ✅ Processus web (uvicorn)
- ✅ HTTP service config
- ✅ Health checks
- ✅ Volumes persistants
- ✅ Commentaires détaillés sur Fly.io (DB, Redis, workers, coûts)

**Modification** : **Au besoin** (changer région, machine type, etc.) — aucun impact sur autres plateforme

---

## 🔄 Cas d'usage courants

### Cas 1 : Développement local

```bash
cd kairos
docker-compose up
# Utilise : docker-compose.yml
# ✅ Pas d'impact sur GCP, Fly.io, Synology
```

### Cas 2 : Déployer sur GCP

```bash
# Sur la VM GCP :
cd ~/kairos
docker-compose -f docker-compose.prod.yml up -d --build
# Utilise : docker-compose.prod.yml + backend/Dockerfile
# ✅ Pas d'impact sur Fly.io, Synology
```

### Cas 3 : Déployer sur Fly.io

```bash
# Sur ton PC :
cd kairos
flyctl deploy
# Utilise : Dockerfile (racine) + fly.toml
# ✅ Pas d'impact sur GCP, Synology
```

### Cas 4 : Ajouter une dépendance (ex: une nouvelle lib Python)

1. Modifie `requirements.txt`
2. Test localement : `docker-compose up`
3. Mettre à jour `backend/Dockerfile` si besoin (très rare)
4. Si tu as modifié `backend/Dockerfile`, applique le même changement à `Dockerfile` (racine)
5. Redéploie où tu veux :
   - GCP : `docker-compose -f docker-compose.prod.yml up -d --build`
   - Fly.io : `flyctl deploy`

### Cas 5 : Changer la région Fly.io

Modifie `fly.toml` ligne `primary_region = "cdg"` → redéploie avec `flyctl deploy`.
**Aucun impact** sur docker-compose.

---

## 🛡️ Garanties

✅ **docker-compose.yml ne sera JAMAIS touché par les changements Fly.io**
✅ **docker-compose.prod.yml ne sera JAMAIS touché par les changements Fly.io**
✅ **backend/Dockerfile reste le source de vérité pour les dépendances**
✅ **Dockerfile (racine) = copie du backend/Dockerfile POUR FLY.IO UNIQUEMENT**
✅ **fly.toml est isolé à Fly.io (secrets, services, config Fly.io)**

---

## 📊 Matrice de déploiement

| Plateforme | Fichiers utilisés | Impact d'une modif | Outils |
|---|---|---|---|
| **Dev local** | `docker-compose.yml` | Redémarrer compose | `docker`, `docker-compose` |
| **GCP VM** | `docker-compose.prod.yml`, `backend/Dockerfile` | Rebuild image | `docker`, `docker-compose` |
| **Fly.io** | `Dockerfile`, `fly.toml` | Rebuild + redéployer | `flyctl` |
| **Synology NAS** | `docker-compose.prod.yml`, `backend/Dockerfile` | Rebuild image | `docker`, `docker-compose` |
| **Oracle** | `docker-compose.prod.yml`, `backend/Dockerfile` | Rebuild image | `docker`, `docker-compose` |
| **Tout autre Docker** | `docker-compose.prod.yml`, `backend/Dockerfile` | Rebuild image | `docker`, `docker-compose` |

---

## 🔧 Workflow recommandé

**Pour développer + tester sur plusieurs plateforme** :

```bash
# 1. Développement local
docker-compose up
# → Itère, teste, commit

# 2. Tester sur Fly.io (staging)
flyctl deploy
# → Vérifie que tout marche en "production-like"

# 3. Déployer sur GCP (prod)
ssh user@gcp-vm
docker-compose -f docker-compose.prod.yml up -d --build

# 4. (Optionnel) Synology pour backup/archivage
# Même commande que GCP
```

**Chaque plateforme reste isolée. Pas de conflit.**

---

## ⚠️ Pieges à éviter

❌ **NE PAS** :
- Modifier `docker-compose.yml` en pensant ça affectera Fly.io (non, c'est isolé)
- Modifier `fly.toml` en pensant ça affectera GCP (non, c'est isolé)
- Ajouter des secrets dans `fly.toml` (les secrets vont en `flyctl secrets`, pas dans le fichier)
- Modifier `Dockerfile` (racine) sans vérifier que `backend/Dockerfile` reste la source de vérité

✅ **FAIRE** :
- Garder `backend/Dockerfile` comme source de vérité
- Synchroniser `Dockerfile` (racine) quand tu modifies `backend/Dockerfile`
- Utiliser `flyctl secrets set` pour les secrets Fly.io, jamais `fly.toml`
- Tester sur dev local avant de déployer ailleurs

---

## 🎓 Résumé pour un nouveau contributeur

> « Kairos peut se déployer sur plusieurs plateforme. Chaque plateforme a ses propres fichiers de config (docker-compose.yml pour dev, docker-compose.prod.yml pour GCP/Synology, fly.toml pour Fly.io). Ils ne s'interfèrent pas. Modifie toujours `requirements.txt` et `backend/Dockerfile` en priorité ; si tu touches `backend/Dockerfile`, applique la même modif à `Dockerfile` (racine). Pour déployer, utilise l'outil approprié : compose local, compose prod, ou flyctl selon la plateforme. »

---

## 📞 Questions fréquentes

**Q: Pourquoi un Dockerfile à la racine ET un dans backend/?**
R: Fly.io exige un Dockerfile à la racine. docker-compose utilise celui de backend/. Les deux doivent rester synchronisés, mais c'est le prix pour supporter Fly.io sans casser docker-compose.

**Q: Si je modifie requirements.txt, dois-je ré-pusher partout?**
R: requirements.txt est copiée par le Dockerfile. À la prochaine build (compose, flyctl deploy, etc.), elle sera intégrée. Pas besoin de push manuellement.

**Q: Docker-compose.prod.yml est différent de docker-compose.yml?**
R: Oui, prod retire les bind-mounts, hot-reload, et ajoute healthchecks + restart. C'est voulu pour la production. Dev local a besoin du hot-reload, prod non.

**Q: Fly.io supporte docker-compose nativement?**
R: Non, Fly.io utilise un seul Dockerfile. Si tu veux plusieurs services (workers, etc.), tu dois les deployer comme apps séparées.

---

## 📖 Documentation associée

- [DEPLOY-LOCAL.md](DEPLOY-LOCAL.md) — Dev local
- [DEPLOY-GCP.md](DEPLOY-GCP.md) — Google Cloud
- [DEPLOY-FLYIO.md](DEPLOY-FLYIO.md) — Fly.io
- [DEPLOY-SYNOLOGY.md](DEPLOY-SYNOLOGY.md) — Synology NAS
- [DEPLOY-ORACLE.md](DEPLOY-ORACLE.md) — Oracle Cloud
