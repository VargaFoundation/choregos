---
title: Runbooks
description: The short list of runbooks for a Choregos instance, and the symptom each one answers.
sidebar_position: 8
---

# Runbooks

Each runbook starts from a symptom and ends with how to check it is fixed. They live in the
repository, next to the alerts that link to them.

| Symptom | Runbook |
| --- | --- |
| the API answers 5xx | [The API returns 5xx](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/runbooks/api-5xx.md) |
| a run stays `Pending` or never ends | [A run stays Pending or never ends](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/runbooks/stuck-run.md) |
| a ticket labelled for an agent did not start | [An agent-ready ticket did not start](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/runbooks/lost-webhook.md) |
| a release train is frozen | [A train is frozen and nothing departs](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/runbooks/train-frozen.md) |
| a node was lost | [Losing a node](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/runbooks/node-loss.md) |
| the database must be restored | [Restoring PostgreSQL](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/runbooks/postgres-restore.md) |
| Temporal must be backed up, restored or upgraded | [Temporal: what to back up](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/runbooks/temporal-backup.md), [Upgrading Temporal](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/runbooks/temporal-upgrade.md) |
| a secret must be rotated | [Rotating secrets](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/runbooks/secret-rotation.md) |
| the database keeps growing | [Purging runs, events and transcripts](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/runbooks/purge-runs.md) |
| the MCP gateway is down or refusing | [The MCP gateway is down](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/runbooks/mcp-gateway-down.md) |
| claude.ai cannot sign in to the MCP door | [Signing in to the MCP door with Keycloak](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/runbooks/keycloak-mcp.md) |
| the memory service lost its quorum | [Ecphoria lost its Raft quorum](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/runbooks/ecphoria-quorum.md) |

The full index: [runbooks](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/runbooks/README.md).
