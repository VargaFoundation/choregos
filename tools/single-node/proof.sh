#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# The single-node proof (ADR 0184, S80-07b), made to run every night:
#
#   blank node -> k3s -> the chart with values/single-node.yaml -> one work item to `approved`
#   -> the chart's backup -> the node DESTROYED -> a new blank node -> k3s -> the chart -> the chart's
#   restore -> the same work item is still `approved`.
#
#   tools/single-node/proof.sh                   # k3s in Docker (k3d), on this machine
#   PROOF_CLUSTER=k3s tools/single-node/proof.sh # k3s itself, on THIS machine (a throwaway VM, root)
#
# Needs: helm, kubectl, openssl, and docker + k3d (k3d mode) or curl and root (k3s mode).
#
# k3d mode only:
#   PROOF_IMPORT_IMAGES="img:tag ..."  images built here (`make images`, retagged), imported into each
#                                      new node before the install: the chart of an unreleased commit
#                                      with its own images. Pair with PROOF_IMAGE_TAG.
#   PROOF_K3S_IMAGE="rancher/k3s:<tag>@sha256:<digest>"
#                                      the node's image (k3d's own default otherwise): the nightly CI job
#                                      pins it by digest, as it pins everything it pulls (S80-04).
#   PROOF_EVICTION="2%"                the kubelet's hard eviction thresholds (imagefs and nodefs), for a
#                                      workstation whose Docker disk is large but more than 85 % used:
#                                      the defaults (15 % and 10 %) put the node under disk pressure
#                                      and no pod is scheduled.
# The backups go to an S3 bucket OFF the node, because the node is destroyed: in k3d mode a SeaweedFS
# container beside the cluster; in k3s mode PROOF_S3_ENDPOINT, PROOF_S3_BUCKET, PROOF_S3_ACCESS_KEY
# and PROOF_S3_SECRET_KEY name one. The site's secrets (the "vault") are drawn once into PROOF_WORKDIR
# and given to both installations, as a real site would.
#
# The agent of the work item is simulated (the demo project's `fake` runtime): the proof is about
# the installation, the backup and the restore, not about a model. Exit 0 only if every step held.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
MODE=${PROOF_CLUSTER:-k3d}
NAME=${PROOF_NAME:-choregos-proof}
NAMESPACE=choregos
RELEASE=choregos
ORG=proof
ADMIN=admin@proof.example
WORK=${PROOF_WORKDIR:-$(mktemp -d)}
export KUBECONFIG="$WORK/kubeconfig"
STARTED=$(date +%s)
mkdir -p "$WORK"

step() { printf '\n== [%4ss] %s\n' "$(($(date +%s) - STARTED))" "$*"; }
k() { kubectl -n "$NAMESPACE" "$@"; }

