// SPDX-License-Identifier: Apache-2.0
"use client";

import { OutpostsList } from "@/components/outposts/outposts-list";
import { Empty } from "@/components/ui";
import { useSession } from "@/lib/session";

/**
 * The organisation's outposts: places a person enrols, which only dial out to the platform. The
 * page is the organisation administrator's; for anyone else the API's refusal is what it shows.
 */
export default function OutpostsPage() {
  const { org } = useSession();
  if (!org) return <Empty>Reading your session…</Empty>;
  return <OutpostsList org={org} />;
}
