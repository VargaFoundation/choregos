// SPDX-License-Identifier: Apache-2.0
"use client";

import { GroupsList } from "@/components/groups/groups-list";
import { Empty } from "@/components/ui";
import { canManageGroups } from "@/lib/memberships";
import { useSession } from "@/lib/session";

/**
 * The organisation's groups (ADR 0160): every member reads them, the administrator creates and fills
 * the `internal` ones. A group the identity provider pushes is shown read-only.
 */
export default function GroupsPage() {
  const { me, org } = useSession();
  if (!org) return <Empty>Reading your session…</Empty>;
  return <GroupsList org={org} canManage={canManageGroups(me?.memberships ?? [], org)} />;
}
