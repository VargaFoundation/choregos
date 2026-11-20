# joiners-leavers — joiners and leavers

Two workflows (`workflows/onboarding.yaml`, `workflows/offboarding.yaml`), two agents (`agents/`)
and two skills (`skills/`), installed in the organisation when a project is born from the
template. The workflows call five **organisation** connectors, by name; declaring them is the
organisation administrator's job:

| Name | Capability | For |
|:--|:--|:--|
| `directory` | `identity` (`entra`) | the account, its groups, its sessions |
| `devices` | `mdm` | enrolling and wiping the laptop |
| `carrier` | `shipping` | shipping and collecting the laptop |
| `badge-readers` | `access_control` | the badge |
| `supplier` | `mcp` | ordering the laptop from the supplier's agent |

A request labelled `offboarding` or `leaver` is born in the offboarding workflow; any other, in
the onboarding one.

## Version 2.0.0: English identifiers

Since 2.0.0 every identifier the template ships is English: agent and skill slugs, connector names,
states, actors, input names, outputs, transition ids, action kinds. A project created
from an older version keeps its own: renaming a state under a running work item is not possible,
and the published workflow versions are never rewritten. `tools/rename_tenant_names.py` re-publishes
an existing tenant's French names as new English versions (`docs/runbooks/rename-tenant-names.md`).
