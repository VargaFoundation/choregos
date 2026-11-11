// SPDX-License-Identifier: Apache-2.0
"use client";

import { useSearchParams } from "next/navigation";
import { use } from "react";
import { ConversationView } from "@/components/assistants/conversation-view";

/** One conversation; `?assistant=org/slug` says whose memory proposals to show (S63-18). */
export default function ConversationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const assistant = useSearchParams().get("assistant") ?? undefined;
  return <ConversationView id={id} assistant={assistant} />;
}
