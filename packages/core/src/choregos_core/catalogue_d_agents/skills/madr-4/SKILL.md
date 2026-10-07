---
name: madr-4
description: How to write an architecture decision record in MADR 4.0 format — location, numbering, required sections, status lifecycle, template.
---

# Architecture decision records, MADR 4.0

A decision record captures ONE architecturally significant decision: the problem, the options that
were really considered, the one chosen and why, and what it costs. Use `template.md` (full) or
`template-minimal.md` (short) beside this file.

## Where and how it is named

- `docs/adr/NNNN-title-with-dashes.md`: NNNN is the highest existing number plus one, on four
  digits; `0001` when the folder is empty. A number is never reused.
- The title is a short problem-solving phrase in the imperative: "Use PostgreSQL row-level
  security for tenant isolation".
- A record is never deleted: a later decision **supersedes** it, and the old one says so.

## Front matter

```yaml
---
status: "proposed"        # proposed | rejected | accepted | deprecated | superseded by ADR-0012
date: 2026-10-07          # when the status last changed
decision-makers: [...]    # who decides
consulted: [...]          # whose opinion was sought (two-way)
informed: [...]           # who is kept up to date (one-way)
---
```

## Sections

Required:

1. `# <title>`
2. `## Context and Problem Statement` — two or three sentences, or a question.
3. `## Considered Options` — a bullet list of names; at least two real options.
4. `## Decision Outcome` — starts with `Chosen option: "<option>", because <justification>`.

Recommended:

- `## Decision Drivers` — the forces and concerns that weigh on the decision.
- `### Consequences` under the outcome — `* Good, because …` / `* Bad, because …`.
- `### Confirmation` — how compliance with the decision will be checked (a review, a test, a fitness function).
- `## Pros and Cons of the Options` — one `###` per option, each argument as `* Good, because …`,
  `* Neutral, because …`, `* Bad, because …`.
- `## More Information` — links, when to revisit the decision, related decisions.

## Rules

- One decision per record.
- Name each option identically in every section.
- Evidence over opinion: cite code as `path:line`, measurements, documentation.
- Say the bad consequences honestly: a decision with no cost was not a decision.
- Write in the language of the existing records; English if there are none.

Template: MADR 4.0.0, https://adr.github.io/madr/ — dual-licensed CC0 1.0 and MIT.
