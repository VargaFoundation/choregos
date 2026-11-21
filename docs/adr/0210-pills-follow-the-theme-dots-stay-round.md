# 0210 — Pills follow the theme, dots stay round

- **Status**: accepted, 2026-11-20 (story S39-03d)
- **Concerns**: the API contract (`Theme.radius.pill`), `apps/web` (`--radius-pill`, `rounded-pill`);
  amends [ADR 0048](0048-the-console-owns-its-primitives.md) §6 ("pills and dots stay round") and
  [ADR 0064](0064-a-theme-is-data-the-console-applies.md) (the radii a theme may set)

## Context

A theme sets two radii: `radius.control` (0 to 8 px, controls) and `radius.surface` (0 to 12 px,
surfaces). Badges, chips, counters and bars were written `rounded-full`, a circle whatever the theme
says (ADR 0048: "pills and dots stay round"). A brand with square corners therefore still showed
rounded pills: the "Default" badge, a workflow chip, the edition badge, the "RECONNECTING" pill.

## Decision

1. **A third radius, `radius.pill`**: an integer from 0 to 999 px, optional. Absent means unchanged,
   which is round (`9999px`); 0 is square; 999 and above is round for any height the console draws.
2. **The console carries it as `--radius-pill`**, declared in `globals.css`'s `@theme` (default
   `9999px`) so Tailwind generates `rounded-pill`; `served-theme.ts` emits `--radius-pill:<n>px` next
   to the other radii, bounded the same way (out of range is dropped, never clamped).
3. **What is a pill**: a badge, chip, tag, filter button, counter, progress bar or flow node: a
   shape that holds text or a measure. It uses `rounded-pill`. **What stays `rounded-full`**: status
   and hue dots, avatars, spinners, numbered circles, circular icon buttons: shapes whose meaning is
   the circle. A square dot would read as a different glyph.
4. **A guard keeps the split**: `apps/web/tests/pill-radius.test.ts` counts `rounded-full` per file
   against a list, each entry with the circle it is for; a new one on a pill, or a stale entry, fails.
5. Nothing else changes: the budget of contrast is unaffected, and a theme that does not set
   `radius.pill` looks exactly as before.

## Consequences

- An organisation theme can have square badges (`"radius": {"pill": 0}`). The enterprise edition maps
  its own setting to the field; a core without it does not know it (the API refuses an unknown
  field, `extra="forbid"`), so a provider sends it only when the core's model has it.
- A new pill-shaped element is written `rounded-pill`; a new dot `rounded-full`. The list in the guard
  is the place to say why.
- **Does not decide**: a per-component radius, or pills with a minimum rounding.
