# 0140 — Conversation evals and user feedback

- **Status**: proposed, 2026-11-10
- **Concerns**: `packages/contracts/schemas/plugin-evals.schema.json` (a conversation suite), the eval runner,
  the promotion gate of channels, a feedback route on a conversation's answer; amends
  [ADR 0071](0071-an-agent-version-reaches-production-through-channels.md); builds on
  [ADR 0020](0020-trajectory-evals.md), [ADR 0133](0133-a-plugin-ships-an-assistant-an-organisation-installs-it-for-an-audience.md)
  and [ADR 0134](0134-every-person-has-a-personal-space-conversations-are-durable.md)

## Context

An agent version reaches production through channels, gated by its evals (ADR 0071). Those evals score a
stage's result. An assistant's quality is a conversation: which tools it calls first, what it never calls,
whether each figure it states is cited, and what a person thinks of the answer. Without a suite that can
say "this version broke a rule", promoting a version is a guess; without feedback, nothing tells the owners
it got worse.

## Decision

1. **A conversation suite** is a new kind of `plugin-evals` file. A case is a scenario of user turns, tool
   results simulated by the sources' fakes, deterministic assertions (`must_call`, `must_not_call`,
   `must_cite`, `must_not_contain`, and an order between calls), and a rubric scored by a judge model with a
   threshold per criterion. The assertions run first and are exact; the judge comes second (lot 2).
2. **It counts in promotion.** An assistant's agent version does not reach `production` on the channel an
   installation follows below the suite's thresholds, as agents' evals gate today.
3. **Feedback.** A thumb up or down on each answer, with an optional comment. A thumb down makes a
   **candidate case**: pseudonymised, proposed to the installation's owners, never added to a suite
   automatically.

## Alternatives rejected

- **Judging only with a model.** A rule such as "never read X before Y" is a fact of the journal, not an
  opinion.
- **Adding negative feedback to the suite automatically.** Personal data would enter a test corpus without a
  person deciding.

## Consequences

- The fakes of every source an assistant uses become part of its test bench.
- A conversation keeps its agent revision (ADR 0134), so feedback is attached to the version that answered.
