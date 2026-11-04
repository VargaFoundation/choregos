# Trial S15-07 - a real Claude Code against the dev tenant's MCP door

Played on **2026-10-05** against `http://choregos.internal.dev.diametral.com` (Choregos **0.14.0**,
`choregos-deploy` #21), from a workstation on the VPN, with Claude Code **2.1.289**.

```bash
CHOREGOS_DEV_TOKEN="$(cat ~/.config/choregos/dev-token)" trials/claude-integration/run.sh
```

A human's `*` token is only used to prepare and to check over REST; it never enters Claude Code's
configuration. For the trial the script mints an `mcp:write` token, bound to the project
`diametral:essai-it4it`, valid for one day, written to a 0600 file in a temporary directory,
and **revokes** it on exit (checked afterwards: it is no longer listed).

## What was observed

| Step | Expected | Observed |
|---|---|---|
| 1 | `POST /mcp` without a token: 401 | 401, `WWW-Authenticate: Bearer realm="choregos"` |
| 2 | An `mcp:write` token bound to the project | created (`01a10ca9-...`) |
| 3 | `claude -p ... --mcp-config ... --strict-mcp-config`: the `choregos` server is `connected` and announces `create_work_item` | `connected`, **21 tools**: the door's 7 and the 14 of the project's IT4IT ontology (`finding_search`, `host_get`, `action_open_infra_pr`...) |
| 4 | Claude Code opens a work item through `create_work_item` | the model answers `ESSAI-IT4IT-2` |
| 5 | Check **over REST**, without believing the model | work item `ESSAI-IT4IT-2` exists (`inbox`); the token was used (`last_used_at` 15:23:27Z) and `last_client` is `claude-code/2.1.289 (sdk-cli)` |

The ontology's tools pass the project's door with the person's rights (S15-03);
no tool decides (`action_*` proposes, the decision is taken in the console).

## What the trial does not prove

- **OAuth**: claude.ai and hosted clients call from their vendor's network, and the
  dev tenant has no public address (S15-08 checks the door against a fake IdP).
- **Writing a proposal through MCP** on the dev tenant: the trial only calls `create_work_item`.
- A client other than Claude Code (Cursor, VS Code): the snippets of the Integrations page are
  checked by the console's tests, not played here.

## What remains on the dev tenant

The work item `ESSAI-IT4IT-2` (title "MCP trial 20261005T...Z", not started), a trace of the trial.
