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

## The registry (`ontology/`)

When the ontology plugin is active (`CHOREGOS_ESSAI_ONTOLOGIE=1`), the project is born with the
registry of what an onboarding opens and what an offboarding must close: `employee`,
`contract`, `account` (and its groups), `group`, `device`, `badge`, and their links. An HR
person's Claude queries it through the project's MCP door (`employee_search`,
`badge_holder`…). Without the plugin, the project is born without a registry, and the audit log
says so.

The registry is kept by ontology actions, played like any governed action:

| Action | Risk | Who decides |
|:--|:--|:--|
| `record_joiner` | low | the policy |
| `record_account`, `record_device`, `record_badge` | low | the policy |
| `record_leaver` (account, badge and laptop closed in the registry) | medium | a manager, re-authenticated |

The workflows do not write to the registry yet: they act on the directory, the fleet and the
readers. Recording it is the agent's or HR's job, until the daily reconciliation exists.

## Version 2.0.0: English identifiers

Since 2.0.0 every identifier the template ships is English: agent and skill slugs, connector names,
states, actors, input names, outputs, transition ids, action kinds, the ontology. A project created
from an older version keeps its own: renaming a state under a running work item is not possible,
and the published workflow versions are never rewritten. `tools/rename_tenant_names.py` re-publishes
an existing tenant's French names as new English versions (`docs/runbooks/rename-tenant-names.md`).
