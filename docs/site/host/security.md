---
title: Security model
description: What protects what on a Choregos instance - organisations, agent sandboxes, network, tokens, secrets, audit - and what is not a wall.
sidebar_position: 7
---

# Security model

Choregos assumes **an agent may attempt anything**, by mistake or by injection, and that a member of
one organisation will try to read another's. This page is the digest; the full model, with what is
not guarded, is the
[security reference](../../security.md).

## Organisations are isolated in the database

Every request declares the organisations it may see, and PostgreSQL's row-level security refuses the
rest; a request that declares nothing sees nothing. That is why the API must connect as a role that
is **not** a superuser (a superuser ignores row-level security): in `staging` and `prod` it refuses to
start otherwise.

## Agents run in a sandbox

- One pod per run: non-root, no capabilities, a seccomp profile, no service-account token, required
  by the Kyverno policies in `infra/policies`. Set `global.runner.runtimeClass` to run every agent pod
  under gVisor or Kata; a project's policy can demand gVisor.
- A run holds only its own short-lived token. It never holds a provider key (the model gateway gets
  the run's own capped key), nor a connector's credential (the API calls tools for it).
- Writes outside the work item's allowed paths, and to the denied paths of the project's policy, are
  refused; the result is checked by the workflow's gates, not trusted.

## Network: three layers

1. The organisation's network list, which an administrator manages (**Admin › Network**), under a
   ceiling the operator sets (`global.egress.ceiling`).
2. A per-run egress proxy (`global.egress.enabled: true`), the wall for host, port and connection rate.
3. Your cluster's network policies.

The proxy cannot see inside TLS: methods, paths and headers are invisible to it. Allow hosts, not
URLs.

## Tokens and their scopes

| Scope | Opens | Never opens |
| --- | --- | --- |
| `*` | the REST API and the CLI | the MCP and A2A doors |
| `mcp:read`, `mcp:write` | the MCP door, optionally one project | the REST API |
| `a2a` | the A2A door, optionally one project | the REST API |
| an application's token | the A2A door, on its project, 365 days at most | everything else |

Tokens are stored as digests and shown once. **A decision** (approving a specification, a merge, a
release) is taken by a person signed in to the console, never by a token, an MCP client or an A2A
caller, and the sensitive ones ask that person to sign in again.

## Secrets

Connector secrets typed in the console are sealed with AES-256-GCM under a key the deployment holds
(`secrets-keks`): a database dump reveals nothing, and losing the key loses the values
([Secrets](secrets.md)).

## Audit

Every sign-in, token, door call, decision and change of rights is in the audit log (**Admin ›
Audit**), under the same organisation isolation as the rest.

## Supply chain

The images are signed with cosign (keyless), with an SBOM and SLSA provenance attached; the Kyverno
policies can require digest references. Hosted agents' images can be required to carry a trusted
signature (`global.images.hostedCosign: enforce`).

## Reporting a vulnerability

Follow [SECURITY.md](../../../SECURITY.md); do not
open a public issue.
