#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Trial S15-07: a REAL Claude Code connects to the MCP door of the dev tenant (ADR 0030), lists
# the tools, opens a work item - then we check over REST, without believing the model.
#
#   CHOREGOS_DEV_URL      the address of the tenant's console (e.g. https://choregos.example)
#   CHOREGOS_DEV_TOKEN    a human's `*` token (set-up and REST checks) - never written here
#   TRIAL_PROJECT         `org:slug` of a project with an internal tracker (e.g. varga:essai-it4it)  # lang: fr-ok
#
# The MCP token is minted for the trial (scope mcp:write, bound to the project, one day), written to
# a 0600 file in the temporary directory, and revoked at the end.
set -euo pipefail
# No tenant is assumed: Choregos is an open project, the trial runs against yours.
URL=${CHOREGOS_DEV_URL:?the console address is required, see the top of the script}
PROJECT=${TRIAL_PROJECT:?an org:slug project is required, see the top of the script}
: "${CHOREGOS_DEV_TOKEN:?a token of scope * is required, see the top of the script}"
API=(curl -sS --max-time 30 -H "Authorization: Bearer $CHOREGOS_DEV_TOKEN" -H "Content-Type: application/json")
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)

echo "1. the door refuses without a token, and says how to authenticate"
code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$URL/mcp" -H 'Content-Type: application/json' -d '{}')
[ "$code" = "401" ] || { echo "  expected 401, got $code (does the Ingress route /mcp to the API?)"; exit 1; }
echo "  401 ✓"

echo "2. an mcp:write token bound to $PROJECT, for one day"
"${API[@]}" -X POST "$URL/api/v1/me/tokens" \
  -d "{\"name\":\"trial-claude-code-$TIMESTAMP\",\"scopes\":[\"mcp:write\"],\"project\":\"$PROJECT\",\"expires_in_days\":1}" \
  > "$TMP/token.json"
TOKEN_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$TMP/token.json")
trap '"${API[@]}" -X DELETE "$URL/api/v1/me/tokens/$TOKEN_ID" >/dev/null || true; rm -rf "$TMP"' EXIT
python3 - "$TMP" "$URL" "$PROJECT" <<'PY'
import json, os, sys
tmp, url, project = sys.argv[1:4]
token = json.load(open(f"{tmp}/token.json"))["token"]
config = {"mcpServers": {"choregos": {"type": "http", "url": f"{url}/mcp/projects/{project}",
          "headers": {"Authorization": f"Bearer {token}"}}}}
path = f"{tmp}/mcp.json"
with open(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600), "w") as f:
    json.dump(config, f)
PY
echo "  token $TOKEN_ID ✓"

echo "3. Claude Code connects and lists the tools"
claude -p "Use the choregos MCP server: call describe_workflow, then reply with the workflow name only." \
  --mcp-config "$TMP/mcp.json" --strict-mcp-config --allowedTools "mcp__choregos__describe_workflow" \
  --output-format stream-json --verbose > "$TMP/list.jsonl"
python3 - "$TMP/list.jsonl" <<'PY'
import json, sys
init = next(json.loads(l) for l in open(sys.argv[1]) if '"subtype":"init"' in l.replace(" ", ""))
servers = {s["name"]: s["status"] for s in init.get("mcp_servers", [])}
tools = [t for t in init.get("tools", []) if t.startswith("mcp__choregos__")]
assert servers.get("choregos") == "connected", servers
assert "mcp__choregos__create_work_item" in tools, tools
print(f"  connected, {len(tools)} tools: {', '.join(sorted(o.removeprefix('mcp__choregos__') for o in tools))} ✓")
PY

echo "4. Claude Code opens a work item"
TITLE="MCP trial $TIMESTAMP"
claude -p "Use the choregos MCP server to open a work item titled '$TITLE' with start=false. Reply with its key only." \
  --mcp-config "$TMP/mcp.json" --strict-mcp-config --allowedTools "mcp__choregos__create_work_item" \
  --output-format json > "$TMP/creation.json"
echo "  the model's reply: $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("result","")[:120])' "$TMP/creation.json")"

echo "5. check over REST, without believing the model"
"${API[@]}" "$URL/api/v1/projects/$PROJECT/work-items?q=$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))' "$TITLE")" > "$TMP/items.json"
"${API[@]}" "$URL/api/v1/me/tokens" > "$TMP/tokens.json"
python3 - "$TMP" "$TITLE" "$TOKEN_ID" <<'PY'
import json, sys
tmp, title, token_id = sys.argv[1:4]
items = [t for t in json.load(open(f"{tmp}/items.json"))["items"] if t["title"] == title]
assert len(items) == 1, f"work item not found: {title}"
token = next(t for t in json.load(open(f"{tmp}/tokens.json")) if t["id"] == token_id)
assert token["last_used_at"], "the MCP token was never used"
print(f"  work item {items[0]['tracker_key']} ({items[0]['state']}) ✓")
print(f"  last use of the token: {token['last_used_at']} by {token.get('last_client')} ✓")
PY
echo "trial succeeded - the token $TOKEN_ID is revoked on exit"
