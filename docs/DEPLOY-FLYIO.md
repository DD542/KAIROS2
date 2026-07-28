# Déployer Kairos sur Fly.io

**Durée : 20-30 min | Coût : gratuit (Free tier) ou ~$12-24/mo (prod)**

Fly.io offre une alternative à Google Cloud : plus simple, moins de config, avec des services managés (Postgres, Redis). Cette approche déploie uniquement l'**API** ; les workers Celery (traitement vidéo) peuvent tourner séparément.

> 📌 **Important** : Ce guide n'affecte **JAMAIS** les déploiements via `docker-compose.yml` (dev local) ou GCP. Les fichiers `Dockerfile` et `fly.toml` sont **additionnels uniquement**.

---

## Prérequis

- ✅ Compte Fly.io (gratuit) : https://fly.io/
- ✅ CLI flyctl installée : https://fly.io/docs/hands-on/install-flyctl/
- ✅ Code Kairos sur ton PC
- ✅ Credentials RTVC (optionnel pour une démo, obligatoire pour RTVC intégration)

---

## Étape 1 — Préparer ton compte Fly.io

### 1.1 — Créer un compte

```bash
flyctl auth signup
# ou
flyctl auth login
```

### 1.2 — Vérifier l'installation

```bash
flyctl version
# -> Fly CLI v0.X.XX
```

---

## Étape 2 — Initialiser l'app Fly.io

### 2.1 — Dans le dossier Kairos

```bash
cd C:\Users\menga\PycharmProjects\kairos
flyctl launch
```

La CLI te posera des questions :

```
? App Name: kairos
? Select region: cdg (Paris) ou lfr (Lyon)
? Would you like to use Dockerfile?: Yes
? Would you like to set up PostgreSQL?: Yes
? PostgreSQL Machine type?: shared-cpu-1x
? Would you like to set up Redis?: Yes
? Redis type?: shared
```

Appuie sur **Enter** pour accepter les suggestions.

### 2.2 — Vérifier

