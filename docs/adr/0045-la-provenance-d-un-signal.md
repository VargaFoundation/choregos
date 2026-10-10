# 0045 — Every signal says where it comes from: declared, observed, inferred or unknown

- **Status**: accepted, 2026-10-10
- **Concerns**: the stage result contract (`Evidence.measured`), the runner, the web console;
  builds on [ADR 0010](0010-les-gates-sont-des-mecanismes.md) (a gate is a mechanism) and
  [ADR 0012](0012-le-moteur-n-est-pas-lie-au-logiciel.md) (business evidence, `evidence.facts`)

## Context

An agent run produces two kinds of signals that look alike on a screen:

- what the **agent says**: its summary, its plan, the tool calls it reports over ACP, the evidence
  it writes in its result (`tests_run: 412`, `coverage_delta: 1.2`, a business fact);
- what the **platform establishes itself**: the tests and checks the runner executes, the diff git
  computes, the gates it evaluates, the cost the gateway meters, the permissions its guard decides.

The runner already lets a measurement override a declaration (`merge_evidence`: the tests it runs
replace what the agent claims). But the merged result no longer said which value was measured and
which was only told: the console showed "tests: ✓ 412" the same way in both cases, and showed a run
the agent called "done" without saying whether anything had confirmed it. A governed platform that
blurs the agent's word and its own observation governs nothing.

## Decision

1. **Four provenances.** Each signal shown in a run's or a work item's summary is one of:
   - **declared** — said by the agent, not checked by the platform;
   - **observed** — measured or seen by the platform itself;
   - **inferred** — computed from other signals (an estimate, a forecast);
   - **unknown** — the source cannot be told (a result written by a runner older than this ADR).
2. **The contract carries it.** `Evidence.measured` lists the fields the platform measured itself
   (`tests_passed`, `lint`, `diff_files`…, and `facts.<name>` for a business fact a command
   measured). The runner writes it in `merge_evidence` and when it computes the diff; an agent that
   sets it is overwritten — an agent cannot declare itself measured. A field missing from the list
   is declared; a missing list (`null`) is unknown.
3. **The console never confuses the two.** Every evidence line, every summary card (cost,
   duration, scope, gates, access, diff: observed; the agent's plan, its reported activity, its
   summary and verdict: declared; an estimate: inferred) carries a provenance tag — a word, an icon,
   and its meaning for a screen reader.
4. **"Done" is not taken on the agent's word.** A run whose agent reports `done` while a gate
   refused shows "declared done, not observed"; while no gate has judged it yet, "declared done,
   not verified yet". A work item's state comes from the workflow's transitions, which the platform
   takes after its gates — never from an agent's claim.

5. **A hand-off is observed too** (S25-04). Every output a step stores (`item.documents`) gets a
   *produced* receipt under the SHA-256 digest of its canonical JSON, with the run that produced it;
   every read — what a run's prompt embeds when the orchestrator renders it, what `get_ticket`
   serves during the run — gets a *read* receipt under the digest of what was read. A receipt is
   never modified: a new revision makes a new receipt. `GET /work-items/{id}/hand-offs` returns them
   with the digest of what each output is worth today, so the next step can be shown to have read
   the exact revision produced, and an output modified afterwards no longer matches its receipt.

## Consequences

- `tests/provenance.test.tsx` is the guard of decision 4: a run declared done with a refused gate
  must read "declared done, not observed".
- The runner tests prove decision 2: what it measured is listed field by field, and a list written
  by the agent is ignored.
- Adding a new signal to a summary means choosing its provenance; a signal that cannot be
  classified is shown as unknown rather than as observed.
