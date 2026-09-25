# Rotating secrets

## Schedule

| Secret | Period | Effect of a failed rotation |
| :-- | :-- | :-- |
| private key of the `choregos-bot` GitHub App | 90 d | no more webhooks, PRs or run tokens |
| LiteLLM `master_key` | 90 d | no model call at all: every run fails |
| the API's ES256 JWT keys (run tokens) | 30 d | runs in flight can no longer post their result |
| webhook secrets (GitHub, generic) | 180 d | webhooks are rejected with 401 |

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

## Check it is fixed

- An end-to-end run passes (`make demo` is not enough: start a real S ticket).
- No 401 in the API logs on the `/internal` and `/webhooks` routes.
