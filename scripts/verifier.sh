#!/usr/bin/env bash
# Vérification de bon fonctionnement de Kairos (test de fumée).
#
# Usage :  bash scripts/verifier.sh [url] [utilisateur:motdepasse]
# Exemple : bash scripts/verifier.sh http://localhost:5000 k:MonMotDePasse
#
# À lancer après chaque mise à jour de dépendances ou déploiement : vérifie que
# la chaîne complète (API, base, recherche, lecture, vignettes) fonctionne.
set -u

URL="${1:-http://localhost:5000}"
AUTH="${2:-}"
CURL=(curl -sS --max-time 45)
[ -n "$AUTH" ] && CURL+=(-u "$AUTH")

ok=0; ko=0
verifier() {  # nom, commande_qui_affiche_OK_ou_pas
  printf "  %-34s " "$1"
  if eval "$2" >/dev/null 2>&1; then echo "OK"; ok=$((ok+1)); else echo "ECHEC"; ko=$((ko+1)); fi
}

echo "=== Vérification de Kairos sur $URL ==="

verifier "API en ligne (/health)"        "${CURL[*]} $URL/health | grep -q ok"
verifier "Bibliothèque (/media)"         "${CURL[*]} $URL/media | grep -q rtvc_id"
verifier "Statistiques (/stats)"         "${CURL[*]} $URL/stats | grep -q medias"
verifier "Recherche sémantique"          "${CURL[*]} --get --data-urlencode 'q=test' --data-urlencode 'limit=1' $URL/search | grep -q hits"
verifier "Documentation API (/docs)"     "${CURL[*]} -o /dev/null -w '%{http_code}' $URL/docs | grep -q 200"

# Ces trois-là nécessitent au moins un média indexé
MID=$(${CURL[*]} "$URL/media" 2>/dev/null | python -c "
import sys,json
d=json.load(sys.stdin)
r=[m for m in d if m.get('status')=='ready' and m.get('has_playback')]
print(r[0]['rtvc_id'] if r else '')
" 2>/dev/null)

if [ -n "$MID" ]; then
  verifier "Lecture vidéo (média $MID)"    "${CURL[*]} -o /dev/null -w '%{http_code}' -r 0-9999 $URL/media/$MID/video | grep -qE '20(0|6)'"
  verifier "Vignette d'aperçu"             "${CURL[*]} -o /dev/null -w '%{http_code}' '$URL/media/$MID/thumbnail?t=5' | grep -q 200"
  verifier "Export sous-titres (SRT)"      "${CURL[*]} $URL/media/$MID/transcript.srt | grep -q ' --> '"
else
  echo "  (aucun média prêt : lecture/vignette/export non testés)"
fi

echo ""
echo "Résultat : $ok réussis, $ko échoués"
[ "$ko" -eq 0 ] || exit 1
