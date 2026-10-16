# Rotating secrets

## Schedule

| Secret | Period | Effect of a failed rotation |
| :-- | :-- | :-- |
| private key of the `choregos-bot` GitHub App | 90 d | no more webhooks, PRs or run tokens |
| LiteLLM `master_key` | 90 d | no model call at all: every run fails |
| the API's ES256 JWT keys (run tokens) | 30 d | runs in flight can no longer post their result |
| webhook secrets (GitHub, generic) | 180 d | webhooks are rejected with 401 |
| the connector secrets key (`secrets-keks`) | 365 d | none if rotated with the list below; losing it loses every sealed connector secret |

## Principle: two valid keys during the transition

Run tokens live up to 2 h. A rotation "in one go" breaks the runs in flight. The API
therefore accepts **two** verification keys during the rotation window.

## GitHub App

1. Generate a new private key in the App settings.
2. Write it into the vault: `choregos/api` → `github-app-private-key-next`.
3. Deploy: the API tries the new key, then the old one.
4. After 24 h without errors, delete the old key on GitHub's side **then** in the vault.

```bash
kubectl -n choregos-system logs deploy/choregos-api | grep -c "github.*401"   # must stay 0
```

### The App's OAuth client secret

Needed only when the instance has several organisations ([ADR 0073](../adr/0073-github-app-installations-are-discovered-not-typed.md)).
Generate a new client secret in the App settings (GitHub allows two at once), write it into the vault
(`choregos/api` → `github-app-client-secret`), let External Secrets sync, restart the API, then delete
the old secret on GitHub. A wrong secret shows as `403` on `GET /github/setup` ("GitHub refused the
authorization code").

## LiteLLM `master_key`

1. Create the new key in LiteLLM (`/key/generate` with the admin role).
2. Update `choregos/gateway` → `master-key` in the vault.
3. Redeploy the API and the workers (the chart's `checksum/config` forces the restart).
4. **Do not** revoke the old key before the runs in flight are finished: their virtual keys
   were minted with it.

## The API's JWT keys

```bash
# Generate an ES256 pair
openssl ecparam -genkey -name prime256v1 -noout -out run-token.pem
openssl ec -in run-token.pem -pubout -out run-token.pub
```

1. Write the new pair into `choregos/api` (`run-token-private-key-next`).
2. Deploy: new tokens are signed with the new key, old ones stay verifiable.
3. After `max_minutes + 15` (2 h is enough), promote the new pair and delete the old one.

## Connector secrets key (`secrets-keks`)

`CHOREGOS_SECRETS_KEKS` is a list `id:base64`; each sealed secret records the id of the key that
wrapped it, so a rotation is a list, never a swap ([ADR 0058](../adr/0058-connector-secrets-are-write-only-values.md)).

1. Generate a key: `python -c "import os,base64;print('k2:'+base64.b64encode(os.urandom(32)).decode())"`.
2. In the vault, put it **first**: `k2:<new>,k1:<old>`. Redeploy the API and the workers: new
   writes are sealed under `k2`, every existing value still opens under `k1`.
3. Re-wrap: `choregos admin secrets rewrap` (platform administrator). It re-wraps the data keys
   under `k2`; the values are never read out. It answers the number of rows changed and is safe to
   repeat.
4. When it answers `0` and a connector test passes, remove `k1` from the list and redeploy.

Never drop `k1` before step 3: a value sealed under a key the list no longer holds is unreadable
(the connector shows `error`, and the message names the missing key id). Back the list up with the
database: the database without the key is useless, and the key without the database is too.

## Check it is fixed

- An end-to-end run passes (`make demo` is not enough: start a real S ticket).
- No 401 in the API logs on the `/internal` and `/webhooks` routes.
