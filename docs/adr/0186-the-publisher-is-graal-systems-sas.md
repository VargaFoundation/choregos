# 0186 — The publisher is GRAAL.SYSTEMS SAS; the Varga Foundation is not named by the product

- **Status**: accepted, 2026-11-18 (decided by the product owner on 2026-11-18: the products no longer
  belong to or point at the Varga Foundation); S80-10 implements it
- **Concerns**: `NOTICE`, `LICENSE`, `docs/TRADEMARK.md`, the console's attribution line, the
  publishing identity (`packages/core/src/tranibor_core/publishing.json`), the default plugin source,
  `tests/licences/test_no_varga.py`; amends [ADR 0024](0024-two-editions.md),
  [ADR 0048](0048-the-console-owns-its-primitives.md),
  [ADR 0064](0064-a-theme-is-data-the-console-applies.md),
  [ADR 0156](0156-the-publishing-identity-is-one-file.md) and
  [ADR 0176](0176-the-core-is-named-tranibor.md)

## Context

The core was published "by the Varga Foundation": the copyright line, the console's footer, the
licence headers, the security contact, the commit author, the plugin repository and its registry, and
the default plugin source's name (`varga`) all said so, and several ADRs made the attribution a
requirement. The product owner decided that the products no longer belong to, or depend on, that
foundation. The publisher is GRAAL.SYSTEMS SAS, which already owns the repositories
(`graalsystems/tranibor`, `graalsystems/tranibor-agents`, `graalsystems/ecphoria`).

## Decision

1. **The publisher is GRAAL.SYSTEMS SAS.** `NOTICE` reads "Copyright 2026 GRAAL.SYSTEMS SAS and the
   Tranibor contributors"; the licence stays Apache-2.0. The console's footer, which is still not
   themable (ADR 0048), says "Tranibor by GRAAL.SYSTEMS SAS · Apache 2.0", and a renamed instance
   "<product>, built on Tranibor by GRAAL.SYSTEMS SAS". The trademark policy, the READMEs, the
   documentation, the CLI and API strings and the code comments name GRAAL.SYSTEMS SAS where they named
   the foundation as owner, publisher or author.
2. **The publishing identity moves with it** (ADR 0156): the security contact is
   `security@graal.systems`, the commit address `tranibor-bot@graal.systems`, the plugin repository
   `https://github.com/graalsystems/tranibor-agents` and its index
   `oci://ghcr.io/graalsystems/tranibor-agents/index:latest`. `plugins/offline/` (the archives and their
   lock) is history, no longer derived: it records the index and the identity that signed the archives,
   and `make plugins-lock` replaces them once the new index has published them.
3. **The default plugin source is `tranibor`.** A new migration renames `org_plugins.source` `varga` to
   `tranibor` (idempotent, reversible, leaves a collision alone), and `PluginsConfig` resolves the old
   name to the new source, so a deployment whose values still say `varga` keeps working.
4. **What stays, and why.** The images published before the move stay in
   `ghcr.io/vargafoundation/*` (the platform's runner, Ecphoria's 0.2.5 image and the plugin index and
   servers published so far, which exist nowhere else): the admission policies and the compose files keep
   naming them, beside the new namespace, until the images are republished. ADRs and plan rows keep their
   history. The demo's sample organisation, formerly `varga`, is `northwind`.
5. **A test keeps it that way.** `tests/licences/test_no_varga.py` fails when "Varga" appears in a
   tracked file outside an explicit allowlist: the ADRs, the plan, the migrations, the offline plugin lock,
   and the few files of point 4 (each listed with its reason).

## Consequences

- ADRs 0024, 0048, 0064, 0156 and 0176 keep their text as history and carry a note that this ADR
  amends them; "built on Choregos by Varga Foundation" is replaced by GRAAL.SYSTEMS SAS everywhere it
  ships.
- ghcr packages of `graalsystems` are private until the owner makes them public: an installation that
  cannot pull `ghcr.io/graalsystems/tranibor-agents` keeps the offline archives.
- The old documentation address is not redirected (ADR 0185).
