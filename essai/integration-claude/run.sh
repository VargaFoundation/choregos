#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Essai S15-07 : un VRAI Claude Code se connecte à la porte MCP du locataire dev (ADR 0030), liste
# les outils, ouvre un ticket — puis on vérifie en REST, sans croire le modèle.
#
#   CHOREGOS_DEV_URL      (défaut http://choregos.internal.dev.diametral.com)
#   CHOREGOS_DEV_TOKEN    jeton `*` d'un humain (préparation et vérifications REST) — jamais écrit ici
#   ESSAI_PROJET          `org:slug` d'un projet à tracker interne (défaut diametral:essai-it4it)
#
# Le jeton MCP est frappé pour l'essai (portée mcp:write, lié au projet, un jour), écrit dans un
# fichier 0600 du répertoire temporaire, et révoqué à la fin.
set -euo pipefail
URL=${CHOREGOS_DEV_URL:-http://choregos.internal.dev.diametral.com}
PROJET=${ESSAI_PROJET:-diametral:essai-it4it}
: "${CHOREGOS_DEV_TOKEN:?jeton de portée * requis, voir en tête du script}"
API=(curl -sS --max-time 30 -H "Authorization: Bearer $CHOREGOS_DEV_TOKEN" -H "Content-Type: application/json")
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
HORODATAGE=$(date -u +%Y%m%dT%H%M%SZ)

echo "1. la porte refuse sans jeton, et dit comment s'authentifier"
code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$URL/mcp" -H 'Content-Type: application/json' -d '{}')
[ "$code" = "401" ] || { echo "  attendu 401, reçu $code (l'Ingress route-t-il /mcp vers l'API ?)"; exit 1; }
echo "  401 ✓"

echo "2. un jeton mcp:write lié à $PROJET, pour un jour"
"${API[@]}" -X POST "$URL/api/v1/me/tokens" \
  -d "{\"name\":\"essai-claude-code-$HORODATAGE\",\"scopes\":[\"mcp:write\"],\"project\":\"$PROJET\",\"expires_in_days\":1}" \
  > "$TMP/jeton.json"
JETON_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$TMP/jeton.json")
trap '"${API[@]}" -X DELETE "$URL/api/v1/me/tokens/$JETON_ID" >/dev/null || true; rm -rf "$TMP"' EXIT
python3 - "$TMP" "$URL" "$PROJET" <<'PY'
import json, os, sys
tmp, url, projet = sys.argv[1:4]
jeton = json.load(open(f"{tmp}/jeton.json"))["token"]
config = {"mcpServers": {"choregos": {"type": "http", "url": f"{url}/mcp/projects/{projet}",
          "headers": {"Authorization": f"Bearer {jeton}"}}}}
chemin = f"{tmp}/mcp.json"
with open(os.open(chemin, os.O_CREAT | os.O_WRONLY, 0o600), "w") as f:
    json.dump(config, f)
PY
echo "  jeton $JETON_ID ✓"

echo "3. Claude Code se connecte et liste les outils"
claude -p "Use the choregos MCP server: call describe_workflow, then reply with the workflow name only." \
  --mcp-config "$TMP/mcp.json" --strict-mcp-config --allowedTools "mcp__choregos__describe_workflow" \
  --output-format stream-json --verbose > "$TMP/liste.jsonl"
python3 - "$TMP/liste.jsonl" <<'PY'
import json, sys
init = next(json.loads(l) for l in open(sys.argv[1]) if '"subtype":"init"' in l.replace(" ", ""))
serveurs = {s["name"]: s["status"] for s in init.get("mcp_servers", [])}
outils = [t for t in init.get("tools", []) if t.startswith("mcp__choregos__")]
assert serveurs.get("choregos") == "connected", serveurs
assert "mcp__choregos__create_work_item" in outils, outils
print(f"  connecté, {len(outils)} outils : {', '.join(sorted(o.removeprefix('mcp__choregos__') for o in outils))} ✓")
PY

echo "4. Claude Code ouvre un ticket"
TITRE="Essai MCP $HORODATAGE"
claude -p "Use the choregos MCP server to open a work item titled '$TITRE' with start=false. Reply with its key only." \
  --mcp-config "$TMP/mcp.json" --strict-mcp-config --allowedTools "mcp__choregos__create_work_item" \
  --output-format json > "$TMP/creation.json"
echo "  réponse du modèle : $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("result","")[:120])' "$TMP/creation.json")"

echo "5. vérification en REST, sans croire le modèle"
"${API[@]}" "$URL/api/v1/projects/$PROJET/work-items?q=$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))' "$TITRE")" > "$TMP/tickets.json"
"${API[@]}" "$URL/api/v1/me/tokens" > "$TMP/jetons.json"
python3 - "$TMP" "$TITRE" "$JETON_ID" <<'PY'
import json, sys
tmp, titre, jeton_id = sys.argv[1:4]
tickets = [t for t in json.load(open(f"{tmp}/tickets.json"))["items"] if t["title"] == titre]
assert len(tickets) == 1, f"ticket introuvable : {titre}"
jeton = next(t for t in json.load(open(f"{tmp}/jetons.json")) if t["id"] == jeton_id)
assert jeton["last_used_at"], "le jeton MCP n'a jamais servi"
print(f"  ticket {tickets[0]['tracker_key']} ({tickets[0]['state']}) ✓")
print(f"  dernier usage du jeton : {jeton['last_used_at']} par {jeton.get('last_client')} ✓")
PY
echo "essai réussi — le jeton $JETON_ID est révoqué à la sortie"
