#!/usr/bin/env bash
# Shim de refus : un agent n'a aucune raison d'appeler ces outils depuis son workspace.
# Le refus est explicite et journalisé — c'est de la défense en profondeur, pas la garantie
# (la garantie est la NetworkPolicy, l'absence de credential et la vérification du diff).
tool=$(basename "$0")
message="choregos: \`$tool\` est refusé dans un workspace d'agent."
printf '%s\n' "$message" >&2
printf 'Si cette opération est nécessaire au ticket, demande-la à un humain avec `ask_human`,\n' >&2
printf "ou signale le besoin avec \`report_finding\`. Arguments reçus : %s\n" "$*" >&2

if [ -n "${CHOREGOS_API_URL:-}" ] && [ -n "${CHOREGOS_RUN_ID:-}" ] && [ -n "${CHOREGOS_RUN_TOKEN:-}" ]; then
  payload=$(printf '{"events":[{"seq":%s,"type":"shim.refused","payload":{"tool":"%s"}}]}' \
    "$(date +%s)" "$tool")
  curl -fsS -m 3 -X POST "$CHOREGOS_API_URL/runs/$CHOREGOS_RUN_ID/events" \
    -H "Authorization: Bearer $CHOREGOS_RUN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$payload" >/dev/null 2>&1 || true
fi
exit 127
