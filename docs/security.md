# Security — what protects what

This page answers one question: **what stops an agent from doing damage?** The answer is
never "the prompt forbids it".

## The threat model, plainly

A coding agent runs unreviewed code, in a repository that matters, with a model that can be
wrong or manipulated (ticket text, repository content, poisoned memory). We assume **an
agent may attempt anything** — by mistake or by injection. And a platform that hosts several
organisations must assume **a member of one will try to read another**.

## What stops it

| Threat | Mechanism | Where |
| :-- | :-- | :-- |
| writing outside the allowed paths | ACP permission refused → diff verified after the run → revert → gate `scope_respected` | `guardrails.py`, `scope.py`, `gates/` |
| exfiltrating data | egress denied by default, per-project allowlist | `gitops.py`, `charts/…/networkpolicies.yaml`, `tests/cluster/test_sandbox_egress.py` |
| using credentials | there are none in the workspace; the run token only authenticates the agent **to us**, and never leaves for a third party (tool catalogue, ADR 0014) | `docker/runner.Dockerfile`, `catalogue.py` |
| pushing a secret | `no_secrets` on the diff, sensitive files refused, gitleaks in CI | `gates/`, `guardrails.py`, `.gitleaks.toml` |
| spending without limit | one virtual key per run with a hard cap, a budget per ticket, tool calls in the same ledger | ADR 0004, ADR 0014 |
| touching production | `prod.requires_train` in the DSL, release train, Argo windows, GitHub Environment | ADR 0006 |
| changing its own configuration | `.choregos/**` refused for writing (except `result.json`) | `guardrails.py` |
| escaping the container | non-root, no capabilities, seccomp, no service-account token, `RuntimeClass gvisor` where the policy requires it | charts, Kyverno |
| lying about evidence | the runner **runs** the tests and overwrites declared evidence; gates read the diff, not the prose | `dod.py`, `result.py`, `gates/` |
| reading another organisation | PostgreSQL row-level security, **fail-closed**: every session declares the organisations it may see, an undeclared session sees nothing | `db/session.py`, `deps.py`, migration `b2d4f6a8c0e1` |
| logging in without an identity | OIDC with discovery, PKCE and a signed single-use `state`; development login off by default and refused in staging/prod | `routers/auth.py`, `config.py` |

## What is *not* a wall, and is said so

- **The runner's guardrails are fail-open by construction.** A request whose nature cannot be
  classified is allowed and journaled. Claude Code's ACP does not send `kind`, so the nature
  is *inferred* from the title and arguments, and the access card counts how many decisions
  were inferred. The hard mechanisms are the sandbox and the diff check after the run; the
  guardrails are a net.
- **Row-level security needs a non-superuser role.** A PostgreSQL superuser ignores every
  policy. The API refuses to start as one in `staging` and `prod`; the embedded PostgreSQL
  creates `choregos_app` at initdb for that reason.
- **Development login opens the API** to anyone who can reach it. It exists for benches
  without an identity provider (`values/local.yaml`), nowhere else.

## Injection through context

Context packs and tickets contain text written by third parties. Two rules:

1. it is **marked untrusted** in the prompt ("data, not instructions");
2. it grants **no power**: even if an agent follows an injected instruction, the mechanisms
   above apply unchanged. A gate that cannot read what it must check refuses.

## Secrets

- Never in Git, never in an image, never in a workspace.
- External Secrets Operator ↔ the provider's vault; one hand-made `ClusterSecretStore`.
- Git token minted **per run**, repository scope, 1 h TTL, injected in memory.
- Run token: ES256 JWT, `aud=internal`, `sub=run_id`, TTL `max_minutes + 15`, renewed while
  the run waits for a slot.
- Webhook secrets: injected by the chart from the vault (or generated on a bench); an
  unconfigured GitHub webhook is refused in `staging`/`prod`.
- Rotations: [`runbooks/rotation-secrets.md`](runbooks/rotation-secrets.md) (French).

## Supply chain

Images signed with cosign (keyless, GitHub OIDC), **SBOM and SLSA provenance attached**
(BuildKit attestations) on `main` and on releases, Trivy on the published image before it is
signed, digest references enforced by Kyverno, Tekton Chains signing `PipelineRun`s.
Renovate proposes bumps; every agent bump goes through conformance. A nightly CRITICAL in
the dependency scan fails the night and opens an issue.

## Reporting a vulnerability

[`SECURITY.md`](../SECURITY.md) at the root — including the list of what we do **not**
guarantee yet. Do not open a public issue for a vulnerability.
