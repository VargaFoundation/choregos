# joiners-leavers — joiners and leavers

Two workflows (`workflows/onboarding.yaml`, `workflows/offboarding.yaml`), two agents (`agents/`)
and two skills (`skills/`), installed in the organisation when a project is born from the
template. The workflows call five **organisation** connectors, by name; declaring them is the
organisation administrator's job:

| Name | Capability | For |
|:--|:--|:--|
| `annuaire` | `identity` (`entra`) | the account, its groups, its sessions |
| `parc` | `mdm` | enrolling and wiping the laptop |
| `transporteur` | `shipping` | shipping and collecting the laptop |
| `lecteurs` | `access_control` | the badge |
| `fournisseur` | `mcp` | ordering the laptop from the supplier's agent |

A request labelled `offboarding` or `depart` is born in the offboarding workflow; any other, in
the onboarding one.

## The registry (`ontology/`)

When the ontology plugin is active (`CHOREGOS_ESSAI_ONTOLOGIE=1`), the project is born with the
registry of what an onboarding opens and what an offboarding must close: `collaborateur`,
`contrat`, `account` (and its groups), `group`, `materiel`, `badge`, and their links. An HR
person's Claude queries it through the project's MCP door (`collaborateur_search`,
`badge_porteur`…). Without the plugin, the project is born without a registry, and the audit log
says so.

The registry is kept by ontology actions, played like any governed action:

| Action | Risk | Who decides |
|:--|:--|:--|
| `consigner_une_arrivee` | low | the policy |
| `consigner_le_compte`, `consigner_le_poste`, `consigner_le_badge` | low | the policy |
| `constater_un_depart` (account, badge and laptop closed in the registry) | medium | a manager, re-authenticated |

The workflows do not write to the registry yet: they act on the directory, the fleet and the
readers. Recording it is the agent's or HR's job, until the daily reconciliation exists.
