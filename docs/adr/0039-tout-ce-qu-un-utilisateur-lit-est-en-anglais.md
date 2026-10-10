# 0039 — Everything a user reads is in English; identifiers and machine codes do not change

- **Status**: accepted, 2026-10-07
- **Concerns**: everything Choregos ships or says to a person — the console, the API's messages,
  the validator's, the shipped workflow templates, policy presets and stack templates, agents,
  skills, the files a template adds to a project's repository, the demo; follows the decision of
  2026-09-24 that made English the reference language of the documentation

## Context

The console has been English since 2026-09-25, and the reference documentation since 2026-09-24.
The product review of 2026-10-07 still found French on almost every screen: state names of the
shipped templates (`À trier`, `Intervention humaine`), the labels of the workflow map, the
validator's and the API's messages, the HR template's tasks and agents, the demo data. None of it
came from the console's code: it came from what the platform ships and says. A user evaluating
Choregos — and every customer the enterprise edition is sold to — reads that as an unfinished
product.

## Decision

1. **Everything a person reads is in English**: state labels, descriptions, field and task titles,
   attestations, action titles and justifications, agent names, descriptions and instructions,
   skills, the files a template adds to a repository, validator and API messages, status comments,
   notifications, demo data.
2. **Identifiers do not change.** State names (`needs_human`, `a_revoir`), field names
   (`date_arrivee`), object types (`collaborateur`), connector and operation names, agent and skill
   slugs, machine codes (`workflow.effet_implicite`, `gate.sans_matiere`) are keys: renaming them
   would break published workflows, installed agents and API clients for no reader's benefit.
   Installed agents and skills are never rewritten (ADR 0033): an organisation that already has
   `coordinateur-onboarding` v1 keeps it until it publishes the next version.
3. **Code, comments, docstrings, `docs/plan` and commit messages stay in French**, as AGENTS.md says.
4. **A guard enforces it, story by story.** `tests/langue/test_anglais.py` reads what is shipped —
   only the fields a person reads, without code spans and Jinja — and refuses French. Each later
   story (core messages, contracts, API, orchestrator, playbooks) adds its own scan to the same
   file, so the guard grows with the translation and never lags behind it.

## Consequences

- Tests that assert on a French message change with it; tests that carry their own French fixtures
  (a workflow written inside the test) keep them — they are data, not what the platform ships.
- A GitHub Projects board created by an older Choregos keeps its French field names (`Coût (€)`,
  `Taille`): renaming a board field is a migration, done in its own story. *Done in S22-21:* the
  fields are `Cost (€)`, `Size`, `Risk`; provisioning renames the French ones in place, and until
  then the adapters write to whichever name the board, the Jira project or the GitLab metadata
  block has.
- The detector is deliberately simple (accents, a short French lexicon, French function words);
  its false positives are settled by a short list of proper names, never by excluding a path.

## Alternatives discarded

- **Internationalisation (French and English)**: `next-intl` was declared in the console and never
  wired (removed 2026-09-24). Two languages double every string and every test for a product whose
  users and competitors work in English; it can come back the day a customer asks for it.
- **Translating identifiers too**: a cleaner surface, at the price of a migration for every
  published workflow and installed agent, and of breaking every API client — for keys nobody reads
  as prose.
