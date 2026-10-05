// SPDX-License-Identifier: Apache-2.0
"use client";

import dynamic from "next/dynamic";
import { use } from "react";
import { Card, Empty, ErrorNote } from "@/components/ui";
import { WorkflowLegend } from "@/components/workflow-graph";
import { useWorkflow } from "@/components/workflows/use-workflow";

// React Flow pèse lourd : chargé à l'ouverture de la carte, et d'elle seule.
const WorkflowGraph = dynamic(
  () => import("@/components/workflow-graph").then((m) => m.WorkflowGraph),
  {
    ssr: false,
    loading: () => <p className="text-sm text-ink-muted">rendering the map…</p>,
  },
);

/** La carte : les états en couloirs, un par sorte d'acteur ; les défauts dits une fois, en légende. */
export default function MapPage({
  params,
}: {
  params: Promise<{ slug: string; name: string }>;
}) {
  const { slug, name } = use(params);
  const { definition, validation } = useWorkflow(
    slug,
    decodeURIComponent(name),
  );
  if (definition.error)
    return <ErrorNote>{String(definition.error)}</ErrorNote>;
  const graph = validation.data?.graph;
  if (!graph) return <Empty>the map appears once the workflow is read…</Empty>;
  return (
    <Card title="map">
      <div className="space-y-3">
        <WorkflowLegend graph={graph} />
        <WorkflowGraph graph={graph} />
      </div>
    </Card>
  );
}
