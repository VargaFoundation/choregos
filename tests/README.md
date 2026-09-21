# tests/ — ce qui prouve que la plateforme tient

| Dossier | Ce qu'il prouve | Quand |
| :-- | :-- | :-- |
| `conformance/backends` | un backend ACP respecte le contrat (7 vérifications du §2.2) | CI (Claude Code), nuit (tous) |
| `conformance/templates` | un template provisionne un projet et fait passer un ticket S | nuit |
| `e2e` | les scénarios des jalons M1–M5 | nuit, et à la main avant un jalon |
| `replay` | les workflows Temporal rejouent les historiques archivés sans casser | CI |
| `fixtures` | dépôts jouets et tickets de référence pour les évals et la matrice | — |

Les tests unitaires vivent à côté du code qu'ils testent (`packages/*/tests`, `apps/*/tests`).
