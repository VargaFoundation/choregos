# 0066 — An organisation's egress allowlist: decided in the app, enforced by the platform's clients, bounded by the deployment

- **Status**: accepted, 2026-10-16
- **Concerns**: the API contract (`/orgs/{org}/egress…`, `allow_egress`, the 409 `egress_required`),
  the database (`org_egress_rules`), every outbound client the platform runs for a tenant, the
  chart (`global.egress`), the console's "add" gestures; amends
  [ADR 0050](0050-skill-supply-chain-gate.md) §4 (the skill-source allowlist stays a trust list of
  repositories; the network is this list) and, once the MCP catalogue lands,
  [ADR 0065](0065-the-mcp-catalogue.md) (its `egress` field reads this list and the ceiling)

## Context

Nothing checked the host of a call the platform itself makes for a tenant: the MCP broker and
discovery, the git skill fetch, the knowledge sources (web, git, Confluence, Graph), the A2A card
fetch. An organisation administrator who declared `https://evil.example/mcp` made the API pod call
it. On a locked-down cluster the network stops it, silently, after a 30 s timeout, and the person
who wanted a new MCP server learns nothing about why.

The three places that decide where Choregos may connect today are not an organisation allowlist:
a deployment-wide declaration that restricts nothing, the skill-source allowlist (repositories a
project owner may import from, not hosts), and the project policy for runner pods
(`sandbox.network.allow_domains`).

The cluster's network policy lives outside the application, in the platform's own repository. The
application must not hold a credential to it, and must not try to patch it.

## Decision

1. **One list per organisation, in the database.** `org_egress_rules`: `host` (exact, or `*.` as the
   leftmost label only; lowercased, IDNA-encoded), `ports` (default 443), `used_by` (a subset of
   `api`, `workers`, `runners`), `status` (`requested`, `allowed`, `refused`, `revoked`), `reason`,
   `origin`, who and when for the request and the decision. At most one live row per host; forced
   RLS like `org_secrets`. Every change is audited (`egress.rule.create`, `egress.rule.revoke`,
   `egress.request.create|approve|refuse`).
2. **Decided in the app, by the administrator.** Opening a host needs `tools:grant`, which only the
   organisation administrator holds. Anyone in the organisation may *request* a host; the request
   waits in the inbox of those who can decide (20 open requests per person at most).
3. **Every governed "add" opens its host first.** The endpoint that adds an MCP connector, a git
   skill import, a knowledge source (and, as they land, the catalogue installs and a remote agent)
   takes `allow_egress: bool = false`. When a host it needs is missing: without the flag, a 409
   `egress_required` carrying the verdicts (the console opens its modal); with the flag and
   `tools:grant`, the hosts are added **in the same transaction** as the add, with the origin and a
   reason, audited; with the flag and without the right, a 403 naming the administrators. Hosts are
   derived by the server, never typed: a connector type declares them
   (`ConnectorTypeSpec.egress`, defaulting to its URL-shaped configuration fields and the schema's
   defaults), a git import gives GitHub's API and tarball hosts (or GitLab's), a web or git source
   gives its URL's host. `POST /orgs/{org}/egress/check` returns them without writing.
4. **Enforced by the clients, not by the endpoints.** `GuardedTransport` wraps the `httpx`
   transport of every client that calls out for a tenant and checks **every request, redirects
   included**, against the policy the caller put in a context variable. A refusal happens before
   the inner transport sees the request. In `enforce`, a call with **no policy in context fails
   closed**: a call site that forgot to set one shows up in the tests. A subprocess cannot be
   wrapped, so `git` sources check their host up front and forbid redirects.
   **Never guarded**: the deployment's own dependencies (gateway, Temporal, memory, object store,
   the OIDC issuer, the GitHub App calls of the platform's VCS). The operator configures them, not
   a tenant.
5. **Bounded by a deployment ceiling.** `CHOREGOS_EGRESS_CEILING` maps a pod family to the host
   patterns the cluster's network lets through. A host the organisation allowed but the ceiling
   excludes is refused **before it is sent**, with a message naming the platform change, instead of
   a timeout. An empty or absent family is *unknown*: the ceiling is not checked and the list alone
   governs.
6. **Modes.** `CHOREGOS_EGRESS_MODE` is `enforce`, `warn` (log `egress.would_refuse` and pass) or
   `off`. The code default is `enforce`; under `CHOREGOS_FAKES=1` it is `warn`, so the fakes suites
   need no rules. The chart default is `enforce`.
7. **The app says what it cannot do.** When the ceiling excludes or does not know a host, the
   verdict carries a `platform_change`: a snippet rendered from the operator's template
   (`CHOREGOS_EGRESS_PLATFORM_CHANGE`: `format`, `template`, `issueUrl`, `owner`, `entries`), a
   neutral Cilium `toFQDNs` fragment by default. The console shows it with the sentence: *Choregos
   cannot change the cluster's network policy: it holds no credential to the platform's repository
   and must not hold one.* The app can prefill an issue URL; it cannot open a pull request.
8. **Nothing breaks on upgrade.** The migration seeds `allowed` rules (`origin: migration`) for the
   hosts the organisation already calls: its connectors, its web and git knowledge sources, its
   skill sources (with GitHub's API and tarball hosts). The administrator reviews and revokes them.

## Consequences

- An administrator adds an MCP server or a git skill in one gesture and sees the host they open.
  Anyone else sees who can, and asks.
- A host the cluster blocks is named at the click, not discovered after a timeout.
- The REST connectors (Jira, GitLab, Slack…) are not guarded yet. They join in a second phase,
  first in `warn`, because their hosts are seeded by a migration and a wrong seed would break
  trackers.
- Runner pods: rules whose `used_by` contains `runners` join every run's `allow_domains` (a later
  PR; activities only, no workflow change).
- The ceiling is declared by the operator and mirrors the cluster; nothing verifies the mirror. A
  stale ceiling gives a wrong `open`, and the cluster still decides.

## Alternatives rejected

- **Let the app patch the cluster policy**, through a service account or a token on the platform
  repository. A tenant process with write access to the platform repository is a privilege
  escalation, and the GitOps controller would revert a live patch.
- **Keep an environment list as the only source.** Every MCP install would become a deployment
  pull request.
- **Reuse the skill-source allowlist.** It lists repositories, not hosts and ports, and only
  concerns skills.
- **Reuse the project's `allow_domains`.** MCP calls and skill fetches happen in the API, for the
  organisation, not in a project's runner.
- **A cluster-wide egress proxy for the API.** A valid operator choice, covered by the same ceiling
  semantics, but not the application's to deploy.
