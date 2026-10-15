// SPDX-License-Identifier: Apache-2.0
"use client";

import { use } from "react";
import { KnowledgeBaseView } from "@/components/knowledge-base-view";

/** One of the knowledge bases a project's runs are served, opened inside the project (S39-14). */
export default function ProjectKnowledgeBasePage({ params }: { params: Promise<{ slug: string; kb: string }> }) {
  const { slug, kb } = use(params);
  return (
    <KnowledgeBaseView slug={kb} back={{ href: `/p/${slug}/knowledge`, label: "Knowledge" }} headingLevel="h2" />
  );
}