# ---------------------------------------------------------------- the off-node bucket
s3_up() {
  if [ "$MODE" = k3d ]; then
    docker network inspect "$NAME" >/dev/null 2>&1 || docker network create "$NAME" >/dev/null
    if ! docker inspect "$NAME-s3" >/dev/null 2>&1; then
      # Without `-s3.config` the gateway accepts any keys: the proof's keys are checked by nobody.
      # 4.x, not 3.97: 3.97 answers `object not found` to the HEAD rclone sends after each upload,
      # and the backup's `ship` failed on it (S80-07d); 4.47 answers as an S3 store does.
      docker run -d --name "$NAME-s3" --network "$NAME" \
        chrislusf/seaweedfs:4.47@sha256:ce9e796f1fe6f06968f4c04bdaf8f678dad9c8acdfef3d244133d71bfa6bf882 \
        server -s3 -dir=/data >/dev/null
    fi
    S3_ENDPOINT="http://$(docker inspect -f "{{(index .NetworkSettings.Networks \"$NAME\").IPAddress}}" "$NAME-s3"):8333"
    S3_BUCKET=choregos-backups
    S3_ACCESS_KEY=proof
    S3_SECRET_KEY=proof-secret
    for _ in $(seq 60); do
      # A `weed shell` started before the master is up can hang for good: bounded, then tried again.
      timeout 30 docker exec "$NAME-s3" sh -c "echo 's3.bucket.create -name $S3_BUCKET' | weed shell" >/dev/null 2>&1 && break
      sleep 2
    done
  else
    : "${PROOF_S3_ENDPOINT:?k3s mode needs PROOF_S3_ENDPOINT (an S3 bucket off this machine)}"
    S3_ENDPOINT=$PROOF_S3_ENDPOINT
    S3_BUCKET=${PROOF_S3_BUCKET:?}
    S3_ACCESS_KEY=${PROOF_S3_ACCESS_KEY:?}
    S3_SECRET_KEY=${PROOF_S3_SECRET_KEY:?}
  fi
}

# ---------------------------------------------------------------- the node
node_up() {
  if [ "$MODE" = k3d ]; then
    local extra=()
    [ -z "${PROOF_K3S_IMAGE:-}" ] || extra+=(--image "$PROOF_K3S_IMAGE")
    if [ -n "${PROOF_EVICTION:-}" ]; then
      local t="memory.available<100Mi,imagefs.available<$PROOF_EVICTION,nodefs.available<$PROOF_EVICTION"
      extra+=(--k3s-arg "--kubelet-arg=eviction-hard=$t@server:*")
    fi
    k3d cluster create "$NAME" --network "$NAME" --wait --timeout 300s \
      --kubeconfig-update-default=false --kubeconfig-switch-context=false "${extra[@]}" >/dev/null
    k3d kubeconfig get "$NAME" >"$KUBECONFIG"
    # shellcheck disable=SC2086 # a list of image references
    [ -z "${PROOF_IMPORT_IMAGES:-}" ] || k3d image import -c "$NAME" $PROOF_IMPORT_IMAGES
  else
    curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=${PROOF_K3S_VERSION:-v1.33.5+k3s1} sh -
    cp /etc/rancher/k3s/k3s.yaml "$KUBECONFIG"
  fi
  kubectl wait --for=condition=Ready node --all --timeout=300s
  kubectl get storageclass local-path >/dev/null
}

node_destroy() {
  if [ "$MODE" = k3d ]; then
    k3d cluster delete "$NAME" >/dev/null
  else
    # Removes k3s and /var/lib/rancher/k3s, the local-path volumes with it.
    /usr/local/bin/k3s-uninstall.sh
  fi
}

# ---------------------------------------------------------------- the site's secrets, drawn once
vault() {
  [ -f "$WORK/run-token.pem" ] && return
  openssl ecparam -name prime256v1 -genkey -noout -out "$WORK/run-token.pem" 2>/dev/null
  openssl rand -base64 48 | tr -d '\n' >"$WORK/session-secret"
  printf 'k1:%s' "$(openssl rand -base64 32)" >"$WORK/secrets-keks"
}

install() {
  kubectl create namespace "$NAMESPACE"
  k create secret generic choregos-api-secrets \
    --from-file=run-token-private-key="$WORK/run-token.pem" \
    --from-file=session-secret="$WORK/session-secret" \
    --from-file=secrets-keks="$WORK/secrets-keks"
  k create secret generic choregos-oidc --from-literal=client-secret=proof-unused
  k create secret generic choregos-backup-target \
    --from-literal=access-key="$S3_ACCESS_KEY" --from-literal=secret-key="$S3_SECRET_KEY"
  local tag=()
  [ -n "${PROOF_IMAGE_TAG:-}" ] && tag=(--set "global.imageTag=$PROOF_IMAGE_TAG")
  helm install "$RELEASE" "$ROOT/charts/choregos" -n "$NAMESPACE" \
    -f "$ROOT/charts/choregos/values/single-node.yaml" \
    --set global.domain=choregos.localtest.me \
    --set global.publicUrl=http://choregos.localtest.me \
    --set global.apiUrl=http://choregos.localtest.me \
    --set "choregos-api.ingress.hostnames={choregos.localtest.me}" \
    --set "choregos-web.ingress.hostnames={choregos.localtest.me}" \
    --set global.oidc.issuer=https://idp.invalid/realms/proof \
    --set global.bootstrap.org="$ORG" --set global.bootstrap.orgName=Proof \
    --set global.bootstrap.admins="$ADMIN" \
    --set backup.target.s3.url="s3://$S3_BUCKET/$NAME" \
    --set backup.target.s3.endpoint="$S3_ENDPOINT" \
    "${tag[@]}" --timeout 20m
  # No `helm --wait`: the migrations are a post-install hook (the embedded database does not exist
  # before the install), and the API is not ready before they ran.
  for d in $(k get deploy -o name); do k rollout status "$d" --timeout=900s; done
}

# A pod with the labels the `k8s_job` executor puts on an agent pod tries TCP connections for 30 s;
# prints `open` if one succeeds, `closed` if none does. Not a single try: k3s's NetworkPolicy
# controller (kube-router) adds a pod to the source set of an ingress rule only once it has seen the
# pod, about 3 s after it starts (measured on k3s 1.35, S80-07d): a single early try read the allowed
# path to the egress proxy as `closed`, so a `closed` from it said nothing about the paths that must
# stay closed either. Its image is the API's, already on the node: nothing is pulled.
PROBE='
import socket, sys, time
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    try:
        socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=5).close()
        sys.exit(0)
    except OSError:
        time.sleep(1)
sys.exit(1)
'
probe() {
  local name="probe-$1-$2" image phase
  image=$(k get deploy choregos-api -o jsonpath='{.spec.template.spec.containers[0].image}')
  k run "$name" --restart=Never --image="$image" --image-pull-policy=IfNotPresent \
    --labels=app.kubernetes.io/name=choregos-runner,app.kubernetes.io/component=runner,choregos/run-id=proof \
    --command -- python -c "$PROBE" "$1" "$2" >/dev/null
  for _ in $(seq 60); do
    phase=$(k get pod "$name" -o jsonpath='{.status.phase}')
    case "$phase" in Succeeded | Failed) break ;; esac
    sleep 2
  done
  k delete pod "$name" --wait=false >/dev/null
  case "$phase" in Succeeded) echo open ;; Failed) echo closed ;; *) echo "undecided ($phase)" ;; esac
}

