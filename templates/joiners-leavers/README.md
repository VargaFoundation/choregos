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
