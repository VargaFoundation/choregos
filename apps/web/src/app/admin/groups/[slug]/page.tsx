// SPDX-License-Identifier: Apache-2.0
"use client";

import { use } from "react";
import { GroupView } from "@/components/groups/group-view";
import { Empty } from "@/components/ui";
import { canManageGroups } from "@/lib/memberships";
import { useSession } from "@/lib/session";

/** One group of the organisation: who is in it, and, for an internal group, adding and removing them. */
export default function GroupPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const { me, org } = useSession();
  if (!org) return <Empty>Reading your session…</Empty>;
  return <GroupView org={org} slug={decodeURIComponent(slug)} canManage={canManageGroups(me?.memberships ?? [], org)} />;
}
