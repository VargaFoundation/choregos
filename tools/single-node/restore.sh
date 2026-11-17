#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Restore a single-node installation from its backup (ADR 0184, docs/runbooks/single-node-restore.md).
#
#   tools/single-node/restore.sh                     # the newest complete backup
#   tools/single-node/restore.sh 20261117T020000Z    # that one
#
# Environment: NAMESPACE (default choregos), RELEASE (default choregos), KUBECTL (default kubectl),
# RESTORE_TIMEOUT (seconds, default 3600).
#
# What it does, in this order: stops every writer of the release (its Deployments: the API, the
# console, the workers, Temporal, the gateway, the memory; PostgreSQL stays up, it is restored
# through its own protocol), runs the chart's restore job (`<release>-restore`), shows its log, then
# brings every Deployment back to the replica count it had. A failed restore leaves the writers
# stopped, on purpose: starting them on a half-restored state would write over it.
set -euo pipefail

NAMESPACE=${NAMESPACE:-choregos}
RELEASE=${RELEASE:-choregos}
KUBECTL=${KUBECTL:-kubectl}
TIMEOUT=${RESTORE_TIMEOUT:-3600}
STAMP=${1:-}
k() { $KUBECTL -n "$NAMESPACE" "$@"; }

k get cronjob "$RELEASE-restore" >/dev/null || {
  echo "no cronjob/$RELEASE-restore in $NAMESPACE: is backup.enabled on in this release?" >&2
  exit 2
}

selector="app.kubernetes.io/instance=$RELEASE"
replicas=$(k get deploy -l "$selector" -o jsonpath='{range .items[*]}{.metadata.name}={.spec.replicas}{"\n"}{end}')
echo "== stopping the writers"
echo "$replicas"
k scale deploy -l "$selector" --replicas=0
writers="app.kubernetes.io/name in (choregos-api,choregos-web,choregos-orchestrator,choregos-temporal,choregos-litellm,choregos-ecphoria)"
if k get pod -l "$selector,$writers" -o name | grep -q .; then
  k wait --for=delete pod -l "$selector,$writers" --timeout=300s
fi

echo "== restore job"
if [ -n "$STAMP" ]; then
  k create configmap "$RELEASE-restore" --from-literal=stamp="$STAMP" --dry-run=client -o yaml | k apply -f -
else
  k delete configmap "$RELEASE-restore" --ignore-not-found
fi
job="$RELEASE-restore-$(date -u +%Y%m%d%H%M%S)"
k create job --from="cronjob/$RELEASE-restore" "$job"
if ! k wait --for=condition=complete "job/$job" --timeout="${TIMEOUT}s"; then
  echo "== the restore FAILED; the writers stay stopped. Its log:" >&2
  for c in fetch database files objects done; do
    k logs "job/$job" -c "$c" 2>/dev/null | sed "s/^/[$c] /" >&2 || true
  done
  exit 1
fi
for c in fetch database files objects done; do
  k logs "job/$job" -c "$c" 2>/dev/null | sed "s/^/[$c] /" || true
done

echo "== starting the writers again"
while IFS='=' read -r name count; do
  [ -n "$name" ] && k scale deploy "$name" --replicas="$count"
done <<<"$replicas"
while IFS='=' read -r name count; do
  if [ -n "$name" ] && [ "$count" != 0 ]; then k rollout status "deploy/$name" --timeout=600s; fi
done <<<"$replicas"
echo "== restored"
