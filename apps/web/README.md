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
- **Thème sobre** : peu de couleurs, une par type d'acteur (agent, humain, système).
