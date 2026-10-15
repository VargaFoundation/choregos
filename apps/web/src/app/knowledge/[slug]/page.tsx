// SPDX-License-Identifier: Apache-2.0
"use client";

import { use } from "react";
import { KnowledgeBaseView } from "@/components/knowledge-base-view";

/** One knowledge base, from the organisation's registry. */
export default function KnowledgeBasePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  return <KnowledgeBaseView slug={slug} back={{ href: "/knowledge", label: "Knowledge" }} />;
}
