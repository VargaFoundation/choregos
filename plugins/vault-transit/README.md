# `tranibor-vault-transit`: a HashiCorp Vault Transit key wrapper

An **extension** (a wheel loaded through the `choregos.plugins` entry point, [ADR 0093](../../docs/adr/0093-an-agent-ships-as-a-plugin.md)),
not a plugin: it ships no catalogue content. It closes the "no native Vault adapter" gap **without**
breaking [ADR 0103](../../docs/adr/0103-secret-keks-sit-behind-a-key-wrapper.md), which says the core ships
no KMS or Vault client: the wrapper lives here, behind the core's `KeyWrapper` seam, and the core gains no
dependency on it (`tests/architecture/test_core_ships_no_vault_client.py`).

## Where it lives, and why

`plugins/vault-transit`, next to `plugins/investment-research`: the repository's one existing practice
for a wheel with its own `pyproject.toml` that a deployment installs beside the core and that registers
through `choregos.plugins`, as a member of the uv workspace, released from the core's repository so that a
change to a seam and the extension that calls it land in one pull request. `integrations/` holds editor
and agent-host adapters, `examples/` holds teaching material, `packages/` is what the core itself
imports. The directory name `plugins/` is the repository's, not a claim that this is a plugin in the
manifest sense.

## What it does

- `VaultTransitKeyWrapper` implements `KeyWrapper`: `wrap` is transit `encrypt` of the data key,
  `unwrap` is `decrypt`. `kek_id` is `<key>-v<N>` (at most 32 characters, so the key name is at most 24).
- **Rotation**: `vault write -f transit/keys/<key>/rotate`, then `choregos admin secrets rewrap`. The rows
  move with transit `rewrap`: the data key is never in the platform's memory. Rows sealed under the
  environment keyring (`TRANIBOR_SECRETS_KEKS`, kept as the `legacy` wrapper while it is still set) move
  to Vault by the same command.
- **Auth**: the pod's service account (Vault `kubernetes` auth, role + JWT, lease renewed by logging in
  again below 20 % of the lease, and after a 403), or a token (given, or read from a file a Vault Agent
  rewrites). Tokens are registered with the platform's redaction.
- `bind_context` sends the row's additional data as the transit `context` (key created with `derived=true`).
- `FakeVault` (`fake.py`) is a scriptable transport that really encrypts; tests never need a Vault.

Configuration is the environment, documented in `extension.py` and in
[Deployment, "Connector secrets sealed under Vault"](../../docs/deployment.md#connector-secrets-sealed-under-vault-transit).

## Limits

- The `KeyWrapper` protocol is synchronous. The client is async (`httpx.AsyncClient`) on a private event
  loop in a daemon thread, but `put` and `get_many` wait for it: each blocks the API's event loop for one
  Vault round trip per secret. Only `rewrap` is async end to end. Making the protocol async is a change of
  the seam, not done here.
- Tested against `FakeVault`, written from Vault's documented API. No real Vault, no real Kubernetes
  auth backend, no Vault namespaces or token renewal semantics beyond what the fake models.
- The wheel is built from this repository (`uv build --package tranibor-vault-transit`); the release
  workflow does not publish it, nor an image carrying it.
