// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { WorkflowChip } from "@/components/workflow-chip";
import { DEFAULT_WORKFLOW_HINT } from "@/components/workflows/timing";
import { usePathname } from "next/navigation";
import { use, type ReactNode } from "react";
import { Badge, Tabs, tabClasses } from "@/components/ui";
import { BarreDuBrouillon } from "@/components/workflows/barre-du-brouillon";
import { BrouillonProvider } from "@/components/workflows/brouillon";
import { useWorkflow } from "@/components/workflows/use-workflow";

// The graph first (S55-06): a workflow is read as a picture, the list of steps is one tab away.
const VUES = [
  { suffix: "", label: "Graph" },
  { suffix: "/steps", label: "Steps" },
  { suffix: "/yaml", label: "YAML" },
  { suffix: "/history", label: "History" },
];

/** Un workflow du projet, par son nom : son processus, sa carte, son YAML, son historique. */
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
      {/* Le brouillon est commun à la carte et à la vue processus : un geste sur l'une se voit sur
          l'autre, et rien n'est publié avant « publish ». */}
      <BrouillonProvider slug={slug} name={name}>
        <BarreDuBrouillon />
        {children}
      </BrouillonProvider>
    </div>
  );
}
