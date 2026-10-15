#!/usr/bin/env bash
# Brings up the whole development environment: kind cluster, operators, Tilt.
# Target set by the plan: under 15 minutes on a 16 GB machine.
set -euo pipefail

cd "$(dirname "$0")/../.."
CLUSTER=${CLUSTER:-choregos}

require() {
  command -v "$1" >/dev/null 2>&1 || { echo "✗ $1 is required (see docs/development.md)"; exit 1; }
}
require kind
require kubectl
require helm

if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "· kind cluster '$CLUSTER' already present"
else
  echo "· creating the kind cluster '$CLUSTER'"
  kind create cluster --config dev/kind.yaml --wait 120s
fi
kubectl config use-context "kind-$CLUSTER"

echo "· dependencies (Postgres, Temporal, LiteLLM, Keycloak, RustFS) in docker compose"
docker compose -f dev/compose.yaml up -d

echo "· platform namespaces"
for ns in choregos-system choregos-data choregos-temporal choregos-gateway choregos-memory gateway monitoring; do
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
done

if command -v tilt >/dev/null 2>&1; then
  echo "· tilt up (Ctrl-C to quit, `make dev-down` to destroy everything)"
  cd dev && exec tilt up
fi

echo "· Tilt is not installed: deploying the chart directly in development mode"
helm upgrade --install choregos charts/choregos \
  --namespace choregos-system \
  -f charts/choregos/values.yaml -f charts/choregos/values/dev.yaml \
  --wait --timeout 10m
kubectl -n choregos-system get pods
