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

The platform holds **one** pair at a time. The orchestrator signs run tokens with
`run-token-private-key`; the API verifies them with `run-token-public-key`, or, when that entry is
absent, with the public half derived from the private key (`apps/api/src/tranibor_api/security.py`).
There is no second key and no key id (`kid`): a token signed by the old key fails verification
under the new one. Nothing reads a `-next` entry. A token lives `max_minutes + 15` minutes, and a
run's agent holds its token for as long as the run lasts, so the rotation is a **replacement in a
quiet window**, not a transition.

1. Pick a window with no run in flight (`choregos runs list`, or the runs that are not terminal on
   the console), and stop the intake of new ones if you can. A run still active when the key is
   replaced gets `401` ("Signature verification failed") on its next `/internal` call and fails.
   Those runs can be retried afterwards; nothing else is lost.
2. Replace `run-token-private-key` in `choregos/api` with the new key. Replace
   `run-token-public-key` too, or delete it so that it is derived: a stale public key next to a new
   private key makes the API reject every token the orchestrator signs.
3. Let External Secrets sync, then restart **together** the API and the orchestrator workers (the
   keys are read from the environment at start): a pair with one process on each key rejects every
   token.
4. Start one real ticket and check that its run passes. A `401` on `/internal` in the API logs
   after the restart means a process still runs on the old key.
## Connector secrets key (`secrets-keks`)

`TRANIBOR_SECRETS_KEKS` is a list `id:base64`; each sealed secret records the id of the key that
wrapped it, so a rotation is a list, never a swap ([ADR 0058](../adr/0058-connector-secrets-are-write-only-values.md)).

1. Generate a key: `python -c "import os,base64;print('k2:'+base64.b64encode(os.urandom(32)).decode())"`.
2. In the vault, put it **first**: `k2:<new>,k1:<old>`. Redeploy the API and the workers: new
   writes are sealed under `k2`, every existing value still opens under `k1`.
3. Re-wrap: `choregos admin secrets rewrap` (platform administrator). It re-wraps the data keys
   under `k2`; the values are never read out. It answers the number of rows changed and is safe to
   repeat.
4. When it answers `0` and a connector test passes, **keep `k1` in the list** until the oldest
   database backup taken before step 3 has left the retention window (WAL-G base backups and WAL
   archive). Then remove it and redeploy.

Never drop `k1` before step 3: a value sealed under a key the list no longer holds is unreadable
(the connector shows `error`, and the message names the missing key id). Step 4's wait is the same
rule applied to a restore: a backup from before the rewrap still holds rows wrapped under `k1`, and
restoring it with a list that dropped `k1` loses them. An old key that only opens costs nothing to
keep. The vault (Infisical) is itself backed up, and its secret history may still hold `k1`; do not
rely on that, it is a second restore under pressure.

## Check it is fixed

- An end-to-end run passes (`make demo` is not enough: start a real S ticket).
- No 401 in the API logs on the `/internal` and `/webhooks` routes.
