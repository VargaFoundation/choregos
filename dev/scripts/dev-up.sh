#!/usr/bin/env bash
# Monte l'environnement de développement complet : cluster kind, opérateurs, Tilt.
# Objectif tenu par le plan : moins de 15 minutes sur un poste de 16 Go.
set -euo pipefail

cd "$(dirname "$0")/../.."
CLUSTER=${CLUSTER:-choregos}

require() {
  command -v "$1" >/dev/null 2>&1 || { echo "✗ $1 est requis (voir docs/dev.md)"; exit 1; }
}
require kind
require kubectl
require helm

if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "· cluster kind '$CLUSTER' déjà présent"
else
  echo "· création du cluster kind '$CLUSTER'"
  kind create cluster --config dev/kind.yaml --wait 120s
fi
kubectl config use-context "kind-$CLUSTER"

echo "· dépendances (Postgres, Temporal, LiteLLM, Keycloak, MinIO) en docker compose"
docker compose -f dev/compose.yaml up -d

echo "· namespaces de la plateforme"
for ns in choregos-system choregos-data choregos-temporal choregos-gateway choregos-memory gateway monitoring; do
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
done

if command -v tilt >/dev/null 2>&1; then
  echo "· tilt up (Ctrl-C pour quitter, `make dev-down` pour tout détruire)"
  cd dev && exec tilt up
fi

echo "· Tilt n'est pas installé : déploiement direct du chart en mode développement"
helm upgrade --install choregos charts/choregos \
  --namespace choregos-system \
  -f charts/choregos/values.yaml -f charts/choregos/values/dev.yaml \
  --wait --timeout 10m
kubectl -n choregos-system get pods
