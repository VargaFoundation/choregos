---
title: Secrets
description: The secrets a Choregos instance needs, where it reads them, how to generate them, and the key that seals connector secrets.
sidebar_position: 4
---

# Secrets

The chart never writes production secrets: it expects them in the namespace, created by hand or
brought from your vault by the External Secrets Operator (on by default, from the
`ClusterSecretStore` named by `global.externalSecrets.storeRef`).

**What you will be able to do:** create every secret the instance needs, and rotate them.

## What the instance reads

| Secret | Keys | Read by |
| --- | --- | --- |
| `choregos-api-secrets` (`global.apiSecretRef`) | `session-secret`, `run-token-private-key`; optional `run-token-public-key`, `session-secret-previous`, `secrets-keks` | API; the run-token key and `secrets-keks` by the orchestrator too |
| the same, for GitHub | `github-app-id`, `github-app-private-key`, `github-webhook-secret`; optional `github-app-client-id`, `github-app-client-secret` | API and orchestrator |
| `global.oidc.secretRef` | `client-secret` | API |
| `global.database.secretRef` | `url` | API, orchestrator |
| `global.gateway.secretRef` | `master-key` | orchestrator |
| `global.objectStore.secretRef` | `access-key`, `secret-key` | API |
| `global.memory.tokenSecret` | `token` | orchestrator |

## Generate them

```shell
openssl rand -base64 48
openssl ecparam -name prime256v1 -genkey -noout
python3 -c "import os,base64;print('k1:'+base64.b64encode(os.urandom(32)).decode())"
```

- **`session-secret`** (the first command) signs the session cookie and the sign-in handshakes. It is
  required; on a deployed instance it must be at least 32 bytes.
- **`run-token-private-key`** (the second) signs the tokens of agent runs: the orchestrator mints
  them, the API checks them, so **both** must read the same key.
- **`secrets-keks`** (the third) seals the connector secrets people type in the console. Without it,
  typing a secret answers `409`.

```shell
kubectl -n choregos create secret generic choregos-api-secrets \
  --from-literal=session-secret="$SESSION_SECRET" \
  --from-file=run-token-private-key=run-token.pem \
  --from-literal=secrets-keks="$KEKS"
```

With External Secrets, put the same properties under `choregos/api` in your vault; add
`global.secrets.keksInExternalSecret: true` once `secrets-keks` exists there.

## The key that seals connector secrets

`secrets-keks` is a list `id:base64`, the first entry sealing and the others only opening. **Losing it
loses every sealed value**, and a database backup alone reveals none of them. Keep it in your vault,
backed up.

To rotate it: put the new key first (`k2:…,k1:…`), redeploy, run `choregos admin secrets rewrap` as
the platform administrator, and remove the old key only once no database backup older than the rewrap
can still be restored.

## Rotating the others

- **Session secret:** move the old value to `session-secret-previous`, put the new one in
  `session-secret`, redeploy, and remove the previous one after 8 hours.
- **Run-token key:** the API and the orchestrator read the same Secret, so both take the new key at
  their restart. A run started before can no longer post its result: rotate it in a quiet window, when
  no run is in flight.

The step-by-step:
[secret rotation runbook](../../runbooks/secret-rotation.md).
`global.devSecrets: true` generates everything for a bench; it is refused in `staging` and `prod`.
