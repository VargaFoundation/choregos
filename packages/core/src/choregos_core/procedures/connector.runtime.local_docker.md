---
id: connector.runtime.local_docker
title: Run agent stages in local Docker (development)
applies_to: {connector: runtime/local_docker}
audience: operator
version: 1
reviewed: 2026-10-16
egress: []
fields:
  network: "the Docker network the runner joins, choregos_default by default"
---

## Before you start

- Development only: it needs a Docker daemon on the machine that runs the orchestrator.
- Use it with `docker compose up` (`dev/compose.yaml`). On a cluster use `k8s_job` or `tekton`.

## At the host

Docker must be installed and the orchestrator must be able to call it. The runner image is pulled on the
first run, which can take several minutes (the pull has a 15 minute timeout).

## Permissions

Whoever can reach the Docker socket controls the host. Do not use this executor on a shared machine.

## In Choregos

Chart or compose settings: `runner.executor: local_docker`. The only option is `network`, the Docker
network the runner container joins so it can reach the API.

## Test it

Start a run: `docker ps` shows a container named `choregos-run-<id>`.

## When it fails

- The run cannot reach the API: the container is not on `network`.
- `Cannot connect to the Docker daemon`: the orchestrator has no access to the socket.
- A very slow first run: the runner image is still being pulled.
