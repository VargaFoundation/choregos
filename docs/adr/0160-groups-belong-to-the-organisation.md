# 0160 — Groups belong to the organisation

- **Status**: proposed, 2026-11-12
- **Concerns**: the tables `org_groups` and `org_group_members`
  (`apps/api/src/choregos_api/db/models/identity.py`, migration `20261114_1000_org_groups.py`), the routes
  `/orgs/{org}/groups…` (`routers/org_groups.py`), `services/org_groups.py`, the principal
  (`deps.py`, `_principal_from_user`), `services/human_groups.py` (`groups_known_in`, `members_of_group`),
  the permission `group:manage` (`rbac.py`), the console's **Groups** page; builds on
  [ADR 0044](0044-people-are-declared-in-the-workflow.md) and [ADR 0052](0052-netops-connectors.md) (a group
  named by a human actor or an approval rule), leaves the identity provider's mapping of roles unchanged

## Context

A workflow addresses a human step to a group (`actors.<id>: {type: human, group: release-captains}`), an
action's approval rule names the groups that may decide, a knowledge base is opened to groups, an
assistant has owner and audience groups. All of them read `Principal.groups` through
`services/human_groups.py::groups_for_org`, which also accepts a group namespaced for one organisation,
`choregos:<org>:<group>`.

Today those groups come from one place only: the identity provider's `groups` claim, copied into
`User.groups` at each sign-in (`routers/auth.py`). There is no group object in the application. So:

- an organisation whose identity provider sends no `groups` claim, or sends groups it cannot change
  quickly, cannot address a step to anyone but by role, and the readiness warning
  `actor.group_unknown` stays on for good;
- nobody in the application can say who is in `release-captains`; the answer is "whoever the identity
  provider sent at their last sign-in";
- an identity provider that pushes groups (SCIM) has nowhere to push them.

## Decision

1. **An organisation has first-class groups**, in `org_groups` (one row per group, `slug` unique in the
   organisation, matching `^[a-z][a-z0-9-]{1,62}$`) and `org_group_members` (one row per person and group).
   Both tables are under forced row-level security with the organisation isolation policy every
   organisation table carries.
2. **Each group has exactly one source**, `source`:
   - `internal`: created, renamed, emptied and deleted through the API by an organisation administrator
     (the new permission `group:manage`, which no other role holds);
   - `scim`: pushed by an identity provider, found by its `external_id` in the organisation. The core
     ships no SCIM endpoint: it exposes `services.org_groups.upsert_external_group(session, org_id, *,
     external_id, slug, name, members_user_ids, actor)`, which creates or updates the group and replaces
     its members, for whatever serves SCIM to call. Every write on a `scim` group through the core API
     answers 409 `group_read_only`: the group is changed where it comes from.
3. **The identity provider's claim is a third source, read as before.** `User.groups` is not touched.
4. **A person carries the organisation's groups like a namespaced claim.** The principal's `groups` is the
   claim as sent, then `choregos:<org>:<group>` for each group of each organisation the person is a member
   of: one more query when the principal is built. Every check that already reads `groups_for_org`
   (human steps, approvers, knowledge, assistants) sees them with no change of its own; the principals
   built to act on a person's behalf (an assistant's task, a delegation, knowledge read on behalf) carry
   them too.
5. **A group row counts only while its person is a member of the organisation** (a role in it or on one
   of its projects). Leaving the organisation takes its groups away with no second gesture.
6. **What the organisation knows includes its groups that have a member.** `groups_known_in` (the
   readiness warning `actor.group_unknown`) and `members_of_group` (who may answer a step) count table
   memberships. An empty group is carried by nobody, so it still warns.
7. **Every member of the organisation reads its groups and their members**; only the administrator writes
   them. Every write is audited: `group.create`, `group.update`, `group.delete`, `group.member.add`,
   `group.member.remove`.

## Consequences

- An organisation can address steps to a group without touching its identity provider, and the console
  says who is in it.
- A group may now be carried twice (from the claim and from the table); both read as the same bare name
  for the organisation, so nothing double-counts.
- Building a principal costs one more query.
- A slug is what workflows name: it does not change through `PATCH` (only the name and description do).
  An identity provider may rename a `scim` group's slug, and a workflow that named the old slug then
  warns again.
- An `internal` group is never taken over by a push: a SCIM group whose slug is taken is refused.

## What this does not do

- It ships no SCIM server, no SCIM schema and no provisioning of users: only the function such a server
  would call.
- It does not map groups to roles: roles still come from memberships and from the identity provider's
  `choregos:<org>:<role>` groups, as before.
- It does not nest groups, give a group a role on a project, or let a project own groups.
- It does not assign a step to one person (a separate decision).
- It does not import the claim's groups into the table, nor let an administrator edit the claim.
