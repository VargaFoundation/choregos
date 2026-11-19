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
