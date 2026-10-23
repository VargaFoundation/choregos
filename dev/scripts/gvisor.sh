#!/usr/bin/env bash
# gVisor for the hosted agents (S41-17, ADR 0080): the pinned release, checked, then either kept
# in a directory or installed into the kind cluster's runners nodes.
#
#   dev/scripts/gvisor.sh fetch DIR   the release in DIR (runsc, its shim, gvisor-bin/)
#   dev/scripts/gvisor.sh kind        runsc into each `role=runners` node of the kind cluster, the
#                                     containerd runtime `runsc`, and the `gvisor` RuntimeClass
#
# The release is pinned with its sha512; the `hosted-gvisor` CI job fetches it through this script (a
# test keeps it so). On a real cluster the `gvisor-installer` DaemonSet of infra/bootstrap/gvisor.yaml does
# the node part; kind nodes are containers, so this copies the binaries in and restarts containerd.
set -euo pipefail
cd "$(dirname "$0")/../.."

GVISOR_RELEASE=20261005.0
GVISOR_SHA512=79869ae9a589355a46d46fdac068d9a954e741741ffe77c64e38c56ea955498c1cb04e9bce44e1cce5155333e6b76bee72347c6661bb7c78fd2fe3aad198c8fe
GVISOR_URL="https://storage.googleapis.com/gvisor/releases/release/${GVISOR_RELEASE}/x86_64/gvisor.tar.bz2"
CLUSTER=${CLUSTER:-choregos}
CONTEXT="kind-${CLUSTER}"

fetch() {
  local dir=$1
  mkdir -p "$dir"
  if [ ! -f "$dir/gvisor.tar.bz2" ]; then
    curl -fsSL -o "$dir/gvisor.tar.bz2.part" "$GVISOR_URL"
    mv "$dir/gvisor.tar.bz2.part" "$dir/gvisor.tar.bz2"
  fi
  echo "${GVISOR_SHA512}  $dir/gvisor.tar.bz2" | sha512sum -c - >/dev/null \
    || { echo "✗ gvisor.tar.bz2 does not match the pinned sha512" >&2; rm -f "$dir/gvisor.tar.bz2"; exit 1; }
  tar -xjf "$dir/gvisor.tar.bz2" -C "$dir"
  # sed, not head: head closes the pipe early, and under pipefail runsc's SIGPIPE fails the script (141).
  "$dir/runsc" --version | sed -n 1p
}

install_kind() {
  command -v kind >/dev/null || { echo "✗ kind is required (docs/development.md)" >&2; exit 1; }
  local cache=${GVISOR_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/choregos/gvisor}
  fetch "$cache"
  local nodes
  nodes=$(kubectl --context "$CONTEXT" get nodes -l role=runners -o jsonpath='{.items[*].metadata.name}')
  [ -n "$nodes" ] || { echo "✗ no role=runners node in $CONTEXT" >&2; exit 1; }
  for node in $nodes; do
    docker cp "$cache/runsc" "$node:/usr/local/bin/runsc"
    docker cp "$cache/containerd-shim-runsc-v1" "$node:/usr/local/bin/containerd-shim-runsc-v1"
    docker cp "$cache/gvisor-bin" "$node:/usr/local/bin/"
    # containerd 2 names the CRI plugin `io.containerd.cri.v1.runtime`, 1.x `io.containerd.grpc.v1.cri`.
    docker exec "$node" sh -euc '
      config=/etc/containerd/config.toml
      grep -q "runtimes.runsc\]" "$config" && exit 0
      if grep -q "^version = 3" "$config"; then plugin="io.containerd.cri.v1.runtime"
      else plugin="io.containerd.grpc.v1.cri"; fi
      printf "\n[plugins.\"%s\".containerd.runtimes.runsc]\n  runtime_type = \"io.containerd.runsc.v1\"\n" \
        "$plugin" >> "$config"
      systemctl restart containerd'
    echo "✓ runsc on $node"
  done
  # The RuntimeClass alone: the installer DaemonSet beside it is for real nodes.
  kubectl --context "$CONTEXT" apply -f infra/bootstrap/gvisor.yaml --selector '!app.kubernetes.io/part-of'
  echo "✓ RuntimeClass gvisor — CHOREGOS_CLUSTER_CONTEXT=$CONTEXT make test-cluster"
}

case "${1:-}" in
  fetch) fetch "${2:?usage: gvisor.sh fetch DIR}" ;;
  kind) install_kind ;;
  *) echo "usage: dev/scripts/gvisor.sh fetch DIR | kind" >&2; exit 2 ;;
esac
