# 0021 — SPIFFE identities behind the run token

- **Status**: proposed, 2026-09-25 — not implemented; optional, platform-team install
- **Concerns**: `apps/api/src/choregos_api/security.py`, the `k8s_job` and `agent_sandbox` executors, the chart

## Context

A run authenticates to the internal API with a JWT (ES256, `aud=internal`, `sub=run_id`,
issuer from settings, TTL = stage budget + 15 minutes), minted by the API at stage start and
mounted into the Job. The runner sidecar holds it; the agent never sees it. P1-3 tests its
expiry, audience, issuer and key pair. Two things stay true and are listed as P2 debt:

- the token **cannot be revoked** before it expires; a stage abandoned by `await_run`
  leaves a valid token for up to 15 minutes after the Job is deleted;
- the token is a **secret in a Kubernetes Secret**, one per run, with the lifecycle problems
  that come with it (cleanup, RBAC on `secrets`, etcd at rest).

SPIFFE gives a workload an identity by **attestation** (which pod, which labels, which
service account), not by a mounted secret: a SPIRE agent on the node issues short-lived
X.509 or JWT SVIDs, rotates them, and stops issuing the moment the workload is gone.

## Decision

1. **A run's identity may be a SPIFFE ID**:
   `spiffe://<trust-domain>/choregos/<org>/<project>/run/<run_id>`, issued by SPIRE to the
   runner pod through workload attestation on the Job's labels (`choregos.io/run-id`,
   `choregos.io/project`) and its service account.
2. **The internal API accepts two credentials**: the run JWT (default, unchanged) and a
   JWT-SVID with `aud=internal` whose SPIFFE ID's `run_id` matches the run being written.
   The check is the same `RunClaims`; only the verifier differs (SPIRE's bundle endpoint
   instead of our key pair).
3. **Revocation becomes deletion.** Abandoning a run deletes its Job; the SVID is not
   renewed and dies within its rotation window (minutes, not the JWT's TTL). No list of
   revoked tokens to keep.
4. **mTLS where it pays**: the runner → LiteLLM and runner → catalogue hops use the X.509
   SVID when SPIRE is present, so the virtual key (ADR 0004) is bound to a workload, not to
   whoever holds the string.

## Conditions

- SPIRE is cluster-scoped (agent daemonset, server, registration): a platform team installs
  it, a tenant of a shared cluster cannot. Same posture as ADR 0016: an option, not a
  default. `tekton` and `local_docker` executors keep the JWT.
- The chart gains `global.spiffe.trustDomain`; when unset, nothing in this ADR is rendered
  and the JWT path is the only one. `garde.yaml` refuses `spiffe` with an executor that
  cannot attest.
- The conformance suite (`tests/cluster`) proves that a deleted Job's identity stops
  authenticating within the rotation window before the capability is announced.

## Consequences

- The last per-run secret disappears where SPIRE exists; the security page stops
  documenting a 15-minute window.
- The access report (P0-3) can name the workload that made each call, not only the run.

## Alternatives discarded

- **A revocation list for run JWTs**: works, adds a lookup to every internal call and a
  table to keep clean; it fixes revocation and nothing else.
- **Kubernetes service account tokens with projected audiences**: closer to hand, but the
  identity would be the executor's service account, not the run's, and the API would have to
  trust the executor's mapping from pod to run — the thing SPIFFE attests for us.
