# 0038 — The console's YAML editor is bundled CodeMirror; nothing is fetched from a third party at run time

- **Status**: accepted, 2026-10-07
- **Concerns**: the console (`apps/web`): `components/yaml-editor.tsx`, the CSP in `src/proxy.ts`,
  the bundle budget (`scripts/bundle-budget.mjs`); follows [ADR 0023](0023-editer-un-workflow-depuis-la-console.md)

## Context

The product review of 2026-10-07 found the YAML editor of the dev tenant broken. The cause, measured
on the production build of `main`: `@monaco-editor/react` does not bundle Monaco — its loader
downloads it **at run time** from `cdn.jsdelivr.net` (14 requests for one page). The console's CSP
(`style-src 'self' 'unsafe-inline'`, `font-src 'self'`) blocks Monaco's stylesheet and icon font, so
the editor rendered without style: a bare textarea, no line numbers, a cursor that did not match the
text. On a tenant whose egress does not reach the CDN, the editor never mounts at all.

Bundling Monaco locally is not an option either: its editor core alone is several megabytes, while
the console's budget is 600 kB per chunk and 1,600 kB in total, enforced in CI.

## Decision

1. **The YAML editor is CodeMirror 6**, bundled with the console (`@codemirror/state`, `view`,
   `commands`, `language`, `lint`, `lang-yaml`, `@lezer/highlight`, pinned exactly). It needs no
   worker, no stylesheet file and no font; its styles are injected and pass `'unsafe-inline'`.
2. **The console loads nothing from a third party at run time.** The CSP keeps no outside origin
   for scripts, styles, fonts or workers, and an end-to-end test on the production build listens
   for CSP violations and for requests leaving the console's origin: there must be none.
3. **The server stays the only validator.** The editor places the API's errors and warnings at
   their line (diagnostics in the gutter); it adds no rule of its own.
4. **The editor is themed with the design system's tokens** (`--varga-*`), so light and dark
   follow without code, and the gutter passes the AA contrast that axe measures.

## Consequences

- The bundle grows by about 365 kB (1,531 kB of 1,600 measured at the time); the workflow map,
  rewritten without React Flow (S21-07), gives some of it back.
- CodeMirror's YAML support is syntax highlighting and bracket matching, not schema completion.
  Completion from `workflow.schema.json` would be an addition, not a dependency change.
- `pnpm`'s minimum release age applies: versions published the same day are refused rather than
  excluded by name; the pinned versions are the latest that pass it.

## Alternatives discarded

- **Monaco from our own origin** (copying `min/vs` into `public/` and pointing the loader at it):
  no third party, but several megabytes served to every editor page, workers under `blob:`, and
  a second copy of the editor to upgrade by hand.
- **A plain `textarea`** with the server's errors listed beside it: no dependency, but no line
  numbers, no highlighting and no way to show an error at its line — the reason ADR 0023 chose an
  editor in the first place.
