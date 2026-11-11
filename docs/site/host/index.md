---
title: Host Choregos
description: For operators. What a Choregos instance is made of, what it needs, and how to install, secure, upgrade and back it up on Kubernetes.
sidebar_position: 1
---

# Host Choregos

These pages are for the people who run a Choregos instance on Kubernetes. They are a digest: each one
says what to do and links to the full
[deployment reference](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/deployment.md) for
every value and every edge case.

**What you will be able to do:** install an instance with Helm, connect it to your identity provider,
provide its secrets, upgrade it, back it up, and know what protects what.

## What you run

| Component | What it does |
| --- | --- |
| **API** (`choregos-api`) | the REST API, the MCP and A2A doors, webhooks, sign-in; the only component that reads the object store |
| **Console** (`choregos-web`) | the web interface |
| **Orchestrator workers** (`choregos-orchestrator`) | the Temporal workers that move work items and start agent runs, one deployment per queue |
| **Tools** (`choregos-tools`) | the platform's MCP tool server for runs |
| **Runner** | the image every agent run starts in, one sandboxed pod per run |

They depend on PostgreSQL, Temporal, a model gateway (LiteLLM), an S3-compatible object store and an
OIDC identity provider. Each dependency can be embedded by the chart, for a bench, or external, for
production (the default).

## Prerequisites

| Need | Minimum | Notes |
| --- | --- | --- |
| Kubernetes | 1.24 | 1.29 for hosted agents, which run as native sidecars |
| Helm | 3.12 | the chart is published as an OCI artifact |
| PostgreSQL | 15 | an operator such as CloudNativePG in production; the API must not connect as a superuser |
| Temporal | 1.22 | a cluster in production |
| An OIDC provider | | any issuer with a discovery document; Keycloak in our deployments |
| The agent-sandbox operator | v1.0.6 | backs the default executor; the chart does not render without its CRDs |
| An S3-compatible object store | | for file artefacts; AWS S3, MinIO, RustFS |
| An ingress | | Gateway API `HTTPRoute` by default, or a classic `Ingress` |
| External Secrets Operator | | used by default to bring the API's secrets from your vault; can be turned off |
| Kyverno | 1.12 | for the admission policies shipped in `infra/policies` |

Recommended in production: a model gateway with real provider keys (cost is measured and capped
there), gVisor as a `RuntimeClass` for agent pods, and a memory service (Ecphoria).

## Try it on your machine first

To see Choregos before installing it on a cluster, run it with Docker Compose from the repository:
`git clone https://github.com/VargaFoundation/choregos.git`, `cp .env.example .env`, then
`docker compose up`, and open `http://localhost:3000`. It listens on `127.0.0.1` only and is not a
deployment.

## The pages

1. [Install with Helm](install.md): the chart, a first values file, the first organisation and token.
2. [Identity](identity.md): single sign-on, roles from your identity provider's groups, OAuth for the
   MCP door.
3. [Secrets](secrets.md): what the chart expects, and the key that seals connector secrets.
4. [Upgrades](upgrades.md): the order of operations, migrations, what to read before.
5. [Backups and restore](backups.md): what to back up, and restoring to one instant.
6. [Security model](security.md): what protects what, and what does not.
7. [Runbooks](runbooks.md): the short list for when something goes wrong.
