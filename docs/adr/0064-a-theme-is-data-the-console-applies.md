# 0064 — A theme is data the console applies

- **Status**: accepted, 2026-10-15
- **Concerns**: the API contract (`GET /api/v1/theme`), the plugin seams (`declare_theme_provider`),
  the console's layout, the chart; amends [ADR 0048](0048-the-console-owns-its-primitives.md) §6;
  builds on [ADR 0043](0043-the-console-s-look.md) (contrast budget, theme before first paint) and
  [ADR 0032](0032-admin-sections-by-manifest.md) (plugins declare data, the core renders)

## Context

The console's look is one file, `apps/web/src/styles/theme.css`: 52 colour roles for two modes,
Geist fonts, radii, a wordmark and a footer. A deployment that wants its own look (its charter, its
logo) has two bad options today: fork the console, or ship its brand inside the community edition,
which is public and Apache-2.0 while a brand is not.

Constraints that shape the answer:

- the console's CSP is `style-src 'self' 'unsafe-inline'`, `font-src 'self'`, `img-src 'self' data: blob:`,
  scripts by nonce, so fonts and images must come from the console's origin (the API is on the same
  origin under `/api`);
- the look has a **measured** contrast budget (ink 7 to 17:1, secondary text and states 4.5:1,
  control borders 3:1…), and a customer palette that breaks it breaks accessibility silently;
- the first paint must already be themed, or every navigation flashes the default look.

## Decision

1. **A theme is data, served by the API.** `GET /api/v1/theme?org=<slug>` is public (the sign-in
   page needs it) and returns a `Theme`: sparse overrides of the colour roles for `dark` and
   `light` (`#rrggbb` only: no `var()`, no name, no alpha, so it is safe to emit as CSS and
   measurable), radii, a title and a body font (family, weight, faces), a logo per mode, a
   favicon, the product name, a footer text, and the default mode. Assets are paths under
   `/api/v1/` (same origin, CSP unchanged). The mono font, the layout and the attribution line
   ("built on Choregos by Varga Foundation", ADR 0048) are **not** themable.
2. **The community edition serves its default and nothing else.** A plugin customises the look by
   `choregos_api.greffons.declare_theme_provider(fn)`: one provider (a second declaration stops the
   start), `fn(session, org) -> dict | None`, sync or async. Customising is an enterprise feature;
   the CE contains no brand content.
3. **The API validates, the console never has to.** The route validates what the provider returns:
   the schema, then the console's contrast budget computed on the **effective** colours (the
   console defaults overridden by the theme, mode by mode). A provider that returns nothing, raises
   or returns an invalid theme yields the default theme, `X-Choregos-Theme: fallback` and an error
   log naming each violation. `choregos_api.theme.validate_theme` is public: a plugin runs the same
   check when its operator writes a theme and refuses it there.
4. **The defaults are generated from `theme.css`**, never typed twice:
   `apps/web/scripts/export-theme-defaults.mts` (`pnpm theme:defaults`) resolves the stylesheet
   (the resolver of the contrast tests) into `apps/api/src/choregos_api/theme_defaults.json`; a
   vitest drift test fails when the two diverge. The budget in `theme.py` is a line-by-line copy of
   `tests/theme-contraste.test.ts`; the API's tests pin it.
5. **A new console role pair**, `--choregos-font-title` and `--choregos-font-title-weight` (default:
   the sans font, 600), used by `h1` to `h3` (S39-03b).
6. **The chart gives a plugin its files**: `choregos-api.extraVolumes` / `extraVolumeMounts`
   (S39-03c).

## Consequences

- ADR 0048 §6 reads: the colour roles are declared in `theme.css`; a served theme may override
  those listed in the contract, under the same budget. `tests/theme-provenance.test.ts` still
  guards the defaults.
- A change to a colour role in `theme.css` requires `pnpm theme:defaults` (a test enforces it).
- The theme endpoint is public and un-authenticated: it must hold nothing secret. The contract has
  no free-form field that reaches the page unchecked (hex, family names and asset paths are
  re-checked by the console before they become CSS, S39-03b).

## Alternatives rejected

- **A `theme.css` endpoint**: a second representation, built server-side from data the console must
  still read (logo, name, default mode).
- **Client-only fetch**: a flash of the default look on every navigation.
- **Bundling themes into the console**: the CE would ship brand code, and the brand is not Apache-2.0.
- **Free CSS in the theme**: unbounded, unmeasurable, and an injection surface under a public endpoint.
