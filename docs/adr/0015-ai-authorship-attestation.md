# 0015 — A signed "AI authorship" attestation per merged change

- **Status**: proposed, 2026-09-25 — not implemented; this record fixes the shape before the code
- **Concerns**: the orchestrator (after a merge), the API (evidence export), the release chain

## Context

Every change an agent produces already leaves a trail in Choregos: the `StageResult`, the
gates and their verdicts (`gate.outcome` run events), the cost ledger, the human decisions,
the playbook checksum, the model actually used. That trail lives in **our** database. The
repository that received the change knows none of it: a merged PR looks exactly like a
human's, and an auditor who asks "which model wrote this, under which acceptance contract,
who signed it off?" has to trust our screens.

Two external facts make this worth a record now:

- **EU AI Act, Article 50** (transparency and traceability obligations, applicable since
  2026-08-02): an operator must be able to show that AI-generated content was produced by an
  AI system, and under which controls. For code, "show" means an artefact that travels with
  the code, not a dashboard.
- **`ossf/tac#628`** proposes an in-toto predicate for *AI authorship* — model, prompt
  fingerprint, acceptance contract, sign-offs. It is a draft nobody has shipped. It is also
  exactly the data we already hold.

## Decision

For every change **merged** through a Choregos workflow, the orchestrator emits an
**in-toto Statement**, signed as a **DSSE envelope**, attached to the change.

### The statement

| Field | Source in Choregos |
| :-- | :-- |
| `subject` | the merge commit (`sha256` of the commit id) and the PR URL |
| `predicateType` | the `ossf/tac#628` predicate, pinned at the draft's version; ours (`choregos.dev/ai-authorship/v1`) until it is published |
| `agent` | `run.backend`, its version from the backend registry, the executor kind |
| `model` | the model the gateway billed (`cost_ledger.model`), never the alias the project asked for |
| `prompt` | a **fingerprint**: `run.playbook_checksum` + the hash of the context pack — not the prompt itself, which may hold ticket text |
| `acceptance` | every gate the transitions declared, with its verdict and detail (`gate.outcome`) |
| `evidence` | what the runner measured (`StageResult.evidence`): tests run, lint, typing, business facts |
| `signoffs` | the human decisions on the ticket: who, when, which kind, through which channel |
| `cost` | tokens and euros per stage, from the ledger |
| `scope` | the allowed paths and the `scope_respected` verdict |

Nothing in the statement is *declared by the agent*: every field is what the platform
measured or what a human did. That is what makes it an attestation and not a claim
(ADR 0010).

### The signature

The orchestrator signs with **sigstore keyless**, using the identity Kubernetes already gives
it (the projected service-account token, OIDC-verifiable by Fulcio) — the same mechanism the
release pipeline uses for images (`cosign sign`). No long-lived signing key to rotate. A
deployment without Internet access to Fulcio/Rekor signs with the run-token key pair
(`apiSecretRef`, ES256) and says so in the envelope.

### Where it goes

1. **On the change itself**: a check-run `choregos/attestation` on the merge commit, whose
   summary carries the statement's digest and a link.
2. **In the object store**, next to the run's transcript: `runs/<run_id>/attestation.dsse.json`.
3. **In the API**: `GET /work-items/{id}/attestation` returns the envelope;
   `GET /projects/{id}/evidence-pack?since=…` returns a ZIP of every attestation, the ledger
   and the events of the window — the Article 50 evidence pack, built once and reproducible.

Verification is a CLI command: `choregos attest verify <envelope>` — Rekor inclusion,
certificate identity, and the subject against the repository.

## Consequences

- An auditor gets an artefact, not a screenshot. A repository can require the check-run to
  merge, which makes the attestation a **fourth lock** (ADR 0006) at no cost.
- The prompt is fingerprinted, not disclosed: the ticket text may be confidential.
- One more activity in the interpreter after `PR_MERGED` (`attest_change`), idempotent by
  merge commit; a replay history to record before it ships (ADR 0003).
- Non-code work (ADR 0012) has no merge commit: the subject becomes the ticket's terminal
  event, and the attestation is filed on the ticket only.

## Alternatives discarded

- **SLSA provenance only**: says *how* an artefact was built, not *who* authored the change
  nor under which acceptance contract.
- **Commit trailers** (`Co-authored-by: agent`): unsigned, editable, and silent about gates
  and sign-offs.
- **Waiting for the predicate to be finalised**: the data is ours today; the predicate type
  is one field to change.

## Not decided here

The predicate's exact schema (it follows the draft, and the draft moves), and whether
Rekor is mandatory or optional per deployment.
