# Déployer Kairos sur le NAS Synology (DS923+)

Kairos tourne à côté de RTVC, sur le même NAS — cohérent avec l'architecture
souveraine. Le DS923+ est en x86_64, donc les images Docker fonctionnent tel
quel.

## 0. Prérequis & point de vigilance RAM

| Élément | Recommandé |
|---|---|
| DSM | 7.2+ avec **Container Manager** installé |
| **RAM** | **≥ 8 Go** (le DS923+ est livré avec 4 Go → **ajouter une barrette**) |
| Espace disque | ~6 Go pour les images + l'espace des vidéos traitées |
| Accès | SSH activé (Panneau de configuration → Terminal & SNMP) |

> ⚠️ **La RAM est le vrai facteur limitant.** La transcription charge un modèle
> d'IA en mémoire. Avec 4 Go, PostgreSQL + le worker risquent de saturer. Sur
> 8 Go+, tout est confortable. On garde `WORKER_CONCURRENCY=1` pour maîtriser la
> mémoire.

## 1. Récupérer le projet sur le NAS

Deux options.

**A. Via Git (le plus simple si le NAS a accès Internet)**
```bash
# en SSH sur le NAS
cd /volume1/docker
git clone <url-de-votre-depot> kairos   # ou copier le dossier via File Station
cd kairos
```

**B. Copier le dossier** avec File Station / un lecteur réseau, vers
`/volume1/docker/kairos`.

## 2. Fournir les images (le point clé)

Construire l'image sur le NAS est possible mais lent. **Le plus fiable : la
construire sur votre PC, l'exporter, l'importer sur le NAS.**

Sur votre PC (où l'image est déjà construite) :
```bash
docker save kairos-backend | gzip > kairos-backend.tar.gz
```
Copiez `kairos-backend.tar.gz` sur le NAS (File Station), puis en SSH :
```bash
cd /volume1/docker/kairos
gunzip -c kairos-backend.tar.gz | docker load
```

> L'image `backend` sert aussi au `worker` (même image, commande différente).
> Grâce à torch CPU-only, elle est bien plus légère qu'avant.

*Alternative : `docker compose -f docker-compose.prod.yml build` directement sur
le NAS si Internet + disque le permettent (compter 15–20 min).*

## 3. Configurer `.env`

```bash
cp .env.example .env
nano .env
```
À renseigner impérativement :
```
POSTGRES_PASSWORD=un-mot-de-passe-fort
RTVC_USERNAME=Rtvc2026
RTVC_PASSWORD=•••••
# Dossier NAS contenant les vidéos à indexer en local (optionnel) :
MEDIA_INPUT_DIR=/volume1/ACCES_UTILISATEURS/Rtvc2026
KAIROS_PORT=8090
```

## 4. Lancer

```bash
docker compose -f docker-compose.prod.yml up -d
```
Vérifier :
```bash
docker compose -f docker-compose.prod.yml ps
curl http://localhost:8090/health      # -> {"status":"ok"}
```

## 5. Exposer proprement en HTTPS (reverse proxy DSM)

Panneau de configuration → **Portail des applications** → **Proxy inversé** →
Créer :

| Champ | Valeur |
|---|---|
| Source — protocole | HTTPS |
| Source — nom d'hôte | `kairos.votre-domaine.fr` (ou sous-domaine DSM) |
| Source — port | 443 |
| Destination — nom d'hôte | `localhost` |
| Destination — port | `8090` |

DSM gère le certificat (Let's Encrypt). Kairos est alors accessible en
`https://kairos.votre-domaine.fr`.

> Pour le streaming vidéo, pensez à autoriser les WebSocket/headers longs si
> besoin (onglet « En-tête personnalisé » du proxy).

## 6. Accès direct aux vidéos du NAS (bonus)

Sur le NAS, Kairos peut lire les fichiers **directement** (via `MEDIA_INPUT_DIR`
monté), sans passer par le téléchargement RTVC — plus rapide. Il suffit de
pointer `MEDIA_INPUT_DIR` sur le partage vidéo et d'utiliser l'onglet
« Indexer une vidéo locale » de l'interface.

## 7. Exploitation

| Action | Commande |
|---|---|
| Logs | `docker compose -f docker-compose.prod.yml logs -f` |
| Redémarrer | `docker compose -f docker-compose.prod.yml restart` |
| Mettre à jour | `git pull` puis rebuild/reload |
| Sauvegarde base | `docker compose -f docker-compose.prod.yml exec db pg_dump -U kairos kairos > backup.sql` |
| Arrêter | `docker compose -f docker-compose.prod.yml down` (⚠️ jamais `-v` : efface la base) |

## Sécurité (rappels)

- Ne jamais committer `.env` (déjà dans `.gitignore`).
- Mettre un `POSTGRES_PASSWORD` fort et un `WEBHOOK_SECRET`.
- Ne pas exposer directement le port `8090` sur Internet : passer par le reverse
  proxy HTTPS de DSM.
- Les modèles Vosk/Tesseract tournent en local : aucune donnée ne sort du NAS.
