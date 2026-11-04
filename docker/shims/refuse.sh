#!/usr/bin/env bash
# Refusal shim: an agent has no reason to call these tools from its workspace.
# The refusal is explicit and logged — it is defence in depth, not the guarantee
# (the guarantee is the NetworkPolicy, the absence of a credential and the diff check).
tool=$(basename "$0")
message="choregos: \`$tool\` is refused in an agent workspace."
printf '%s\n' "$message" >&2
printf 'If this operation is needed for the ticket, ask a human for it with `ask_human`,\n' >&2
printf "or report the need with \`report_finding\`. Arguments received: %s\n" "$*" >&2

if [ -n "${CHOREGOS_API_URL:-}" ] && [ -n "${CHOREGOS_RUN_ID:-}" ] && [ -n "${CHOREGOS_RUN_TOKEN:-}" ]; then
  payload=$(printf '{"events":[{"seq":%s,"type":"shim.refused","payload":{"tool":"%s"}}]}' \
    "$(date +%s)" "$tool")
  curl -fsS -m 3 -X POST "$CHOREGOS_API_URL/runs/$CHOREGOS_RUN_ID/events" \
    -H "Authorization: Bearer $CHOREGOS_RUN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$payload" >/dev/null 2>&1 || true
fi
exit 127
