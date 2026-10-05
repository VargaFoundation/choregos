#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Essai, élément 9 : construit les images, démarre la pile intégrée, mesure le démarrage et la
# mémoire, puis joue le scénario IT4IT (éléments 2 à 6) contre la pile qui tourne.
#
#   ./run.sh            # construit, démarre, mesure, joue, arrête
#   GARDER=1 ./run.sh   # laisse la pile debout (docker compose down -v pour l'arrêter)
set -euo pipefail
cd "$(dirname "$0")"
RACINE="$(git rev-parse --show-toplevel)"
export ESSAI_PORT_API="${ESSAI_PORT_API:-18190}"

# La paire des jetons de run, partagée par l'API et l'orchestrateur — et par `decor.py`, qui frappe le
# jeton du run comme le ferait l'orchestrateur. Éphémère : elle meurt avec ce script.
cles="$(mktemp -d)"
trap 'rm -rf "$cles"' EXIT
openssl ecparam -name prime256v1 -genkey -noout -out "$cles/prive.pem" 2>/dev/null
openssl ec -in "$cles/prive.pem" -pubout -out "$cles/public.pem" 2>/dev/null
CHOREGOS_RUN_TOKEN_PRIVATE_KEY="$(cat "$cles/prive.pem")"
CHOREGOS_RUN_TOKEN_PUBLIC_KEY="$(cat "$cles/public.pem")"
export CHOREGOS_RUN_TOKEN_PRIVATE_KEY CHOREGOS_RUN_TOKEN_PUBLIC_KEY

secondes() { date +%s; }

debut="$(secondes)"
docker build -q -f "$RACINE/docker/api.Dockerfile" --target api -t choregos-api:essai-base "$RACINE" >/dev/null
docker build -q -f "$RACINE/docker/api.Dockerfile" --target worker -t choregos-orchestrator:essai-base "$RACINE" >/dev/null
docker build -q -f greffon.Dockerfile --build-arg BASE=choregos-api:essai-base -t choregos-api:essai . >/dev/null
docker build -q -f greffon.Dockerfile --build-arg BASE=choregos-orchestrator:essai-base -t choregos-orchestrator:essai . >/dev/null
construction=$(( $(secondes) - debut ))

docker compose down -v >/dev/null 2>&1 || true
debut="$(secondes)"
docker compose up -d --wait api worker lakekeeper >/dev/null
demarrage=$(( $(secondes) - debut ))

# Mémoire au repos, une fois les services stabilisés.
sleep 20
memoire="$(docker stats --no-stream --format '{{.Name}};{{.MemUsage}}' \
  $(docker compose ps -q) | awk -F';' '
    { split($2, u, " / "); v = u[1]
      if (v ~ /GiB$/) { sub(/GiB$/, "", v); m = v * 1024 } else if (v ~ /MiB$/) { sub(/MiB$/, "", v); m = v }
      else if (v ~ /KiB$/) { sub(/KiB$/, "", v); m = v / 1024 } else { m = 0 }
      total += m; printf "  %-42s %8.0f Mio\n", $1, m }
    END { printf "  %-42s %8.0f Mio\n", "TOTAL", total }')"

echo "construction des images : ${construction} s"
echo "démarrage (up --wait)   : ${demarrage} s"
echo "mémoire au repos :"
echo "${memoire}"
echo

statut=0
(cd "$RACINE" && uv run python essai/demarrage-local/scenario.py) || statut=$?

if [ -z "${GARDER:-}" ]; then
  docker compose down -v >/dev/null 2>&1 || true
fi
exit "$statut"
