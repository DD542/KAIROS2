# Déployer Kairos sur Google Cloud (crédit gratuit 300 $, 90 jours)

Objectif : une VM avec une capacité **garantie** (contrairement à l'offre
Always Free ARM d'Oracle, souvent en rupture de stock), pour déployer
aujourd'hui.

> **Coût réel : 0 €.** La carte bancaire sert à vérifier ton identité ; Google
> ne peut **jamais** prélever au-delà du crédit sans que tu passes toi-même en
> compte payant (action volontaire, un bouton à cliquer explicitement).

---

## Étape 1 — Créer le compte (~10 min)

1. Va sur **https://cloud.google.com/free**
2. Connecte-toi avec un compte Google (ou crées-en un)
3. Clique **« Get started for free »** / **« Profiter de l'essai gratuit »**
4. Renseigne pays, carte bancaire (vérification, non facturée)
5. Tu arrives sur la **console** : https://console.cloud.google.com

## Étape 2 — Créer un projet

En haut de la console, à côté du logo Google Cloud, clique le sélecteur de
projet → **« New Project »** → nomme-le `kairos` → **Create**. Attends
quelques secondes que le projet soit sélectionné (son nom apparaît en haut).

## Étape 3 — Activer l'API Compute Engine

Dans la barre de recherche en haut de la console, tape **« Compute Engine »**
→ clique le résultat → clique **« Enable »** (active l'API, ~1 min).

## Étape 4 — Créer la VM

Menu ☰ → **Compute Engine** → **VM instances** → **Create instance**.

| Champ | Valeur |
|---|---|
| Name | `kairos` |
| Region | une proche de toi (`europe-west1` = Belgique, `europe-west9` = Paris) |
| Machine type | **E2 → e2-standard-2** (2 vCPU, 8 Go RAM) |
| Boot disk | clique **Change** → OS : **Ubuntu** → version **Ubuntu 24.04 LTS** → taille **30 Go** → **Select** |
| Firewall | coche **« Allow HTTP traffic »** |

Laisse le reste par défaut → clique **Create** (30-60 s de création).

> 💡 Pas de blocage « out of capacity » attendu ici — c'est justement l'intérêt
> de cette option face à Oracle.

## Étape 5 — Ouvrir le port de Kairos (5000)

Le firewall par défaut n'ouvre que 80/443. On ajoute le port de l'app :

Menu ☰ → **VPC network** → **Firewall** → **Create firewall rule** :

| Champ | Valeur |
|---|---|
| Name | `allow-kairos` |
| Targets | All instances in the network |
| Source IPv4 ranges | `0.0.0.0/0` |
| Protocols and ports | coche **TCP**, tape `5000` |

→ **Create**.

## Étape 6 — Se connecter à la VM

Le plus simple : dans la liste **VM instances**, sur la ligne `kairos`,
clique le bouton **SSH** (colonne Connect) → une fenêtre de terminal
s'ouvre **directement dans le navigateur**, aucune clé à gérer.

## Étape 7 — Installer Docker

Dans ce terminal SSH (dans le navigateur) :
```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER
exit
```
Puis reclique **SSH** pour rouvrir une session (active le groupe docker).

## Étape 8 — Envoyer le projet Kairos sur la VM

Option la plus simple si le code est sur GitHub :
```bash
git clone <URL_DE_TON_DEPOT> kairos
cd kairos
```

Sinon, depuis PowerShell sur ton PC (remplace l'IP par celle affichée dans
la console, colonne « External IP ») :
```powershell
gcloud compute scp --recurse C:\Users\menga\PycharmProjects\kairos kairos:~/kairos --zone=<ta-zone>
```
*(nécessite `gcloud` CLI installé ; sinon, le plus simple reste `git clone` ou
un transfert via l'onglet « Upload file » de la fenêtre SSH navigateur, en
zippant le dossier au préalable.)*

## Étape 9 — Configurer

```bash
cd ~/kairos
cp .env.example .env
nano .env
```
Renseigner :
```
POSTGRES_PASSWORD=un-mot-de-passe-fort
RTVC_USERNAME=Rtvc2026
RTVC_PASSWORD=•••••
KAIROS_PORT=5000
KAIROS_PASSWORD=le-mdp-du-testeur
API_WORKERS=2
WORKER_CONCURRENCY=1
MEDIA_INPUT_DIR=./media_in
```
(`Ctrl+O` puis Entrée pour enregistrer, `Ctrl+X` pour quitter nano)
```bash
mkdir -p media_in
```

## Étape 10 — Construire et lancer

```bash
docker compose -f docker-compose.prod.yml up -d --build
```
Premier build ~10-15 min (télécharge torch, Whisper, les modèles).
```bash
docker compose -f docker-compose.prod.yml ps      # tout doit être Up
curl http://localhost:5000/health                  # -> {"status":"ok"}
```

## Étape 11 — Donner l'accès au testeur

Récupère l'**External IP** dans la console (VM instances) et envoie :
- URL : `http://IP_EXTERNE:5000`
- Mot de passe : celui de `KAIROS_PASSWORD`

---

## Exploitation

```bash
docker compose -f docker-compose.prod.yml logs -f       # logs en direct
docker compose -f docker-compose.prod.yml restart       # redémarrer
docker compose -f docker-compose.prod.yml down          # arrêter (JAMAIS -v)
```

## Pièges connus

| Problème | Solution |
|---|---|
| Port 5000 injoignable | vérifier la règle firewall `allow-kairos` (étape 5) |
| `permission denied` docker | reconnecte-toi en SSH après le `usermod` |
| Build lent | normal la 1re fois (~15 min), ensuite en cache |
| Compte facturé par erreur | impossible sans action volontaire ; vérifier dans Billing que le compte reste en mode "Free trial" |
