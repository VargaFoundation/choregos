// SPDX-License-Identifier: Apache-2.0
"use client";

import { use } from "react";
import { ActivityView } from "@/components/activity/activity-view";
import { useSession } from "@/lib/session";

/** What is running in this project, and what has just finished (S64-06): the organisation's list, narrowed. */
export default function ProjectActivityPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const { org } = useSession();
  return <ActivityView org={org} project={slug} />;
}
