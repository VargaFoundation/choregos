// SPDX-License-Identifier: Apache-2.0
"use client";

import { use } from "react";
import { OutpostView } from "@/components/outposts/outpost-view";
import { Empty } from "@/components/ui";
import { useSession } from "@/lib/session";

/** One outpost: its instances and their approval, the keys to pin, the recent calls routed to it. */
export default function OutpostPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const { org } = useSession();
  if (!org) return <Empty>Reading your session…</Empty>;
  return <OutpostView org={org} slug={decodeURIComponent(slug)} />;
}
