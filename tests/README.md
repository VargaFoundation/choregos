# tests/ — what proves the platform holds

| Directory | What it proves | When |
| :-- | :-- | :-- |
| `conformance/backends` | an ACP backend honours the contract (the 7 checks of §2.2) | CI (Claude Code), nightly (all) |
| `conformance/templates` | a template provisions a project and takes an S ticket through | nightly |
| `e2e` | the scenarios of milestones M1–M6 (M6: the HR scenario, arrival and departure, S20-07) | nightly, and by hand before a milestone |
| `replay` | the Temporal workflows replay the archived histories without breaking | CI |
| `fixtures` | toy repositories and reference tickets for the evals and the matrix | — |

Unit tests live next to the code they test (`packages/*/tests`, `apps/*/tests`).
