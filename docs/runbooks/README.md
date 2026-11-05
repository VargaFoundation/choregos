# Runbooks

A runbook is read at 3 a.m. by someone who did not write the code. Each one starts with
*how to know it is this*, then gives the commands, then says *how to check it is fixed*.

English is the reference language since 2026-09-24; the French originals were deleted in 0.21
(ADR 0098). Seven runbooks were
renamed in 0.21 (ADR 0098). Their former file names were redirect stubs until 0.23.0; the stubs were
removed in 0.24.0 (S45-49).

| Runbook | When |
| :-- | :-- |
| `train-frozen.md` | a train is frozen and nothing departs |
| `api-5xx.md` | the API returns 5xx |
| `postgres-restore.md` | data loss, PITR restore — the only one played on a cluster so far |
| `ecphoria-quorum.md` | Ecphoria lost its Raft quorum |
| `secret-rotation.md` | rotating the GitHub App, the gateway `master_key`, the JWT keys |
| `temporal-upgrade.md` | upgrading Temporal |
| `rename-tenant-names.md` | bringing a tenant created before `joiners-leavers` 2.0.0 forward to English names |
| `purge-runs.md` | the database grows, transcripts pile up |
| `stuck-run.md` | a run stays `Pending` or never ends |
| `lost-webhook.md` | an `agent-ready` ticket did not start |
| `node-loss.md` | a node disappears (spot eviction, failure) and work does not resume |
| `aca-live.md` | checking the Azure Container Apps executor against a real subscription |
| `temporal-backup.md` | what Temporal holds, how to back it up, what a loss costs and how to reconcile after a restore |
| `keycloak-mcp.md` | claude.ai or Claude Code over OAuth cannot connect to the MCP door |
| `live-eval-matrix.md` | measuring rtk and headroom on real models before a default-on decision |
| `refresh-catalogues.md` | refreshing the shipped MCP server and skills catalogues from their sources |
| `mcp-gateway-down.md` | the organisation's MCP gateway is down, or refuses tool calls |
