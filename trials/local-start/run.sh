#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Trial, element 9: builds the images, starts the integrated stack, measures the start-up and the
# memory, then plays the IT4IT scenario (elements 2 to 6) against the running stack.
#
#   ./run.sh            # builds, starts, measures, plays, stops
#   KEEP=1 ./run.sh     # leaves the stack up (docker compose down -v to stop it)
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(git rev-parse --show-toplevel)"
export TRIAL_API_PORT="${TRIAL_API_PORT:-18190}"

# The pair of run-token keys, shared by the API and the orchestrator - and by `scenery.py`, which mints
# the run's token as the orchestrator would. Ephemeral: it dies with this script.
keys="$(mktemp -d)"
trap 'rm -rf "$keys"' EXIT
openssl ecparam -name prime256v1 -genkey -noout -out "$keys/private.pem" 2>/dev/null
openssl ec -in "$keys/private.pem" -pubout -out "$keys/public.pem" 2>/dev/null
CHOREGOS_RUN_TOKEN_PRIVATE_KEY="$(cat "$keys/private.pem")"
CHOREGOS_RUN_TOKEN_PUBLIC_KEY="$(cat "$keys/public.pem")"
export CHOREGOS_RUN_TOKEN_PRIVATE_KEY CHOREGOS_RUN_TOKEN_PUBLIC_KEY

seconds() { date +%s; }

start="$(seconds)"
docker build -q -f "$ROOT/docker/api.Dockerfile" --target api -t choregos-api:trial-base "$ROOT" >/dev/null
docker build -q -f "$ROOT/docker/api.Dockerfile" --target worker -t choregos-orchestrator:trial-base "$ROOT" >/dev/null
docker build -q -f extension.Dockerfile --build-arg BASE=choregos-api:trial-base -t choregos-api:trial . >/dev/null
docker build -q -f extension.Dockerfile --build-arg BASE=choregos-orchestrator:trial-base -t choregos-orchestrator:trial . >/dev/null
build=$(( $(seconds) - start ))

docker compose down -v >/dev/null 2>&1 || true
start="$(seconds)"
docker compose up -d --wait api worker lakekeeper >/dev/null
startup=$(( $(seconds) - start ))

# Memory at rest, once the services have settled.
sleep 20
memory="$(docker stats --no-stream --format '{{.Name}};{{.MemUsage}}' \
  $(docker compose ps -q) | awk -F';' '
    { split($2, u, " / "); v = u[1]
      if (v ~ /GiB$/) { sub(/GiB$/, "", v); m = v * 1024 } else if (v ~ /MiB$/) { sub(/MiB$/, "", v); m = v }
      else if (v ~ /KiB$/) { sub(/KiB$/, "", v); m = v / 1024 } else { m = 0 }
      total += m; printf "  %-42s %8.0f MiB\n", $1, m }
    END { printf "  %-42s %8.0f MiB\n", "TOTAL", total }')"

echo "image build             : ${build} s"
echo "start-up (up --wait)    : ${startup} s"
echo "memory at rest:"
echo "${memory}"
echo

status=0
(cd "$ROOT" && uv run python trials/local-start/scenario.py) || status=$?

if [ -z "${KEEP:-}" ]; then
  docker compose down -v >/dev/null 2>&1 || true
fi
exit "$status"
