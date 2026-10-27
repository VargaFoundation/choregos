# 0065 — Catalogues of MCP servers and skills: curated, generated from recorded sources, installed through the existing gates, never enabled by default

> **Amended by [ADR 0093](0093-an-agent-ships-as-a-plugin.md)**: a catalogue entry may come from a
> plugin, whose source and content digest are its recorded source.

- **Status**: accepted, 2026-10-15; amended by [ADR 0074](0074-catalogue-rights.md) (rights, per-project enablement)
- **Concerns**: `packages/core` (`choregos_core.catalogues`, `mcp_catalogue/`, `skill_catalogue/`),
  `tools/catalogues/`, the routes `GET /orgs/{org}/mcp-catalogue` and
  `GET /orgs/{org}/skill-catalogue` with their `install` verbs, the console (`components/catalogue/`);
  amends [ADR 0022](0022-a-federable-catalogue.md) (the federation becomes a shipped, reviewed
  file) and [ADR 0050](0050-skill-supply-chain-gate.md) (catalogue installs); builds on
  [ADR 0014](0014-a-platform-held-tool-catalogue.md) (no default tool), [ADR 0033](0033-agent-registry-and-skill-library.md)
  (skills) and [ADR 0034](0034-connectors-by-capability.md) (connectors)

## Context

A fresh organisation sees an empty `/admin/connectors` and an empty `/skills`: an administrator must
know an MCP server's URL by heart, or a repository, a path and a commit. The world publishes lists:
the official MCP Registry (more than 42,000 latest entries, of which thousands on tunnel hosts,
relays and proxies), awesome lists, skills.sh install counts. None is a curated list:

- the registry is a namespace-verified phone book (the namespace proves who owns a domain or a
  GitHub organisation, not that the server is good, alive or safe), a preview API without
  durability guarantee, and incomplete for vendors' hosted remotes (GitHub's own entry lists an OCI
  package, not its hosted endpoint);
- install counts are gameable: clones of a popular skills repository ride its name with millions of
  "installs" and a handful of stars;
- a hand-typed list rots: nine of the first hundred seed repositories were archived within months.

## Decision

1. **A catalogue is a versioned file in the image, never a live feed.** One YAML file per catalogue
   in `choregos_core` (`mcp_catalogue/catalogue.yaml`, `skill_catalogue/catalogue.yaml`), validated
   by a JSON Schema, carrying `schema_version`, a CalVer `catalogue_version`, `generated_at` and the
   `sources` it was built from. No network at runtime; the operator may merge a site file after it
   (`CHOREGOS_MCP_CATALOGUE_EXTRA`).
2. **A human curates the seed; a script resolves it.** `tools/catalogues/<x>/seed.yaml` is the
   reviewed list (adding a server or a skill is a line in a pull request). The generator resolves
   each line against recorded sources and applies **exclusion rules in code**, each drop reported
   with its reason (archived, deleted, tunnel or relay host, aggregator, arbitrary code execution,
   no open licence, binary content, publisher not allowlisted…). A directory that updates itself is
   not an access control (ADR 0014).
3. **Recorded, replayable.** `--record` (a maintainer, with network) writes the raw responses under
   `tests/fixtures/catalogues/`; the default mode replays them and **refuses the network**;
   `--check` (in CI) replays and diffs against the committed file. The output is sorted and
   byte-deterministic, so the committed file is exactly what the recording yields. `--discover`
   prints candidates outside the seed and writes nothing.
4. **Available is not enabled.** A catalogue entry is an offer. Installing it goes through the door
   that already exists, with the same governance: an MCP entry creates an ordinary org `mcp`
   connector (administrator only, secret by reference, tools born closed, groups, egress); a skill
   entry publishes a skill version through the supply-chain gate of ADR 0050, which runs again at
   install. Nothing is installed by default and nothing follows upstream by itself: an update is a
   new catalogue release plus a person clicking.
5. **What the file may say, and what the code decides.** `install` (`installable`, `needs_oauth`,
   `needs_hosting`) and the per-organisation `state` are **computed from what this release can do**,
   never stored: when OAuth connectors land, entries flip without a file change. `official` comes
   from a vendor map in the generator's overrides, never from a hand-set flag.
6. **Licence rule.** A skill enters the catalogue only under an open licence; proprietary,
   source-available or unlicensed skills (including Anthropic's `docx`, `pdf`, `pptx`, `xlsx`) are
   never bundled nor listed, and stay importable by hand through the git import, where a person
   decides. Only permissive, script-free skills are redistributed in the image, with their licence
   file verbatim and a provenance record; copyleft and high-risk skills are listed with their pin
   and fetched at install.
7. **Secrets by reference.** An MCP entry that needs a credential takes it as a reference
   (`secret_refs`), exactly as `POST /orgs/{org}/connectors` does. The catalogue knows no secret
   and no store: the reference format is the connector's, restricted to `CONNECTOR_*` environment
   names, and a sealed secret store plugs into the same field without a change here.
8. **Egress is made visible.** A deployment declares the hosts its API may reach
   (`CHOREGOS_API_EGRESS_HOSTS`); the catalogue says per entry `open | undeclared | unknown`, so the
   console warns before a silent timeout.
9. **Stdio servers are listed, not run.** Running a third-party process is a supply-chain decision
   of whoever operates the platform (ADR 0056: what runs in a pod is chosen by the operator). Such
   an entry is `needs_hosting`; hosting is a later, operator-side feature.

## Consequences

- `/admin/connectors` and `/skills` become useful on day one without enabling anything.
- The catalogue is a reviewed artifact: a monthly refresh opens a pull request whose diff (new
  versions, archived entries, stars, risk changes) a person merges.
- OAuth-only vendors (most hosted remotes) are listed but not installable until a connector can
  carry a per-user OAuth credential.
- The generator reuses the gate's own code to precompute a skill's risk; a test asserts the
  precomputed value equals the install-time assessment, so generator and gate cannot drift.
- Stars and installs are snapshots, dated in the file; they inform, they do not rank alone.

## Alternatives discarded

- **Proxy the registry live in the console**: 42,000 mostly unvetted entries, tunnels and relays, a
  preview API.
- **A runtime MCP gateway** (aggregators): re-opens the discovery ADR 0022 refuses.
- **Auto-enable "safe" servers or seed every organisation with skills**: contradicts ADR 0014 and
  the opt-in library of ADR 0033.
- **A hand-written list without generator**: it rots.
- **Proxy skills.sh**: gameable telemetry, not GitHub-only, no licence data.
- **Bundle every skill**: copyleft text in an Apache-2.0 image, high-risk scripts installed without
  a second reviewer.
