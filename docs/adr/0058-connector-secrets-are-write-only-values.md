# 0058 — Connector secrets are write-only values, sealed in the database

- **Status**: accepted, 2026-10-16
- **Concerns**: connectors (`org_connectors`, `connectors`), the API (`secret_store.py`,
  `routers/org_connectors.py`, `routers/connectors.py`), the orchestrator's `load_project`, the
  contract (`openapi.yaml`), the chart (`global.secrets`), the console; amends
  [ADR 0034](0034-connectors-by-capability.md) (point 3: "references **or** sealed values"); builds on
  [ADR 0005](0005-adaptateurs-et-fakes.md) (fakes)

## Context

A connector holds no secret in clear (ADR 0034): its secret fields are **references**
(`env:JIRA_TOKEN`), resolved when the adapter is built. On a platform where secrets reach pods only
through a vault operator, that model has an operational wall: nobody can set an environment variable
on the API pod from the console. The console asked for "client secret (by reference) `env:NAME`"
and an administrator could not give it one. The SharePoint connector of the knowledge bases
(S33-04) could not be declared; the Confluence and git sources (S39-18, S39-20), the MCP servers and
the Rovo credentials (S39-08) would meet the same wall.

There was also no way to change an org connector after creating it, no rotation, and no "set /
last rotated" to show.

## Decision

1. **Two ways to give a secret field**, both kept.
   - A **value**, typed in the console: write-only. The API seals it and stores a **derived
     reference** in `secret_refs`: `store:connector/<name>/<field>` for an org connector,
     `store:project/<slug>/<kind>/<field>` for a project connector.
   - A **reference** (`env:NAME`, or a plugin scheme such as `vault:`): for operators, behind
     "use a reference instead".
2. **A `SecretStore` seam** (`apps/api/src/choregos_api/secret_store.py`): `put`, `get_many`, `meta`,
   `delete`, async, with the session and the organisation. `SealedDbSecretStore` is the default;
   `FakeSecretStore` serves `CHOREGOS_FAKES` when no key is configured and the tests. An external
   manager (Infisical, Vault) stays a **reference scheme** (`declarer_un_resolveur`). Rejected:
   writing to a vault from the console, which would put a platform credential with write rights in a
   tenant-facing process.
3. **Envelope encryption.** Each value gets a random 256-bit data key; the value is encrypted with
   AES-256-GCM; the data key is wrapped under a **KEK** (AES-256-GCM too). The additional authenticated
   data is `org_id|name|version` (prefixed `dek|` for the wrapping), so a row copied to another
   organisation, name or version does not open. KEKs come from the deployment:
   `CHOREGOS_SECRETS_KEKS="k2:<base64 of 32 bytes>,k1:<...>"`; the first is active; `kek_id` is stored
   per row. **No plaintext, no hash, no fingerprint**: the hash of a low-entropy secret leaks it.
4. **No key, no value.** Without a key a write answers 409 naming `CHOREGOS_SECRETS_KEKS`; a read of a
   `store:` reference raises `SecretIntrouvable` with the same text, so the connector shows in error;
   the API logs `secrets.key_missing` once at start-up. References keep working.
5. **Table `org_secrets`** under forced RLS like the rest of the registry (unique on `org_id, name`).
6. **Derived names only.** A user may never write a `store:` reference: `verifier_les_secrets` refuses
   it unless it is exactly the reference this connector's field derives (the idempotent re-send of a
   DTO). Otherwise a project owner could point a project connector at
   `store:connector/entra-acme/client_secret`, set `base_url` to a host they control and press "test".
7. **Resolution through one function.** `resolved_configuration(session, org_id, kind, type, config,
   secret_refs)` reads the `store:` references in one query, then calls the sync
   `configuration_resolue(..., preresolved=...)`. The core registers a `store` scheme whose resolver
   raises, so a call site that forgot the platform's resolver fails loudly. The API's call sites (test,
   discover, broker, governed action, knowledge sync) use it; the orchestrator's `load_project`
   reads the sealed values and gives them to `AdapterSet.from_connectors` as `resolved`.
8. **Redaction.** Values never appear in a DTO, the audit log or `last_error`. Every resolved value is
   remembered by `choregos_core.redaction` (process-local, bounded, values of 6+ characters), which
   `redact` and a structlog processor use to mask them wherever they would be written, whatever their
   shape. The error paths that write `last_error` or answer with an upstream message go through it.
   Any future export of connectors carries metadata and reference names only.
9. **Audit.** `connector.secret.set` and `connector.secret.clear` record the connector, the field and
   the version; `org.secret.rewrap` the number of rows. Never the value.
10. **RBAC.** Org connector secrets: `tools:grant`, the organisation's administrator, as declaring the
    connector. Project connector secrets: `connector:write`. The state (`set, rotated at, by`) is
    readable by whoever reads the connector.
11. **Rotation.** Put the new KEK first and restart; every row still opens (each carries its
    `kek_id`). Then `choregos admin secrets rewrap` (`POST /platform/secrets/rewrap`, platform
    administrator) re-wraps the data keys under the active key — the values are neither read out nor
    changed. Drop the old key from the list afterwards. Replacing a value is `PUT …/secrets/{field}`
    (version +1).
12. **The connector owns its secrets.** Deleting an org connector deletes its sealed values; switching
    a field from a value to a reference, or clearing it, deletes the value. A project connector's PUT
    keeps a typed secret its body does not name (re-saving a configuration must not wipe it).

## Consequences

- **Key custody is the deployment's**: lose the KEK and every sealed value is lost; the runbook says
  to keep it in the vault and to rotate with a list, not a swap. The database alone (a dump, a
  backup, a replica) reveals nothing.
- An organisation administrator can set and replace a secret but never read it back; the platform
  reads it only to build an adapter.
- Out of scope, here and not forgotten: restricting the `env:` references to a pattern (a project
  owner can still write `env:CHOREGOS_SESSION_SECRET` on a connector), which is a separate security
  fix; and the orphan sealed values of a deleted project.
