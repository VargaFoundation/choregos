#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Essai, élément 8 : un run sans Secret Kubernetes, sur kind, avec une Role sans aucun droit sur les Secrets.
# Arrêt : kind delete cluster --name essai-sans-secret
set -euo pipefail
cd "$(dirname "$0")"
CLUSTER=essai-sans-secret
K="kubectl --context kind-${CLUSTER}"
kind get clusters | grep -qx "${CLUSTER}" || kind create cluster --name "${CLUSTER}" --image kindest/node:v1.36.1 --wait 120s
docker build -q -t essai-run:latest runtime >/dev/null
docker build -q -t essai-api:latest api >/dev/null
docker build -q -t essai-orchestrator:latest orchestrator >/dev/null
# kind 0.30 ne lit pas la configuration containerd v4 des nœuds 1.36 : import direct dans containerd.
for image in essai-run essai-api essai-orchestrator; do
  docker save "${image}:latest" | docker exec -i "${CLUSTER}-control-plane" ctr --namespace=k8s.io images import - >/dev/null
done
${K} apply -f k8s/manifests.yaml >/dev/null
${K} -n essai-control create secret generic essai-internal --from-literal=token="$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')" \
  --dry-run=client -o yaml | ${K} apply -f - >/dev/null
${K} -n essai-control rollout restart deploy/api >/dev/null
${K} -n essai-control rollout status deploy/api --timeout=120s >/dev/null
${K} -n essai-control delete job orchestrator --ignore-not-found >/dev/null
${K} apply -f k8s/orchestrator-job.yaml >/dev/null
${K} -n essai-control wait --for=jsonpath='{.status.conditions[?(@.type=="Complete")].status}'=True job/orchestrator --timeout=300s >/dev/null || true
${K} -n essai-control logs job/orchestrator
echo "--- garde-fous"
echo "orchestrator can create secrets in essai-run: $(${K} auth can-i create secrets -n essai-run --as=system:serviceaccount:essai-control:orchestrator || true)"
echo "orchestrator can read secrets in essai-run: $(${K} auth can-i get secrets -n essai-run --as=system:serviceaccount:essai-control:orchestrator || true)"
echo "secrets in essai-run: $(${K} get secrets -n essai-run -o name | wc -l)"