Un nouveau **`fly.toml`** devrait être créé (celui qu'on a déjà fourni remplace celui-ci si nécessaire).

---

## Étape 3 — Configurer les secrets (credentials)

Les secrets Fly.io ne s'écrivent **JAMAIS** dans `fly.toml`. Utilise la CLI :

```bash
# Mot de passe PostgreSQL
flyctl secrets set POSTGRES_PASSWORD="un-mot-de-passe-tres-fort-123!"

# RTVC credentials (optionnel)
flyctl secrets set RTVC_USERNAME="Rtvc2026"
flyctl secrets set RTVC_PASSWORD="ton-mdp-rtvc"

# Mot de passe d'accès Kairos (pour la démo)
flyctl secrets set KAIROS_PASSWORD="KairosDemo2026"

# Webhook secret (optionnel)
flyctl secrets set WEBHOOK_SECRET="random-secret-string"
```

Vérification :

```bash
flyctl secrets list
```

---

## Étape 4 — Préparer la base de données

### 4.1 — Copier le schéma

Fly.io a créé une base Postgres. Tu dois initialiser le schéma (tables, indexes) :

```bash
# Connexion SSH à la machine API
flyctl ssh console -s
```

Tu es maintenant dans la machine Fly.io. Exécute :

```bash
# Dans la console SSH :
cd /app
python -c "from app.db import init_db; init_db()"
```

Ou envoie le fichier `init.sql` directement :

```bash
# Depuis ton PC (avant le SSH) :
flyctl ssh sftp get
# Puis upload db/init.sql vers la machine
```

Sinon, depuis la console SSH :

```bash
psql $DATABASE_URL < /app/db/init.sql
```

---

## Étape 5 — Construire et déployer

### 5.1 — Build initial

```bash
flyctl build
```

⏳ Attend 5-10 min (télécharge torch, Whisper, les modèles).

### 5.2 — Déployer

```bash
flyctl deploy
```

⏳ Attend ~2-3 min.

### 5.3 — Vérifier

```bash
flyctl status
```

Devrait afficher :

```
App
  Name     = kairos
  Owner    = personal
  Version  = 1
  Status   = running
  Hostname = kairos.fly.dev

...
Instances
ID       PROCESS  VERSION  STATUS     CHECKS                      UPTIME
abc123   web      1        running    1 total, 1 passing          2m
```

### 5.4 — Tester l'API

```bash
flyctl open /health
# Ou :
curl https://kairos.fly.dev/health
# -> {"status":"ok"}
```

✅ **Kairos tourne !**

---

## Étape 6 — Accès et premiers tests

### 6.1 — URL publique

```bash
flyctl open
# Ouvre https://kairos.fly.dev dans le navigateur
```

### 6.2 — Authentification

La page te demande le mot de passe Kairos (celui que tu as defini en secrets).

### 6.3 — Logs en direct

```bash
flyctl logs
```

Pour voir les erreurs ou les requêtes en temps réel.

---

## Étape 7 — Ajouter les workers Celery (optionnel)

Par défaut, seule l'**API** tourne sur Fly.io. Pour que la **transcription vidéo** fonctionne, tu dois ajouter un worker.

### Option A — Déployer un worker Celery séparé

```bash
# Créer une nouvelle app pour le worker
flyctl apps create kairos-worker

# Copier la config (et modifier le fly.toml pour le worker)
cd C:\Users\menga\PycharmProjects\kairos
flyctl launch --app kairos-worker

# Modifier fly.toml pour le worker :
# [processes]
# worker = "celery -A app.worker.celery_app worker --loglevel=info --concurrency=1"

# Puis déployer :
flyctl deploy --app kairos-worker
```

### Option B — Accepter une démo sans workers

Pour la présentation/démo initiale, tu peux accepter que **l'upload fonctionne** mais **le traitement soit asynchrone** :
- User upload une vidéo → elle apparaît immédiatement dans la liste
- En arrière-plan, un job attendra un worker (ou une intervention manuelle)

Cela simplifie le déploiement initial.

---

## Déploiements suivants (mises à jour)

Après chaque changement au code :

```bash
cd C:\Users\menga\PycharmProjects\kairos
git add -A
git commit -m "..."
flyctl deploy
```

Fly.io utilisera le cache de build → plus rapide (~30-60 s).

---

## Logs et monitoring

### Voir les logs en direct

```bash
flyctl logs
```

### Logs d'une machine spécifique

```bash
flyctl logs -i abc123  # remplace abc123 par l'ID de la machine
```

### Historique des déploiements

```bash
flyctl releases
```

---

## Problèmes courants

| Problème | Solution |
|---|---|
| **Build échoue sur Whisper** | Normal, ça prend 2-3 min. Relance `flyctl deploy`. |
| **502 Bad Gateway** | L'app crash. Vérifie `flyctl logs`. Probablement DB pas initialisée ou secret manquant. |
| **DATABASE_URL non défini** | Fly.io injecte ce secret auto. Sinon, ajoute-le manuellement. |
| **Postgres connection refused** | La DB n'est pas attachée à l'app. Relance `flyctl launch` ou `flyctl postgres attach kairos-db`. |
| **"Cannot find module app"** | Le chemin COPY dans le Dockerfile est mal défini. Vérifier qu'il copie `backend/app` correctement. |
| **Timeout sur `/health`** | L'app met trop de temps à démarrer (chargement modèles). Augmente le `grace_period` dans `fly.toml`. |

---

## Comparaison : Dev Local vs GCP vs Fly.io

| Aspect | Dev Local | GCP | Fly.io |
|---|---|---|---|
| **Outil** | `docker compose up` | `docker-compose.prod.yml` | `flyctl deploy` |
| **Coût** | 0 € (local) | 0 € (gratuit 90j) puis ~$20/mo | 0 € (gratuit) ou ~$12-24/mo |
| **Postgres** | Containerisée (compose) | Cloud Postgres | Fly Postgres |
| **Redis** | Containerisée (compose) | Cloud Memorystore | Fly Redis |
| **Celery workers** | Oui (compose) | Oui (compose) | Séparé (autre app) |
| **Hot reload** | Oui (`--reload`) | Non | Non |
| **Configuratio** | Simple | Moyen (firewall, etc) | Simple |
| **Domaine** | localhost:5000 | IP externe | kairos.fly.dev |
| **Scaling** | Manuel | Via Compute Engine | Auto |

---

## ⚠️ Rappels importants

✅ **Ces fichiers ne cassent RIEN d'existant** :
- `docker-compose.yml` : **100% inchangé** (dev local)
- `docker-compose.prod.yml` : **100% inchangé** (GCP)
- `backend/Dockerfile` : **100% inchangé** (build backend)

✅ **Nouveaux fichiers additionnels** (Fly.io uniquement) :
- `Dockerfile` (à la racine)
- `fly.toml`

Donc tu peux :
- Développer localement : `docker-compose up`
- Tester GCP : `docker-compose -f docker-compose.prod.yml up`
- Déployer Fly.io : `flyctl deploy`

**Aucun conflit, aucune modification.**

---

## Prochaines étapes

1. ✅ Créer le compte Fly.io + runCLI setup
2. ✅ `flyctl launch` dans le dossier Kairos
3. ✅ Ajouter les secrets (POSTGRES_PASSWORD, RTVC_*, etc.)
4. ✅ Initialiser la DB (schéma + tables)
5. ✅ `flyctl deploy`
6. ✅ Tester `https://kairos.fly.dev`
7. (Optionnel) Ajouter un worker Celery pour la transcription

Bon déploiement ! 🚀
