#!/usr/bin/env bash
# Audit de sécurité des dépendances Kairos.
#
# Usage :  bash scripts/audit-securite.sh
#
# À relancer régulièrement (avant chaque mise en production, ou une fois par
# mois) : de nouvelles vulnérabilités sont publiées en continu sur des paquets
# qui étaient sains au moment du build.
set -u

echo "=== Vulnérabilités connues (pip-audit) ==="
docker compose exec -T backend sh -c \
  'pip install -q pip-audit 2>/dev/null; pip-audit --progress-spinner off 2>&1' \
  | tail -40

echo ""
echo "=== Paquets en retard de version ==="
docker compose exec -T backend pip list --outdated 2>/dev/null | head -25

echo ""
echo "Si des vulnérabilités apparaissent :"
echo "  1. monter la version concernée dans backend/requirements.txt"
echo "  2. docker compose build backend worker"
echo "  3. docker compose up -d && bash scripts/verifier.sh"
