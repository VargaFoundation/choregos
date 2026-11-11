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
| the API answers 5xx | [The API returns 5xx](../../runbooks/api-5xx.md) |
| a run stays `Pending` or never ends | [A run stays Pending or never ends](../../runbooks/stuck-run.md) |
| a ticket labelled for an agent did not start | [An agent-ready ticket did not start](../../runbooks/lost-webhook.md) |
| a release train is frozen | [A train is frozen and nothing departs](../../runbooks/train-frozen.md) |
| a node was lost | [Losing a node](../../runbooks/node-loss.md) |
| the database must be restored | [Restoring PostgreSQL](../../runbooks/postgres-restore.md) |
| Temporal must be backed up, restored or upgraded | [Temporal: what to back up](../../runbooks/temporal-backup.md), [Upgrading Temporal](../../runbooks/temporal-upgrade.md) |
| a secret must be rotated | [Rotating secrets](../../runbooks/secret-rotation.md) |
| the database keeps growing | [Purging runs, events and transcripts](../../runbooks/purge-runs.md) |
| the MCP gateway is down or refusing | [The MCP gateway is down](../../runbooks/mcp-gateway-down.md) |
| claude.ai cannot sign in to the MCP door | [Signing in to the MCP door with Keycloak](../../runbooks/keycloak-mcp.md) |
| the memory service lost its quorum | [Ecphoria lost its Raft quorum](../../runbooks/ecphoria-quorum.md) |

The full index: [runbooks](../../runbooks/README.md).
