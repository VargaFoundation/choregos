#!/usr/bin/env bash
# Détruit l'environnement de développement, sans laisser de volume orphelin.
set -euo pipefail
cd "$(dirname "$0")/../.."
CLUSTER=${CLUSTER:-choregos}

docker compose -f dev/compose.yaml down -v || true
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  kind delete cluster --name "$CLUSTER"
fi
echo "✓ environnement de développement supprimé"
