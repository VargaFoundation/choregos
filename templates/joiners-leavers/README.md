# joiners-leavers — arrivées et départs

Deux workflows (`workflows/onboarding.yaml`, `workflows/offboarding.yaml`), deux agents
(`agents/`) et deux skills (`skills/`), installés dans l'organisation quand un projet naît du
gabarit. Les workflows appellent cinq connecteurs de l'**organisation**, par leur nom ; les
déclarer est le geste de son administrateur :

| Nom | Capacité | Pour |
|:--|:--|:--|
| `annuaire` | `identity` (`entra`) | le compte, ses groupes, ses sessions |
| `parc` | `mdm` | l'inscription et l'effacement du poste |
| `transporteur` | `shipping` | l'expédition et la reprise du poste |
| `lecteurs` | `access_control` | le badge |
| `fournisseur` | `mcp` | la commande du poste à l'agent du fournisseur |

Une demande étiquetée `offboarding` ou `depart` naît dans le workflow de départ ; toute autre,
dans celui d'arrivée.

## Le registre (`ontology/`)

Quand le greffon de l'ontologie est actif (`CHOREGOS_ESSAI_ONTOLOGIE=1`), le projet naît avec le
registre de ce qu'une arrivée ouvre et de ce qu'un départ doit fermer : `collaborateur`, `contrat`,
`account` (et ses groupes), `group`, `materiel`, `badge`, et leurs liens. Le Claude d'une RH
l'interroge à la porte MCP du projet (`collaborateur_search`, `badge_porteur`…). Sans le greffon, le
projet naît sans registre, et le journal d'audit le dit.

Le registre se tient par des actions de l'ontologie, jouées comme toute action gouvernée :

| Action | Risque | Qui décide |
|:--|:--|:--|
| `consigner_une_arrivee` | faible | la politique |
| `consigner_le_compte`, `consigner_le_poste`, `consigner_le_badge` | faible | la politique |
| `constater_un_depart` (compte, badge et poste fermés au registre) | moyen | un responsable, ré-authentifié |

Les workflows n'écrivent pas encore au registre : ils agissent sur l'annuaire, le parc et les
lecteurs. Le consigner est le geste de l'agent ou de la RH, en attendant la réconciliation
quotidienne.
