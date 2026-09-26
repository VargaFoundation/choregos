# Security policy

## Reporting a vulnerability

Write to **security@varga.foundation**, or open a private security advisory on GitHub. Please do
not open a public issue: we would rather fix before we publish.

Include the version or commit, what you observed, how to reproduce it, and the impact you
estimate. We acknowledge within 3 working days and give a first assessment within 10.

## Supported versions

The project is at **0.x**: only the latest minor receives security fixes, and there is no
long-term support branch yet. A supported-version table will mean something once a 1.0 exists;
until then, upgrading is the fix.

## What we consider a vulnerability

- Any way for an agent to write outside its scope without being caught.
- Any secret leak: into a workspace, an image, a log, a transcript.
- Any bypass of the three production locks (merge queue, release train, guardrails).
- Any privilege escalation into the cluster from a runner.
- Any access to another organisation's data (RLS, RBAC, audit).

## What we do not consider a vulnerability

- An agent producing bad code: that is what CI and review are for.
- A model following an injected instruction **without that granting it any power**: the
  mechanisms (scope, absence of credentials, gates) apply just the same.
- `CHOREGOS_FAKES=1`, which is not meant for production.

## What we do, and where to check it

| Claim | Where it is enforced |
| :-- | :-- |
| Images are signed (cosign keyless) and admitted by digest | `.github/workflows/release.yml`, `infra/policies/images.yaml` |
| An SBOM and a `mode=max` provenance attestation ship with every release image | `release.yml`, job `images` |
| A CRITICAL from Trivy blocks publication — on `main` **and** on the released image | `ci.yml` job `images`, `release.yml` job `manifest` (`exit-code: 1`) |
| The nightly dependency scan **blocks**, and its failure opens an issue | `nightly.yml` (`exit-code: 1`, job `alerte`) |
| Secrets never enter git: gitleaks is a gate on every pull request | `ci.yml` job `secrets` |
| Organisations are isolated by fail-closed PostgreSQL RLS, audit included | migrations `b2d4f6a8c0e1`, `c3e5a7f9b1d4`, `db/session.py` |
| Secrets rotate on the schedule in `docs/runbooks/rotation-secrets.md` | that runbook |

## What we do **not** guarantee, stated rather than discovered

- **Runner guardrails are a net, not a wall.** What they cannot classify is allowed and logged,
  unless the project policy sets `sandbox.unknown_requests: reject`. The hard mechanisms are the
  sandbox (NetworkPolicy, per-project egress proxy, non-root, no service-account token) and the
  after-the-fact diff check.
- **The development login** (`/auth/login?as=`) is off by default and refused in staging and
  production, by the API *and* by the chart. An installation that turns it on is open to anyone
  who can reach the API.
- **RLS assumes the API does not connect as a PostgreSQL superuser** — a superuser ignores every
  policy. The API refuses to start that way in staging and production.
- **Platform administration is over-broad**: `Principal.is_platform_admin()` is true for any
  `org_admin` of any organisation. Harmless on a single-organisation install; a tenant crossing
  from the second one on. Tracked in `docs/plan/BLOCKERS.md`.
- **A run token cannot be revoked** before it expires (`max_minutes + 15`).
- **Audit is not tamper-evident**, and its retention is documented in a runbook but applied by
  no code.

This section is deliberately longer than the one above it. Where a page and the code disagree,
the code wins — and the page is a bug.
