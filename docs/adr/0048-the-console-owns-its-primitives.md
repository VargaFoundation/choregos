# 0048 — The console owns its primitives: it looks like Temporal's UI, writes in sentence case, and no longer depends on the Varga Foundation design system

- **Status**: accepted, 2026-10-12
- **Concerns**: the web console (`apps/web`): its components, its case, its type and its shapes;
  supersedes [ADR 0042](0042-the-interface-s-letter-case.md) and §1, §4 (the type of buttons and
  table headers), §6 and §7 of [ADR 0043](0043-the-console-s-look.md)

## Context

ADR 0043 (2026-10-10) gave the console the colours of an operations console but kept the Varga
Foundation design system as its component library: its tokens were remapped, its components
re-skinned or wrapped in `components/ui.tsx`. ADR 0042 (2026-10-09) kept its writing convention —
everything the console *names* in lowercase.

Two days later the console still read as the foundation's site, not as a product: a "choregos /
varga foundation" brand mark in the header and the sidebar, lowercase headings ("agents",
"approve"), monospace buttons and navigation labels, uppercase mono captions, square corners on
every card, input and button. Seven of the design system's components were still used directly
(`Card`, `TabList`, `Alert`, `Container`, `Numeral`, `CheckSquare`, `Code`), the default
organisation of the client was `varga`, and every colour role was named `--varga-*`. Keeping the
library meant re-skinning it a little more after each of its releases.

The product owner decided (D1, plan of 2026-10-12, item 10): **the console looks like Temporal's
UI, and only like it; the Varga Foundation design system is dropped as a dependency of the
console; "by Varga Foundation" stays as a footer line.**

## Decision

1. **The console owns its primitives.** `apps/web` no longer depends on
   `@varga/design-system`: no package, no stylesheet, no `@source`. Cards, tabs, alerts, the page
   container, inline code, checkboxes, step numerals and the wordmark are local components in
   `src/components/ui.tsx` (and `src/components/ui/`), built on plain accessible HTML — or on Radix
   primitives where a behaviour needs one and the package is already a dependency. ESLint refuses
   an import from the Varga package.
2. **It copies the look of Temporal's UI** (MIT, the Holocene design system): a cool near-black,
   one accent, tinted status pills, dense tables, a darker left sidebar. It copies a *look*, never
   a logo, a name or an asset: the console's mark is a **Choregos wordmark**, and "by Varga
   Foundation" is one line of the footer.
3. **Sentence case**, everywhere the console writes: headings, card titles, buttons, links, tabs,
   menu entries, table headers, field labels, badges, empty states and help text start with a
   capital and continue in lowercase ("New project", "Approve", "Release trains"). Data keeps its
   case (a project's name, a state's label, an identifier, a product name), as ADR 0042 §3 already
   said; it is marked `data-donnee` where the console shows it in a place a label could be.
   `e2e/la-casse.spec.ts` refuses a label or a sentence the console writes that starts in
   lowercase.
4. **Sans for what is read, mono for what is compared.** Geist carries text, titles, navigation,
   buttons, captions and table headers; Geist Mono carries identifiers, code, numbers that line up
   and timestamps — nothing else. Captions and table headers are no longer uppercased.
5. **Small radii.** Cards, panels, inputs, selects, buttons, alerts and menus are rounded at 4–6 px
   (`--radius-sm` 4 px for controls, `--radius-md` 6 px for surfaces); pills and dots stay round;
   borders stay 1 px. Dark mode keeps no shadows, light mode short cool ones on raised surfaces.
6. **The colour roles are the console's own**: `--choregos-*`, declared in `src/styles/theme.css`
   and nowhere else; `tests/theme-provenance.test.ts` checks that every role the code reads is
   declared there.
7. **Kept from ADR 0043**: every colour from Radix Colors (§2), the contrast budget per mode (§3),
   Geist and Geist Mono self-hosted (§4, except the type of buttons and table headers), the theme
   the person chooses applied before the first paint (§5), and the shell — sidebar, rail and 48 px
   top bar (§8). The contrast tests are what make this change safe, and they stay green.

## Consequences

- ADR 0042 is superseded. ADR 0043 stays accepted for §2, §3, §5 and §8, and for §4 except the
  type of buttons and table headers; its §1, §6 and §7 are superseded by this record.
- The foundation's design system can evolve without the console: no remapping, no wrapper kept "in
  the same names and props", no test that compares the console's tokens with the foundation's.
- Every label of the console changes case once; unit and end-to-end tests that find an element by
  its name change with it.
- Delivered as stream S30: this record (S30-01), then the primitives, the wordmark, the type, the
  radii and the case (S30-02). The navigation is reworked separately (S30-03), on top of it.

## Alternatives discarded

- **Keep remapping the design system** (ADR 0043 §1). Every look the product owner rejected —
  square corners, mono buttons, the brand mark, lowercase — came from the library, and each was
  undone by a wrapper that had to follow its releases.
- **Fork the design system.** A fork is still the foundation's site, maintained twice.
- **Adopt a third-party component library** (shadcn/ui, Radix Themes). The console already has its
  tokens, its contrast budget and about fifteen primitives; a library would bring its own look to
  re-skin, which is the problem this record removes.
- **Uppercase the first letter in CSS** (`::first-letter`). The accessible name would stay
  lowercase, a screen reader and a test would read one text and a person another.
