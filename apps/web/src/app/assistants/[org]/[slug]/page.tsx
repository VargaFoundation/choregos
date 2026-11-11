// SPDX-License-Identifier: Apache-2.0
"use client";

import { use } from "react";
import { AssistantWorkspace } from "@/components/assistants/assistant-workspace";

/** One assistant, for me: memory, connected accounts, tasks, inbox, sources (S63-18). */
export default function AssistantPage({ params }: { params: Promise<{ org: string; slug: string }> }) {
  const { org, slug } = use(params);
  return <AssistantWorkspace org={org} slug={slug} />;
}