# ADR 0184, S80-07c: an agent pod reaches the API, the model gateway, the memory and the egress
# proxy, and never the embedded PostgreSQL or Temporal. k3s enforces the chart's NetworkPolicies.
isolation() {
  local failed=0 target want got
  for target in choregos-api:8000:open choregos-litellm:4000:open choregos-ecphoria:8432:open \
    choregos-egress:3128:open choregos-postgresql:5432:closed choregos-temporal:7233:closed; do
    IFS=: read -r host port want <<<"$target"
    got=$(probe "$host" "$port")
    echo "  agent pod -> $host:$port: $got (expected $want)"
    [ "$got" = "$want" ] || failed=1
  done
  return $failed
}

# S80-07f, #1854: k3s applies a NEW pod's egress policy 1.2 to 5.2 s after its containers start
# (measured on k3d, 15 starts open out of 15). The `k8s_job` executor puts the egress gate first in an
# agent pod; nothing else of the pod starts until a direct connection out is refused. The Job below is
# built by the executor itself, in the executor worker, with the deployment's own settings
# (`runner.egressGate`); only its runner container is replaced, by a loop that tries the outside
# directly from its first instruction for 10 s. Gated, its first attempt must be refused and none may
# connect. The same pod without the gate is the control: it shows the window the gate closes, and is
# reported, not judged (a faster node may close it before the first attempt). The images are the
# API's, already on the node: the gate runs from any image that carries `choregos_core`.
AGENT_JOB='
import json, sys
from types import SimpleNamespace
from choregos_adapters.executor.k8s_job import KubernetesJobExecutor
from choregos_core.domain import StageJobSpec
from choregos_core.egress_gate import gate_from_env
name, image, gated, loop = sys.argv[1], sys.argv[2], sys.argv[3] == "1", sys.argv[4]
gate = gate_from_env()
if gated and gate is None:
    sys.exit("this deployment does not turn the egress gate on (runner.egressGate.enabled)")
executor = KubernetesJobExecutor(client=SimpleNamespace(), egress_gate=gate if gated else None)
spec = StageJobSpec(run_id=name, project_slug="proof", namespace="choregos", runner_image=image,
                    api_url="http://choregos-api:8000", run_token="unused")
