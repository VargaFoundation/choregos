# 0042 — What the console names is lowercase; what it says is a sentence

- **Status**: accepted, 2026-10-09
- **Concerns**: every text the web console writes (`apps/web`); amends
  [ADR 0039](0039-tout-ce-qu-un-utilisateur-lit-est-en-anglais.md)

## Context

Two rules pulled the console in opposite directions. The Varga Foundation design system writes
**everything in lowercase** — a writing convention, not a `text-transform`. The product review of
2026-10-07 (point 8, then P3) listed "everything in lowercase" as a defect and asked for **sentence
case**. The code followed neither: on 2026-10-09, 15 sentences started in lowercase ("each project
runs its own workflows: …", "a ticket goes in, … but first, who are you?"), and a handful of
labels started with a capital ("Environment prod", "Run implement · attempt 1", "Other MCP
client"), next to hundreds that did not.

## Decision

1. **What the console names stays lowercase**, as the design system writes it: buttons, links,
   tabs, menu entries, headings and card titles, table headers, field labels, badges, empty-state
   titles, short notes that are not sentences ("no finding", "reading the costs…").
2. **What the console says is a sentence and starts with a capital**: a paragraph, a lead, a help
   text, an explanation in an empty state — any text that ends with a full stop or holds more than
   one sentence.
3. **Data keeps its case.** A project's name, a state's label, an actor, a ticket's title, a
   product name (GitHub, Claude Code), an identifier or a code (`BILL-42`, `mcp:write`) are shown as
   they were written. Neither rule rewrites them.

## Consequences

- `e2e/la-casse.spec.ts` walks the console's pages and refuses a sentence that starts in lowercase,
  and a label the console writes that starts with a capital; data is marked `data-donnee` and left
  alone.
- Messages written by the API (errors, the catalogue's summaries) are outside this ADR's test: they
  are aligned in the API.
