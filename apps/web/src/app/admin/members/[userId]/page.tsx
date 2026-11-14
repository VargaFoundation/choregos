// SPDX-License-Identifier: Apache-2.0
"use client";

import { use } from "react";
import { MemberProfileView } from "@/components/member-profile";
import { Empty } from "@/components/ui";
import { canManageGroups } from "@/lib/memberships";
import { useSession } from "@/lib/session";

/** One member's profile (S66-42, ADR 0162): reached from the members list, and from My account for oneself. */
export default function MemberProfilePage({ params }: { params: Promise<{ userId: string }> }) {
  const { userId } = use(params);
  const { me, org } = useSession();
  if (!org) return <Empty>Reading your session…</Empty>;
  const id = decodeURIComponent(userId);
  return (
    <MemberProfileView
      org={org}
      userId={id}
      // An organisation administrator manages members (and their linked accounts).
      canManage={canManageGroups(me?.memberships ?? [], org)}
      isSelf={me?.id === id}
    />
  );
}
