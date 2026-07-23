# Déployer Kairos sur Oracle Cloud « Always Free » (gratuit, permanent)

Objectif : une **URL publique permanente** pour qu'un testeur à distance utilise
Kairos, sans payer d'hébergement. Oracle offre à vie une machine virtuelle ARM
très généreuse : **jusqu'à 4 cœurs + 24 Go de RAM** (largement assez).

> **Vocabulaire.** Une *VM* (machine virtuelle) = un ordinateur loué dans le
> cloud, auquel on se connecte à distance en *SSH* (terminal sécurisé).
> *ARM* = un type de processeur (celui des téléphones) — différent de ton PC
> (x86) : il faudra **reconstruire** les images Docker sur la VM, c'est prévu
> dans le guide.

---

## Étape 1 — Créer le compte Oracle Cloud (~15 min)

1. Va sur **https://signup.cloud.oracle.com** et crée un compte.
2. Une **carte bancaire est demandée** pour vérifier ton identité :
   **0 € prélevé** (une empreinte de ~1 € peut apparaître puis disparaître).
   Le compte reste en mode « Always Free » tant que tu ne l'upgrades pas
   explicitement — il ne peut PAS facturer tout seul.
3. **Choix crucial : la « Home Region »** (ex. *France Central — Paris*,
   *Frankfurt*…). Elle est **définitive**. Prends une région proche de toi
   (Paris ou Marseille). C'est là que vivra ta VM.
4. Confirme l'e-mail, connecte-toi à la console : https://cloud.oracle.com

## Étape 2 — Créer la VM ARM (~10 min)

Dans la console : **☰ Menu → Compute → Instances → Create instance**.

| Champ | Valeur à mettre |
|---|---|
| Name | `kairos` |
| Image | **Ubuntu 24.04** (garde bien *aarch64/ARM* quand tu choisis la shape) |
| Shape | **Ampere → VM.Standard.A1.Flex** → **2 OCPU / 12 Go RAM** (suffisant ; 4/24 si dispo) |
| Réseau (VCN) | laisse « Create new virtual cloud network » (défauts) |
| **Public IP** | vérifie **« Assign a public IPv4 address » = Yes** |
| Boot volume | 100 Go (le gratuit couvre 200 Go au total) |
| **SSH keys** | colle ta **clé publique** (voir ci-dessous) |

**Générer la clé SSH sur ton PC** (PowerShell) :
```powershell
ssh-keygen -t ed25519            # Entrée 3 fois (pas de phrase)
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub   # copie cette ligne dans le champ SSH keys
```

Clique **Create**. Quand l'instance est verte (*Running*), note son
**adresse IP publique** (ex. `129.151.x.x`).

> ⚠️ **« Out of capacity »** : erreur fréquente sur les VM ARM gratuites
> (très demandées). Solutions : réessaie plus tard (la nuit ça passe mieux),
> essaie un autre *Availability Domain* (AD-1/2/3), ou réduis à 1 OCPU/6 Go
> puis redimensionne ensuite. Ça finit toujours par passer.

## Étape 3 — Ouvrir le port 80 (il y a DEUX pare-feux !)

C'est le piège n°1 chez Oracle : il faut ouvrir le port **aux deux niveaux**.

**3a. Pare-feu Oracle (Security List)** — dans la console :
Compute → ta VM → clique le lien **Virtual cloud network** → **Security Lists**
→ *Default Security List* → **Add Ingress Rules** :

| Champ | Valeur |
|---|---|
| Source CIDR | `0.0.0.0/0` |
| IP Protocol | TCP |
| Destination Port Range | `80` |

**3b. Pare-feu de la VM (iptables)** — après connexion SSH (étape 4) :
```bash
sudo iptables -I INPUT 6 -p tcp --dport 80 -j ACCEPT
sudo netfilter-persistent save
```

## Étape 4 — Se connecter et installer Docker (~5 min)

Depuis PowerShell sur ton PC :
```powershell
ssh ubuntu@IP_PUBLIQUE
```
(`ubuntu` est l'utilisateur par défaut des images Ubuntu d'Oracle.)

Puis, sur la VM :
```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker ubuntu
exit        # ressors puis reconnecte-toi pour activer le groupe docker
```

## Étape 5 — Envoyer le projet sur la VM

Depuis PowerShell (ton PC) :
```powershell
scp -r C:\Users\menga\PycharmProjects\kairos ubuntu@IP_PUBLIQUE:~/kairos
```
*(Alternative propre si tu as un dépôt GitHub privé : `git clone` sur la VM.)*

## Étape 6 — Configurer

Sur la VM :
```bash
cd ~/kairos
cp .env.example .env
nano .env
```
À renseigner :
```
POSTGRES_PASSWORD=un-mot-de-passe-fort
RTVC_USERNAME=Rtvc2026
RTVC_PASSWORD=•••••
KAIROS_PORT=80                      # URL sans :8090 pour le testeur
KAIROS_PASSWORD=le-mdp-du-testeur   # OBLIGATOIRE (app exposée sur Internet)
API_WORKERS=2
WORKER_CONCURRENCY=1
MEDIA_INPUT_DIR=./media_in
```
```bash
mkdir -p media_in     # dossier vidéos locales (peut rester vide)
```

> `KAIROS_PASSWORD` est vital : sans lui, n'importe qui trouvant l'IP pourrait
> explorer ton NAS RTVC via l'interface. Avec lui, le navigateur demande le
> mot de passe une fois, puis tout fonctionne normalement.

## Étape 7 — Construire et lancer (~15 min de build ARM)

```bash
docker compose -f docker-compose.prod.yml build     # reconstruit pour ARM
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps        # tout doit être Up/healthy
curl http://localhost/health                        # -> {"status":"ok"}
```
Le build télécharge torch CPU (version ARM), le modèle Vosk FR et le modèle
d'embeddings — c'est long la première fois, puis mis en cache.

## Étape 8 — Donner l'accès au testeur

Envoie-lui simplement :
- **l'URL** : `http://IP_PUBLIQUE`
- **le mot de passe** (`KAIROS_PASSWORD`) — nom d'utilisateur : n'importe quoi.

Il peut chercher, lire les vidéos au timestamp, et même indexer une vidéo du
NAS RTVC via l'explorateur — depuis n'importe quelle ville.

---

## Bonus : HTTPS + nom de domaine (optionnel)

Pour `https://kairos.tondomaine.fr` au lieu de `http://IP` : un sous-domaine
gratuit (DuckDNS) + le reverse proxy **Caddy** (2 lignes de config, certificat
automatique). À faire plus tard si besoin — pour un test, l'IP suffit.

## Pièges connus

| Problème | Cause / solution |
|---|---|
| « Out of capacity » à la création | Manque de stock ARM gratuit → réessayer, changer d'AD, réduire la taille |
| Port 80 injoignable | Oublié **un des deux** pare-feux (étape 3a **et** 3b) |
| VM récupérée par Oracle | Les VM Always Free *inactives* peuvent être arrêtées ; garde-la un minimum utilisée, ou upgrade le compte en « Pay As You Go » (toujours 0 € tant qu'on reste dans le quota Free) |
| Build lent | Normal sur ARM la 1re fois (~15 min) ; ensuite tout est en cache |
| `permission denied` docker | Reconnecte-toi en SSH après le `usermod` (étape 4) |

## Exploitation courante

```bash
docker compose -f docker-compose.prod.yml logs -f      # logs en direct
docker compose -f docker-compose.prod.yml restart      # redémarrer
docker compose -f docker-compose.prod.yml down         # arrêter (JAMAIS -v : efface la base)
```
