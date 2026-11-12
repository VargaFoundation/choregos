// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { WorkflowChip } from "@/components/workflow-chip";
import { DEFAULT_WORKFLOW_HINT } from "@/components/workflows/timing";
import { usePathname } from "next/navigation";
import { use, type ReactNode } from "react";
import { Badge, Tabs, tabClasses } from "@/components/ui";
import { DraftBar } from "@/components/workflows/draft-bar";
import { DraftProvider } from "@/components/workflows/draft";
import { useWorkflow } from "@/components/workflows/use-workflow";

// The graph first (S55-06): a workflow is read as a picture, the list of steps is one tab away.
const VUES = [
  { suffix: "", label: "Graph" },
  { suffix: "/steps", label: "Steps" },
  { suffix: "/yaml", label: "YAML" },
  { suffix: "/history", label: "History" },
  { suffix: "/spend", label: "Tokens and cost" },
];

/** A project's workflow, by its name: its process, its map, its YAML, its history. */
export default function WorkflowLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ slug: string; name: string }>;
}) {
  const { slug, name: brut } = use(params);
  const name = decodeURIComponent(brut);
  const pathname = usePathname();
  const { definition } = useWorkflow(slug, name);
  const base = `/p/${slug}/workflows/${encodeURIComponent(name)}`;
  return (
    <div className="space-y-6">
      {/* The trail above already says "Workflows / <name>" (S55-02): here the workflow wears its colour. */}
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="flex min-w-0">
          <WorkflowChip name={name} version={definition.data?.version} size="md" />
        </h2>
        {definition.data?.is_default && (
          <Badge tone="neutral" title={DEFAULT_WORKFLOW_HINT}>
            Default
          </Badge>
        )}
      </div>
      <Tabs aria-label={`Views of ${name}`}>
        {VUES.map((vue) => {
          const href = `${base}${vue.suffix}`;
          const active = pathname === href;
          return (
            <Link
              key={vue.suffix}
              href={href}
              aria-current={active ? "page" : undefined}
              className={tabClasses(active)}
            >
              {vue.label}
            </Link>
          );
        })}
      </Tabs>
      {/* The draft is shared by the map and the process view: a gesture on one shows on
              the other, and nothing is published before "publish". */}
      <DraftProvider slug={slug} name={name}>
        <DraftBar />
        {children}
      </DraftProvider>
    </div>
  );
}
