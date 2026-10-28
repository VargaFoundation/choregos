# 0095 — SkillSpector scans every skill a person publishes, offline, on a local OSV mirror

- **Status**: accepted, 2026-10-27 — implemented by S44-02; amends
  [ADR 0050](0050-skill-supply-chain-gate.md) (the gate's scan)
- **Concerns**: `choregos_api.services.skill_scanner`, `choregos_api.scanners` (`osv_mirror`,
  `skillspector_offline`), the publish path of `services/skills.py`, the API image's `skillspector`
  stage, the settings `CHOREGOS_SKILL_SCANNER`, `CHOREGOS_SKILLSPECTOR_PYTHON`,
  `CHOREGOS_SKILL_SCAN_TIMEOUT_S`, `CHOREGOS_OSV_MIRROR_URL`, `CHOREGOS_OSV_MIRROR_PATH`,
  `CHOREGOS_OSV_REFRESH_HOURS`, `CHOREGOS_OSV_MAX_AGE_HOURS`

## Context

ADR 0050's scan reads patterns: injection phrases, hidden Unicode, encoded blobs, a download piped
into a shell. It says so itself: "patterns, not a sandbox". NVIDIA SkillSpector (Apache-2.0,
reviewed on 2026-10-26) reads what patterns miss: a script's AST, taint from a secret to a socket,
YARA rules, MCP tool poisoning and least privilege, and dependencies with known vulnerabilities
(OSV). Its supply-chain check calls `api.osv.dev` with a hard-coded URL and no offline switch, and
its optional LLM pass sends the files to a provider.

The owner's rule (2026-10-26): no external dependency at scan time, or else a local cache of the
external resource.

## Decision

1. **SkillSpector runs on every version a person publishes**: a zip, the console, git, or an update.
   It runs after ADR 0050's scan, before any row is written. It does not run on the platform's own
   catalogue, which is reviewed with its release, or on a git preview, which shows ADR 0050's tier
   only.
2. **In its own interpreter, as a child process.** Its dependencies (LangGraph, YARA, the provider
   SDKs) are not the platform's. The API image carries it in `/opt/skillspector`, pinned to the
   commit of v2.13.1. The child gets the bare environment of the knowledge converter (no database
   URL, no key), a `$HOME` of its own, and a timeout (120 s).
3. **Offline.** The scan is `--no-llm`. Every HTTP client SkillSpector builds gets a transport that
   answers OSV's two calls (`POST /v1/querybatch`, `GET /v1/vulns/{id}`) from the local mirror and
   refuses everything else. Nothing leaves the pod at scan time.
4. **The local OSV mirror** is one SQLite file per API replica, on its `/tmp` volume (about 1 GB). It
   is rebuilt every 24 h from OSV's published PyPI and npm archives (`<base>/<ecosystem>/all.zip`;
   `CHOREGOS_OSV_MIRROR_URL`, which an operator can point to their own copy, or leave empty in an
   air-gapped deployment). The mirror matches versions itself, because OSV's API does that
   server-side: listed versions, then `ECOSYSTEM` and `SEMVER` ranges (`introduced`, `fixed`
   excluded, `last_affected` included), PEP 440 for PyPI and SemVer for npm. Checked by hand against
   `api.osv.dev` on 2026-10-27 with three queries: same answers, same identifiers.
5. **Its findings join the gate's** as `skillspector:<rule>`. HIGH and CRITICAL are critical: the
   version is high risk and waits for a second administrator (ADR 0050). MEDIUM is a warning, LOW is
   information. SkillSpector's own `DO_NOT_INSTALL` makes the version high risk, and its `CAUTION`
   makes it at least medium. The scan raises the risk, never lowers it.
6. **Failures hold the version, never pass it.** A scanner that is missing, crashes or times out is
   a critical `scanner_failed`, so the version waits for review. A mirror older than 72 h, or absent,
   gives `osv_mirror_missing`, and an incomplete analysis gives `scanner_incomplete`. Both are
   warnings: the dependency check did not happen, and the reviewer is told.
7. **`CHOREGOS_SKILL_SCANNER`**: `skillspector` in the API image, `off` elsewhere (where SkillSpector
   is not installed), which leaves ADR 0050's scan alone.

## Consequences

- More versions wait for a second administrator: SkillSpector's HIGH rules are broad. That is the
  intended trade, since a false positive costs a review and a miss costs a compromised agent.
- The API image grows by about 190 MB. A new SkillSpector release is a deliberate bump: its rules
  change, and `apps/api/tests/test_skill_scanner.py` runs against the real tool where
  `CHOREGOS_TEST_SKILLSPECTOR_PYTHON` names it.
- Each API replica downloads about 260 MB a day from OSV's bucket (a platform call, not an
  organisation's), and the mirror holds about 1 GB on the replica's `/tmp`.
- Publishing is slower: SkillSpector takes 3 to 10 s per version.
- The mirror covers PyPI and npm only, which is what SkillSpector's dependency check reads.
- The image's Trivy scan gets one exception (`trivy-secret.yaml`): the Anthropic SDK under
  `/opt/skillspector` documents `{"type": "service_account"}`, which the `gcp-service-account`
  rule reads as a key. The exception names that directory only; a real key anywhere else still
  fails the build.

## Alternatives rejected

- **SkillSpector as a library in the API's environment**: its dependency tree would become the
  platform's, and its upgrades with it.
- **A self-hosted `osv.dev`**: Google Cloud Datastore and its indexers, far heavier than a file.
- **OSV's live API**: refused by the owner's rule.
- **Its LLM pass**: it sends the skill's files to a provider, and the gate's verdict would depend on
  a model's mood.
