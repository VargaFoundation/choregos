# apps/web — the Choregos front end

Next.js 15 (App Router), React 19, TypeScript strict, Tailwind, TanStack Query.
The front end **talks only to the API** (`packages/contracts/openapi.yaml`): the types come
from the generated contracts, never from a local redefinition.

## Develop

```bash
pnpm install
pnpm dev                                   # against the local API (http://localhost:8000)
NEXT_PUBLIC_API_MODE=mock pnpm dev         # demo mode: fixtures, no API
pnpm lint && pnpm typecheck && pnpm test   # what the CI checks
pnpm e2e                                   # Playwright journeys (demo mode)
pnpm e2e:live                              # live journeys: the real API on fakes (uv), CI job `web-live`
```

## Screens

| Route | What one does there |
| :-- | :-- |
| `/` | the projects: cost of the month, active tickets, trains, first-pass PR rate |
| `/projects/new` | wizard: template → repository → connectors → summary → provisioning |
| `/p/{slug}` | overview: throughput, quality, costs of the last 14 days |
| `/p/{slug}/board` | kanban whose **columns are the workflow states**, inline decisions |
| `/p/{slug}/items/{id}` | ticket: cost per stage, timeline, PR |
| `/p/{slug}/runs/{id}` | run: virtualised ACP log, refused permissions highlighted, diff, evidence |
| `/p/{slug}/trains` | trains per environment: batch, departure, freeze (reason required), approval |
| `/p/{slug}/findings` | triage: create the ticket, duplicate, ignore, make agent-ready |
| `/p/{slug}/memory` | search, facts proposed by agents to validate |
| `/p/{slug}/workflow` | YAML editor with validation by the API and graph rendering |
| `/p/{slug}/settings` | connectors (+ test), policy, backend × model matrix |
| `/admin` | session, roles, audit log |

## Choices

- **Demo mode** (`NEXT_PUBLIC_API_MODE=mock`): fixtures that tell the story of a living project.
  The front-end stream moves forward without waiting for the API, as the plan provides (§3.4).
- **SSE with resume**: `Last-Event-ID` avoids losing events on reload.
- **Virtualised log**: 10,000 events render without jank (backed by a unit test).
- **Theme**: an operations console in the manner of Temporal (ADR 0043, ADR 0048) — dark by default, light or the system's on request (`choregos.theme`, set before the first paint), colours from Radix Colors with a contrast budget per mode, Geist to read and Geist Mono to compare (identifiers, code, numbers, timestamps).
- **Primitives**: the console owns its own (`src/components/ui.tsx`, ADR 0048); it no longer depends on the Varga Foundation's design system, and eslint refuses an `@varga/*` import. Sentence case everywhere; data keeps its own (`data-verbatim`).
- **Shell**: from 1280 px, a grouped sidebar that folds into a rail (`choregos.sidebar`, set before the first paint like the theme); a 48 px top bar; below that, a "menu" button opens the same list. The list follows the scope (S30-03): the organisation, a project (Work, Define, Configure, with a project switcher) or the administration; no more tab rows, a breadcrumb carries the hierarchy.
