---
name: profils-d-acces
description: The access groups of each job, and the sensitive groups that need a stronger approval.
---

# Access profiles

A job opens an **access group**; some also open a **sensitive group**, granted only on a
re-authenticated approval.

| Job | Access group | Sensitive group |
|:--|:--|:--|
| Developer | `devs` | `prod-lecture` |
| Operations engineer | `ops` | `prod-admin` |
| Accountant | `finance` | `paie` |
| Recruiter | `rh` | `dossiers-salaries` |
| Sales | `ventes` | `crm-export` |

## Rules

- A job missing from this table has **no** sensitive group by default: say so in the plan.
- A requested group that does not match the job is a **mismatch**: report it, with the expected
  group.
- When someone leaves, every group — sensitive ones included — closes with the account: disabling
  it is enough, no group-by-group removal is needed.
