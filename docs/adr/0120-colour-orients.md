# 0120 — Colour orients: one accent for actions, a categorical palette and an info tone to find one's way

- **Status**: accepted, 2026-11-03, by the product owner ("it lacks colour, with the default theme
  as with ours; the product is already complex, the console must make it simple")
- **Concerns**: `apps/web/src/styles/theme.css`, `apps/web/src/app/globals.css`, `components/ui.tsx`,
  `lib/workflow-hue.ts`; amends [ADR 0043](0043-the-console-s-look.md) §2 and
  [ADR 0048](0048-the-console-owns-its-primitives.md) §2; keeps the contrast budget of ADR 0043 §3

## Context

ADR 0043 and 0048 gave the console the look of Temporal's UI: a cool near-black, **one accent**
(iris) for actions, links, focus and selection, tinted pills for the states of a run. Three weeks
later the product owner reviewed the console on a real project and found it hard to read:

- nothing tells one workflow from another — the board, a ticket and the workflow list show the same
  grey cards whatever the process;
- the settings page is a dozen sections with no icon and no colour to find one's way;
- a ticket says what is expected of you in the same ink as everything else;
- a served brand theme (ADR 0064) can only recolour what the default already colours, so a brand
  theme is as grey as the default.

The colour that was refused was colour *for its own sake*. What is missing is colour that **orients**:
it says which workflow, which part of the console, and what kind of message, before a word is read.

## Decision

1. **The accent stays one, and stays for actions**: buttons, links, focus and selection are iris and
   nothing else. No categorical colour is used for something that can be clicked *because* of its
   colour.
2. **A categorical palette of eight hues** — indigo, teal, amber, pink, cyan, grass, orange,
   violet, from Radix Colors — each with the four roles a state already has (`solid`, `soft`, `line`,
   `ink`): `--choregos-cat-{1..8}-*`. It colours **identity**, never status: a workflow, a section of
   the navigation, a group of settings. No hue of the palette is red: red means a failure, and a
   workflow should not look failed because of its name.
3. **A workflow's hue is computed from its name** (`workflowHue(name)`, a stable hash), so the same
   workflow has the same colour on the board, a ticket, the workflow list and the map — with no field
   in the workflow contract and nothing to configure.
4. **An info tone** (`--choregos-info`, `info-soft`, `info-line`) completes ok / warn / danger for
   messages that are neither good nor bad news: "the next step is yours", "an agent is working".
5. **Colour never speaks alone** (ADR 0043 §2 still holds): a hue comes with a name, an icon or a word.
6. **The budget holds**: every `cat-*-ink` and `info` reaches 4.5:1 on every surface and on its own
   soft tint, in both modes — measured by `tests/theme-contraste.test.ts`. In light mode the ink
   darkens toward step 12 of its own scale, as the state inks do.

## Consequences

- Pages get a header with a tinted icon, and the board, a ticket and the workflow pages show the
  workflow's chip (S55-02 to S55-08).
- A served theme may override the new roles once they are listed in `ThemeTokens` (S55-01b,
  contract-change); until then a brand theme keeps the default categorical palette.
- `tests/theme-provenance.test.ts` keeps every new colour a Radix step.

## Alternatives discarded

- **A colour field per workflow**: one more thing to choose and to review, a contract change, and two
  workflows chosen "blue" by two teams are indistinguishable. A computed hue can still be overridden
  later by such a field if the need appears.
- **More accents** (a second action colour): two colours that both mean "click" mean nothing.