job = executor._job(spec, name)
pod = job["spec"]["template"]["spec"]
runner = next(c for c in pod["containers"] if c["name"] == "runner")
runner["env"] = [e for e in runner["env"] if "valueFrom" not in e]
runner["command"] = ["/app/.venv/bin/python", "-c", loop]
runner.pop("args", None)
print(json.dumps(job))
'
FIRST_PACKETS='
import json, socket, time
start, tries = time.monotonic(), []
while time.monotonic() - start < 10:
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=1).close()
        tries.append("open")
    except OSError:
        tries.append("closed")
    time.sleep(0.2)
print(json.dumps({"first": tries[0], "open": tries.count("open"), "attempts": len(tries)}))
'
agent_pod() {
  local name=$1 gated=$2 image phase
  image=$(k get deploy choregos-api -o jsonpath='{.spec.template.spec.containers[0].image}')
  k exec deploy/choregos-orchestrator-executor -- python -c "$AGENT_JOB" "$name" "$image" "$gated" "$FIRST_PACKETS" |
    k apply -f - >/dev/null
  for _ in $(seq 60); do
    phase=$(k get pod -l "job-name=$name" -o jsonpath='{.items[0].status.phase}' 2>/dev/null || true)
    case "$phase" in Succeeded | Failed) break ;; esac
    sleep 2
  done
  echo "$phase"
}

egress_gate() {
  local phase report gate failed=0
  phase=$(agent_pod proof-gated 1)
  report=$(k logs job/proof-gated -c runner 2>/dev/null || true)
  gate=$(k logs job/proof-gated -c egress-gate 2>/dev/null || true)
  echo "  gated agent pod ($phase): gate $gate"
  echo "  gated agent pod, its first direct connections: $report"
  case "$report" in *'"first": "closed", "open": 0,'*) ;; *) failed=1 ;; esac
  [ "$phase" = Succeeded ] || failed=1
  phase=$(agent_pod proof-ungated 0)
  echo "  control pod without the gate ($phase): $(k logs job/proof-ungated -c runner 2>/dev/null || true)"
  k delete job proof-gated proof-ungated --wait=false >/dev/null
  return $failed
}

token() {
  k exec deploy/choregos-api -- choregos-admin tokens create --email "$ADMIN" --name proof --expires-in-days 1
}

trap 'status=$?; [ $status -ne 0 ] && echo "PROOF FAILED (exit $status) — workdir $WORK"; \
  if [ -z "${PROOF_KEEP:-}" ] && [ "$MODE" = k3d ]; then k3d cluster delete "$NAME" >/dev/null 2>&1; \
  docker rm -f "$NAME-s3" >/dev/null 2>&1; docker network rm "$NAME" >/dev/null 2>&1; fi; true' EXIT

step "off-node bucket ($MODE)"
s3_up
echo "  $S3_ENDPOINT, bucket $S3_BUCKET"
step "blank node -> k3s"
node_up
vault
step "install the chart (values/single-node.yaml)"
install
step "an agent pod reaches what a run uses, and not the database or Temporal"
isolation
step "an agent pod's first packet out is refused: the egress gate holds it until its policy applies"
egress_gate
step "one work item to completion"
TOKEN=$(token)
ITEM=$(k exec -i deploy/choregos-api -- python - run "$ORG" "$TOKEN" <"$ROOT/tools/single-node/work_item.py")
echo "  $ITEM"
step "backup (the chart's CronJob, started now)"
k create job --from=cronjob/choregos-backup proof-backup
k wait --for=condition=complete job/proof-backup --timeout=1200s
for c in prepare database files ship; do k logs job/proof-backup -c "$c" | sed "s/^/  [$c] /"; done
step "destroy the node"
node_destroy
step "a new blank node -> k3s -> the chart"
node_up
install
step "restore"
"$ROOT/tools/single-node/restore.sh"
step "the work item is still there"
TOKEN=$(token)
k exec -i deploy/choregos-api -- python - check "$ORG" "$TOKEN" "$ITEM" <"$ROOT/tools/single-node/work_item.py"
step "PROOF HELD: install, work item, backup, destroy, restore"
