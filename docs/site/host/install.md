---
title: Install with Helm
description: Install Choregos on Kubernetes with its Helm chart, give it a first values file, create the first organisation and token, and check it works.
sidebar_position: 2
---

# Install with Helm

**What you will be able to do:** install an instance, sign in, and hand the first token to a person.

**Before you start:** the [prerequisites](index.md#prerequisites) in place, and the
[secrets](secrets.md) created in the namespace (or in your vault, with External Secrets).

## 1. Write your values

A first `my-values.yaml` for an instance at `https://choregos.example.com`, with external
dependencies:

```yaml
global:
  environment: prod
  publicUrl: https://choregos.example.com
  apiUrl: https://choregos.example.com
  database:
    host: choregos-pg-rw.choregos-data.svc
    name: choregos
    user: choregos
    secretRef: choregos-db
  temporal:
    address: temporal-frontend.choregos-temporal.svc:7233
  gateway:
    url: http://litellm.choregos-gateway.svc:4000
    secretRef: choregos-gateway-secrets
  objectStore:
    url: s3://choregos-artefacts
    secretRef: choregos-object-store
  oidc:
    issuer: https://sso.example.com/realms/acme
    clientId: choregos
    secretRef: choregos-oidc
  bootstrap:
    org: acme
    orgName: ACME
    admins: "alice@acme.example,bob@acme.example"
choregos-api:
  ingress:
    hostnames: ["choregos.example.com"]
choregos-web:
  ingress:
    hostnames: ["choregos.example.com"]
```

- `publicUrl` and `apiUrl` are what the API builds every address from: the sign-in callback, the
  Integrations page's commands, the links in notifications. Set both to `https://…`.
- `bootstrap` creates the first organisation and makes those people its administrators at every
  start, never changing what exists. There is no default organisation.
- In `staging` and `prod`, the chart refuses to render with an embedded dependency, generated
  secrets, the development sign-in or the demonstration fakes.

For a single-node bench with every dependency embedded, start from the chart's
`values/local.yaml` overlay instead.

## 2. Install

```shell
helm upgrade --install choregos oci://ghcr.io/vargafoundation/charts/choregos \
  --version 0.27.2 --namespace choregos --create-namespace -f my-values.yaml
```

The chart's version is the product's version; the images
(`ghcr.io/vargafoundation/choregos-api`, `-web`, `-orchestrator`, `-tools`, `-runner`) follow it, are
multi-arch and signed. The database migrations run as a Helm hook before the API starts.

Install the admission policies once per cluster, from a checkout of the repository at the same tag:

```shell
kubectl apply -k infra/policies
```

## 3. Check it

```shell
kubectl -n choregos get pods
curl -s https://choregos.example.com/healthz
```

**What you should see:** the API, the console and the worker deployments `Running`, and
`{"status":"ok"}`. Open `https://choregos.example.com` and sign in as one of the `bootstrap.admins`.

## 4. The first token

Once signed in, a person creates their own tokens (**My account**, or `choregos tokens create`).
Before anyone has signed in, the first one comes from the server side:

```shell
kubectl -n choregos exec deploy/choregos-api -- choregos-admin tokens create --email alice@acme.example
choregos login --api-url https://choregos.example.com --token chg_... --org acme
```

## What can go wrong

| Symptom | Likely cause |
| --- | --- |
| the render fails naming `agent-sandbox` | the agent-sandbox operator's CRDs are missing |
| the API does not start, naming `CHOREGOS_SESSION_SECRET` | the session secret is missing or shorter than 32 bytes |
| every agent call answers `Signature verification failed` | the orchestrator and the API do not share `run-token-private-key` |
| sign-in answers `503` | the identity provider's discovery document cannot be read, or names another issuer |
| sign-in loops back to the login page | the URLs say `https` but the ingress also serves plain HTTP: redirect it |
| the first run that writes a file fails | the object store has no credentials or a wrong endpoint |

The full list of values: the
[deployment reference](../../deployment.md).
