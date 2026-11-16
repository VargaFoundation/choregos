# 0173 — A run does not write the platform's lines of its journal

- **Status**: proposed, 2026-11-12; S65-08 implements it
- **Concerns**: the run's journal (`run_events`), its door `POST /internal/runs/{id}/events`
  (`routers/internal.py`, `post_events`), a new module `choregos_core.run_journal`, the two places
  where the orchestrator writes into a run's journal (`activities/gates.py`, `_journal`;
  `activities/stage.py`, `_journal_skipped_memories`), the console's guarantees panel
  (`apps/web/src/components/guarantees.tsx`); builds on
  [ADR 0010](0010-gates-are-mechanisms.md), [ADR 0143](0143-a-run-that-ended-stays-ended.md),
  [ADR 0146](0146-the-runner-and-the-agent-do-not-share-a-container.md) and
  [ADR 0171](0171-what-is-merged-is-what-was-judged.md)

## Context

A run's journal has two authors.

- **The run** writes what it did: its ACP log, what its runner mounted, refused or stored, the calls
  of its tools. It posts them in batches to `POST /internal/runs/{id}/events`, with its run token.
- **The platform** writes what it decided about the run: the verdict of each guarantee
  (`gate.outcome`, with the commit it was given on since ADR 0171) and the memories it left out of
  the run's context (`memory.skipped`). The orchestrator adds them to the same table.

Both are read on the same page. The console's guarantees panel shows the `gate.outcome` lines as the
verdicts of the platform, and a person who approves a step reads them there.

The run's door wrote whatever type a batch carried. Whoever holds a run's token could therefore add
a `gate.outcome` line to that run's journal, and the page showed it beside the real ones. Today the
runner and the agent share a container (ADR 0146), so the holder of the token is not only the runner.
The same door let a run write `memory.skipped`, which the orchestrator reads back to decide what it
has already said (`_journal_skipped_memories`): a line written first silenced the real one.

No decision of the platform read these lines: a guarantee's verdict goes back to the workflow in the
activity's own answer, never through the journal. What was at stake is what a person reads before
deciding, and ADR 0010 says a guarantee shown is a mechanism or nothing.

## Decision

1. **A type tells the author, and the platform's types are reserved.** `choregos_core.run_journal`
   names the types the platform writes in a run's journal (`GATE_OUTCOME`, `MEMORY_SKIPPED`) and the
   namespaces they live in (`gate.`, `memory.`). The namespaces are reserved whole, not type by type:
   a line the platform adds later under one of them is refused from a run before anyone thinks of it.
2. **The run's door refuses them.** A batch that carries a type of a reserved namespace is answered
   422 and nothing of it is written: a journal is not half a run's and half a forgery. The match is
   the most lenient a reader could make (blanks, case). The refusal is logged with the run. It holds
   for a closed run too: ADR 0143 keeps the journal open for what a process did after its run ended,
   which is the run's own lines.
3. **The platform takes its types from the module.** A test reads every place that builds a line of
   a run's journal: outside the run's door, the type is one of the module's names. A new platform
   line cannot be written outside the reserved namespaces without that test saying so.
4. **The run side stays out of the namespaces.** A second test reads every type the runner and its
   tool bridge record: none is reserved. Reserving a namespace the runner uses would drop its lines
   in silence, because a lost journal never fails a run.

## Alternatives rejected

- **A column that says who wrote the line.** It answers the same question with a migration, a field
  on the journal's contract and a filter every reader must remember. The namespace answers it where
  the line is written. It is the answer to take if a third author ever writes there.
- **A list of the types a run may write.** The run's types are the ACP's and the runner's, they grow
  with every runner release, and a runner newer or older than the API would lose lines.
- **Dropping the reserved lines and keeping the rest of the batch.** No runner sends one, so a batch
  that carries one is not a runner's mistake to be forgiving about.

## Consequences

- No migration, no contract change: the route answered 422 for a malformed batch already.
- A journal written before this change is not rewritten, and nothing in a stored line tells who
  wrote it. `docs/security.md` says so.
- The console is unchanged: what it reads as the platform's word is now the platform's.
- A runner that sent a reserved type would lose that batch and nothing else: its run does not fail on
  a journal (`EventJournal.flush`).
- Not addressed here, and noted: the platform numbers its lines after the highest number the journal
  holds, in the run's own sequence.
