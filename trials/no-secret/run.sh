#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Trial, element 8: a run without a Kubernetes Secret, on kind, with a Role that has no right on Secrets.
# Stop: kind delete cluster --name trial-no-secret
set -euo pipefail
cd "$(dirname "$0")"
CLUSTER=trial-no-secret
K="kubectl --context kind-${CLUSTER}"
kind get clusters | grep -qx "${CLUSTER}" || kind create cluster --name "${CLUSTER}" --image kindest/node:v1.36.1 --wait 120s
docker build -q -t trial-run:latest runtime >/dev/null
docker build -q -t trial-api:latest api >/dev/null
docker build -q -t trial-orchestrator:latest orchestrator >/dev/null
# kind 0.30 does not read the containerd v4 configuration of the 1.36 nodes: import straight into containerd.
for image in trial-run trial-api trial-orchestrator; do
  docker save "${image}:latest" | docker exec -i "${CLUSTER}-control-plane" ctr --namespace=k8s.io images import - >/dev/null
done
${K} apply -f k8s/manifests.yaml >/dev/null
${K} -n trial-control create secret generic trial-internal --from-literal=token="$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')" \
  --dry-run=client -o yaml | ${K} apply -f - >/dev/null
${K} -n trial-control rollout restart deploy/api >/dev/null
${K} -n trial-control rollout status deploy/api --timeout=120s >/dev/null
${K} -n trial-control delete job orchestrator --ignore-not-found >/dev/null
${K} apply -f k8s/orchestrator-job.yaml >/dev/null
${K} -n trial-control wait --for=jsonpath='{.status.conditions[?(@.type=="Complete")].status}'=True job/orchestrator --timeout=300s >/dev/null || true
${K} -n trial-control logs job/orchestrator
echo "--- guard rails"
echo "orchestrator can create secrets in trial-run: $(${K} auth can-i create secrets -n trial-run --as=system:serviceaccount:trial-control:orchestrator || true)"
echo "orchestrator can read secrets in trial-run: $(${K} auth can-i get secrets -n trial-run --as=system:serviceaccount:trial-control:orchestrator || true)"
echo "secrets in trial-run: $(${K} get secrets -n trial-run -o name | wc -l)"
