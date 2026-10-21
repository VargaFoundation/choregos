# 0082 — Distillation in passes: map over parts, reduce, a budget and an estimate

- **Status**: accepted, 2026-10-21
- **Concerns**: `apps/api/src/choregos_api/knowledge/distill.py`, `routers/knowledge.py`, the
  `knowledge_distillation_parts` table, the `KnowledgeDistill` workflow of the orchestrator; amends
  [ADR 0051](0051-knowledge-bases.md) (distillation was one call with the whole corpus, refused over
  400 000 characters); keeps [ADR 0008](0008-idempotence-and-run-id.md) (idempotent activities) and
  [ADR 0050](0050-skill-supply-chain-gate.md) (`pending_review`)

## Context

An owner's knowledge base held 2 261 896 characters, mostly spreadsheets. Distillation refused it:
one call with the whole corpus cannot hold that, and the refusal gave no way forward. Raising the
limit does not help: no prompt holds 565 000 tokens, and a person clicking "distil" had no idea what
it would read or cost.

## Decision

1. **Map over parts, then reduce.** The corpus is cut into parts of at most
   `knowledge_distill_part_chars` (300 000): whole documents packed in file-name order, a bigger
   document cut on its headings (a section bigger than a part, on its paragraphs), so each section
   lands in exactly one part. Each part is distilled with the organisation's `knowledge-distiller`
   prompt; the platform keeps only principles citing the part's documents. A merge call (a tree of
   them when the candidates do not fit a prompt) joins duplicates and unites their sources. Its prompt
   is held by the platform, not the organisation's to edit, and its answer is checked again: a
   merged principle may cite only what its candidates cited, then only what the corpus holds.
   One part needs no merge call.
2. **Each call is kept by the digest of what it read** (`knowledge_distillation_parts`: model,
   prompt digest and text). A retried or replayed activity makes no second call; distilling again
   after one document changed pays for that document's part and the merge only.
3. **A budget, checked before every call**: the spend so far plus the call's estimate must stay under
   the lower of the platform budget (`knowledge_distill_budget_usd`) and the cap the requester set.
   Otherwise the run stops, says `over_budget`, and publishes nothing. A model without a price counts
   as free and is shown as "not priced". More parts than `knowledge_distill_max_parts` is refused
   (`too_large`), naming the biggest documents and suggesting what to exclude.
4. **An estimate before the click** (`GET …/distill/estimate`, no model call): characters, tokens (4
   characters each), documents, parts, calls, the model, the price when known, the biggest documents.
   `exclude` (globs on file names, at most 20) leaves documents out of both the estimate and the run.
   The console confirms with a cap of the estimate plus 25 %.
5. **The run is a Temporal workflow** (`KnowledgeDistill`): plan, one activity per part, reduce,
   publish. It survives an API restart. Without a Temporal server (the fakes, the tests) the same
   steps run in sequence in the API process. The base's `distillation` record carries the phase, the
   parts done and the spend; a record with no heartbeat for 30 minutes no longer blocks a new run.
6. **Publication is unchanged and idempotent**: a `pending_review` version, whatever the risk; when
   the files equal the latest version's, that version is reused.

## Consequences

- A base of any size up to the part cap can be distilled; the cap and the budget keep a mistaken
  click from spending without limit.
- The prices are the organisation's catalogue prices (ADR 0060). Without one, the budget cannot
  stop a run: the console says "not priced" and the part cap is the only bound.
- The estimate assumes `knowledge_distill_output_tokens_per_call` (6 000) of output per call; a model
  that answers more is caught by the budget check as the real spend accumulates, one call late.
- Merging is a model call over candidates, not over the sources: a principle spread over two parts
  can be joined with a weaker statement. The sources stay exact, and the person who approves the
  version reads them.
