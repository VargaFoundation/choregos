// SPDX-License-Identifier: Apache-2.0
"use client";

import { use } from "react";
import { InboxView } from "@/components/inbox-view";

/** The project's own inbox (S39-04): what waits for a person in this project only. */
export default function ProjectInboxPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  return <InboxView project={slug} heading="h2" />;
}
