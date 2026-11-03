# 0103 — The secret KEKs sit behind a key wrapper

- **Status**: accepted, 2026-11-03
- **Concerns**: `apps/api/src/choregos_api/secret_store.py` (`KeyWrapper`, `EnvKeyWrapper`,
  `SealedDbSecretStore`); builds on [ADR 0058](0058-connector-secrets-are-write-only-values.md)

## Context

Connector secrets are sealed with an envelope: a random data key per value, wrapped under a
key-encryption key (KEK) read from `CHOREGOS_SECRETS_KEKS`. The wrapping was written inline against
that keyring, so an organisation that must keep its KEKs in a KMS or a Vault transit engine could not
do so without forking the store.

## Decision

1. `KeyWrapper` is a protocol: `active_id`, `wrap(dek, aad) -> (wrapped, nonce, kek_id)` and
   `unwrap(wrapped, nonce, aad, kek_id) -> dek`. `seal`, `unseal` and `SealedDbSecretStore` use it.
2. `EnvKeyWrapper` over the existing `Keyring` is the default. It writes the same bytes as before
   (AES-256-GCM, additional data `b"dek|" + aad`), so existing rows open and nothing changes for
   today's deployments. `seal` and `unseal` still accept a `Keyring`.
3. `SealedDbSecretStore(keyring=None, wrapper=None)` takes a wrapper; `rewrap` selects the rows whose
   `kek_id` differs from `wrapper.active_id`, so moving to or between wrappers is the existing rotation.
4. An extension supplies a KMS or Vault-transit wrapper (through `set_secret_store`, or a
   `SealedDbSecretStore` built with a wrapper). A wrapper's `kek_id` fits `org_secrets.kek_id`, at most
   32 characters.
5. Core ships **no** KMS integration and no KMS client dependency.

## Rejected

Shipping AWS KMS, GCP KMS or Vault clients in the community distribution: each is a dependency, a
credential path and a failure mode that most deployments do not need, and the choice of provider belongs
to the operator. The seam gives them the hook without the weight.

## Consequences

The seam is proven with a fake KMS in tests; no real KMS is exercised. A wrapper whose `unwrap` calls a
network service makes `get_many` slower and able to fail; that is the wrapper's contract to handle.
