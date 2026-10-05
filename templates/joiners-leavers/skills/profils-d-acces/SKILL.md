---
name: profils-d-acces
description: Les groupes d'accès de chaque poste, et les groupes sensibles qui demandent une validation renforcée.
---

# Les profils d'accès

Un poste ouvre un **groupe d'accès** ; certains ouvrent aussi un **groupe sensible**, qui ne
s'accorde que sur une validation ré-authentifiée.

| Poste | Groupe d'accès | Groupe sensible |
|:--|:--|:--|
| Développeuse, développeur | `devs` | `prod-lecture` |
| Ingénieure, ingénieur d'exploitation | `ops` | `prod-admin` |
| Comptable | `finance` | `paie` |
| Chargée, chargé de recrutement | `rh` | `dossiers-salaries` |
| Commerciale, commercial | `ventes` | `crm-export` |

## Règles

- Un poste absent de ce tableau n'a **pas** de groupe sensible par défaut : le dire dans le plan.
- Un groupe demandé qui ne correspond pas au poste est un **écart** : le signaler, avec le groupe
  attendu.
- Au départ, tout groupe — sensible compris — se ferme avec le compte : la désactivation suffit,
  aucun retrait groupe par groupe n'est à prévoir.
