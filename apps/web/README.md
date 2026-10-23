# apps/web — le front de Choregos

Next.js 15 (App Router), React 19, TypeScript strict, Tailwind, TanStack Query.
Le front **ne parle qu'à l'API** (`packages/contracts/openapi.yaml`) : les types viennent
des contrats générés, jamais d'une redéfinition locale.

## Développer

```bash
pnpm install
pnpm dev                                   # contre l'API locale (http://localhost:8000)
NEXT_PUBLIC_API_MODE=mock pnpm dev         # mode démo : fixtures, sans API
pnpm lint && pnpm typecheck && pnpm test   # ce que la CI vérifie
pnpm e2e                                   # parcours Playwright (mode démo)
pnpm e2e:live                              # live journeys: the real API on fakes (uv), CI job `web-live`
```

## Écrans

| Route | Ce qu'on y fait |
| :-- | :-- |
| `/` | les projets : coût du mois, tickets actifs, trains, taux de PR au premier passage |
| `/projects/new` | wizard : template → dépôt → connecteurs → récapitulatif → provisioning |
| `/p/{slug}` | vue d'ensemble : débit, qualité, coûts des 14 derniers jours |
| `/p/{slug}/board` | kanban dont **les colonnes sont les états du workflow**, décisions en ligne |
| `/p/{slug}/items/{id}` | ticket : coût par étape, timeline, PR |
| `/p/{slug}/runs/{id}` | run : journal ACP virtualisé, permissions refusées en évidence, diff, preuves |
| `/p/{slug}/trains` | trains par environnement : lot, départ, gel (motif obligatoire), approbation |
| `/p/{slug}/findings` | triage : créer le ticket, doublon, ignorer, rendre agent-ready |
| `/p/{slug}/memory` | recherche, faits proposés par des agents à valider |
| `/p/{slug}/workflow` | éditeur YAML avec validation par l'API et rendu du graphe |
| `/p/{slug}/settings` | connecteurs (+ test), politique, matrice backend × modèle |
| `/admin` | session, rôles, journal d'audit |

## Choix

- **Mode démo** (`NEXT_PUBLIC_API_MODE=mock`) : des fixtures qui racontent un projet vivant.
  Le flux front avance sans attendre l'API, comme le prévoit le plan (§3.4).
- **SSE avec reprise** : `Last-Event-ID` évite de perdre des événements au rechargement.
- **Journal virtualisé** : 10 000 événements s'affichent sans saccade (test unitaire à l'appui).
- **Theme**: an operations console in the manner of Temporal (ADR 0043, ADR 0048) — dark by default, light or the system's on request (`choregos.theme`, set before the first paint), colours from Radix Colors with a contrast budget per mode, Geist to read and Geist Mono to compare (identifiers, code, numbers, timestamps).
- **Primitives**: the console owns its own (`src/components/ui.tsx`, ADR 0048); it no longer depends on the Varga Foundation's design system, and eslint refuses an `@varga/*` import. Sentence case everywhere; data keeps its own (`data-donnee`).
- **Shell**: from 1280 px, a grouped sidebar that folds into a rail (`choregos.sidebar`, set before the first paint like the theme); a 48 px top bar; below that, a "menu" button opens the same list. The list follows the scope (S30-03): the organisation, a project (Work, Define, Configure, with a project switcher) or the administration; no more tab rows, a breadcrumb carries the hierarchy.
