# 0043 — The console looks like an operations console: soft contrast, a sans-serif, a chosen theme

- **Status**: accepted, 2026-10-10
- **Concerns**: the web console (`apps/web`): its colours, type, shapes and theme; amends
  [ADR 0042](0042-la-casse-de-l-interface.md); keeps the Varga Foundation design system as the
  component library

## Context

The console renders through the Varga Foundation design system. Its identity is editorial:
monospace everywhere, page titles of 36–48 px in a bold display mono, square corners, a solid
inverted slab for the primary action, an ink rule under every table header, a neon accent, and a
dark mode that is a pure black-and-white inversion chosen by the operating system alone. Measured
on 2026-10-10, text sat at 19.8:1 on its background in light and 19:1 in dark, while hairlines sat
at 1.26–1.9:1: all of the contrast was in the text, none in the structure.

The product owner found the console "harsh and very high-contrast", and asked for the look of the
operations consoles the team works with — a Temporal-like console: a cool near-black, one accent,
a sans-serif to read and a mono to compare, tinted status pills, dense tables, a left sidebar, and a
theme the person chooses.

## Decision

1. **The console remaps the design system's tokens; it does not fork it.** `src/styles/theme.css`
   redefines every `--varga-*` colour role and adds a few `--choregos-*` ones (raised surfaces, the
   sidebar, status pills). The foundation's token values are imported into a cascade layer, so the
   console's unlayered values always win — whatever the specificity of the design system's dark
   selectors. Components keep coming from the design system; the few whose look is hard-coded are
   wrapped in `components/ui.tsx` (S24-02).
2. **Every colour comes from Radix Colors** (MIT): a step of a Radix scale, or a `color-mix()` of a
   step with `#000`, `#fff` or another step. `tests/theme-provenance.test.ts` checks every literal.
   Neutral is slate, the single accent for actions, links, focus and selection is iris, agents
   (what runs without you) are plum, and states are blue (running), amber (waiting), green
   (succeeded), red (failed), orange (retrying) and slate (neutral) — always with a word or an
   icon, never by colour alone.
3. **A contrast budget, per mode** (`tests/theme-contraste.test.ts`): body text between 7:1 and
   17:1 on every surface; secondary text, accent text and every state at 4.5:1 at least, on the
   page, a card, a hover and its own pill; text on a filled action at 4.5:1 at rest and on hover;
   control borders and the focus ring at 3:1 at least (WCAG 1.4.11, which axe does not measure);
   hairlines at 1.3:1 at least; the sidebar darker than the page.
4. **Geist to read, Geist Mono to compare.** Geist (sans) carries text, titles and navigation
   labels; Geist Mono carries code, identifiers, timestamps, numbers that line up, buttons and
   table headers. Both are self-hosted through `next/font/local` (the `geist` package, OFL); the
   design system's JetBrains Mono and Space Mono are no longer loaded.
5. **The theme is the person's choice**: dark (the default, and what renders without JavaScript),
   light, or the system's. The preference lives in the browser (`choregos.theme`); a script in the
   document head — carrying the request's CSP nonce — applies it before the first paint, and React
   never renders `data-theme`, so neither a flash nor a hydration mismatch can occur. `dark:` follows
   `data-theme`, not the system alone.
6. **Shapes stay square**, except pills and dots, which are round; borders are 1 px; dark mode has
   no shadows, light mode short cool ones on raised surfaces.
7. **Amends ADR 0042**: a caption or a table header may be uppercased by CSS (`text-transform`);
   that is presentation, and the text in the document keeps the case ADR 0042 gives it.
8. **The shell** (S24-03): from 1280 px, the navigation leaves the header for a 240 px left
   sidebar, darker than the page, grouped (work, catalogue, organisation) with Lucide icons (ISC).
   It folds into a 52 px rail of icons; the choice lives in the browser (`choregos.sidebar`) and
   the same head script applies it before the first paint (`data-sidebar`, never rendered by
   React). The top bar is 48 px: the organisation on the left, the edition, the person, sign-out
   and the theme on the right. Below 1280 px, its "menu" button opens the same grouped list, with
   44 px targets.

## Consequences

- The design system can move on without the console following blindly: a new colour role in its
  dark tokens turns `theme-provenance` red until the console declares it.
- `e2e/accessibilite.spec.ts` runs axe on every page in both themes, and `e2e/theme.spec.ts` proves
  the default, the persistence of a choice, and that the head script alone applies it;
  `e2e/coquille.spec.ts` proves the same for the folded sidebar, and the 48 px top bar at every
  width.
- The work is delivered as stream S24: tokens, fonts and theme (S24-01); restyled components
  (S24-02); a left sidebar and a 48 px top bar (S24-03); the map, the journey, the board and the
  journal (S24-04).
