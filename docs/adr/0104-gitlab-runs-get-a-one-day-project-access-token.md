# 0104 — GitLab runs get a one-day project access token

- **Status**: accepted, 2026-11-04
- **Concerns**: `packages/adapters/src/choregos_adapters/scm/gitlab.py` (`GitLabScm`), the `scm/gitlab`
  connector type and its setup procedure; builds on [ADR 0005](0005-adapters-and-fakes.md) and
  [ADR 0115](0115-the-agent-never-holds-the-run-token.md)

## Context

A project whose code lives on GitLab can use `scm: gitlab`. The `ScmAdapter` protocol asks every adapter
for `mint_token`: the credential a runner receives to push its branch. Choregos holds no broad token in a
runner (AGENTS.md). On GitHub the App mints an installation token for one repository with a short TTL. GitLab
has no equivalent: the configured token is a Maintainer's, with the `api` scope, and must stay on the platform.

GitLab offers project access tokens: scoped (`write_repository`), bound to one project, with a role. Two
facts shape the design. They expire on a **date**, not a time, so the shortest lifetime is "until tomorrow".
Each one creates a **bot user** in the project, which stays there after the token is gone unless GitLab
cleans it up.

## Decision

1. **One token per run.** `GitLabScm.mint_token` creates a project access token named
   `choregos-run-<8 hex>`, scope `write_repository` only, access level Developer (30), `expires_at` the next
   UTC date. The `ttl_s` and `scopes` the protocol passes are not honoured: the lifetime is a day and the
   scope is fixed.
2. **Clean at the next mint.** Before creating a token, the adapter lists the project's tokens and deletes
   those whose name starts with `choregos-run-` and whose `expires_at` is before today (at most 20 per call).
   Errors are logged and ignored: a cleanup failure must not stop a run. Tokens with other names are never
   touched.
3. **The configured token needs the Maintainer role and the `api` scope**, because creating and deleting
   project access tokens requires both. It never leaves the platform.
4. **The tier is a requirement.** On GitLab.com, project access tokens need Premium or Ultimate. On the
   Free tier `mint_token` fails with GitLab's `403`. Self-managed instances have no such limit. The setup
   procedure says so.

## Alternatives considered

- **One token per (project, day), reused by the day's runs.** It would spare token and bot-user churn, but
  it needs the platform to store a live secret between runs, and every run of the day would share one
  credential. The point of a per-run token is that a leak is bounded to one run.
- **The configured token.** It is a Maintainer's with `api`: it can change settings, delete branches and
  mint more tokens. Far too broad for a runner.
- **A group deploy token.** It spans every project of the group and cannot be scoped to one repository's
  writes in the way a project token can: too broad.

## Consequences

- A run token is valid for up to a day after its run ends, because the protocol has no revoke. Its blast
  radius is one project's repository writes (including pushes to unprotected branches), for at most a day.
- Expired tokens are deleted at the next mint on the same project, so a project that stops running
  workflows keeps its last tokens and their bot users until someone deletes them. Whether GitLab deletes
  the bot user together with a deleted token is not proven here.
- Each run adds an entry to the project's access tokens list and a bot user. A project that runs very many
  runs per day can hit GitLab's per-project limits on tokens; the cleanup only removes expired ones.
- Auto-merge sends both `auto_merge` and the deprecated `merge_when_pipeline_succeeds`, since recent GitLab
  reads the first and older versions the second.

## Residual risk and follow-up

No revoke at run end: a finding proposes adding `revoke_token` to the `ScmAdapter` protocol (a protocol
change, out of scope here) so a run's token can be deleted when the run ends instead of at the next mint.
