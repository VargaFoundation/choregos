"use client";

import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { use, useEffect, useState } from "react";
import { Button, Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import type { WorkflowValidation } from "@/lib/types";

// Monaco et React Flow pèsent lourd : ils sont chargés à l'ouverture de cette page,
// et d'elle seule. Le board et les runs n'en paient pas le prix.
const YamlEditor = dynamic(() => import("@/components/yaml-editor").then((m) => m.YamlEditor), {
  ssr: false,
  loading: () => <p className="text-sm text-ink-muted">éditeur en cours de chargement…</p>,
});
const WorkflowGraph = dynamic(
  () => import("@/components/workflow-graph").then((m) => m.WorkflowGraph),
  { ssr: false, loading: () => <p className="text-sm text-ink-muted">graphe en cours de rendu…</p> },
);

/**
 * Éditeur de workflow : YAML à gauche, graphe à droite. La validation est faite par
 * l'API (le même code que l'orchestrateur), donc ce qu'on voit ici est ce qui s'appliquera.
 */
export default function WorkflowPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const current = useQuery({ queryKey: ["workflow", slug], queryFn: () => api.workflow(slug) });
  const [yaml, setYaml] = useState("");
  const [report, setReport] = useState<WorkflowValidation | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (current.data?.yaml) setYaml(current.data.yaml);
  }, [current.data?.yaml]);

  useEffect(() => {
    if (!yaml) return;
    const timer = setTimeout(async () => {
      try {
        setReport(await api.validateWorkflow(yaml));
      } catch {
        setReport(null);
      }
    }, 500);
    return () => clearTimeout(timer);
  }, [yaml]);

  async function save() {
    setSaving(true);
    setMessage(null);
    try {
      const saved = await api.putWorkflow(slug, yaml);
      setMessage(`workflow ${saved.name} v${saved.version} enregistré`);
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "enregistrement refusé");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card
        title="Définition (YAML)"
        action={
          <Button tone="primary" onClick={save} disabled={saving || report?.valid === false}>
            Enregistrer
          </Button>
        }
      >
        <YamlEditor
          label="Workflow YAML"
          value={yaml}
          onChange={setYaml}
          issues={report?.errors ?? []}
          warnings={report?.warnings ?? []}
        />
        {message && <p className="mt-2 text-sm">{message}</p>}
      </Card>

      <div className="space-y-4">
        <Card title="Validation">
          {!report && <Empty>validation en cours…</Empty>}
          {report?.valid && <p className="text-sm text-ok">✓ workflow valide</p>}
          {report?.errors?.map((issue, index) => (
            <ErrorNote key={index}>
              {issue.message}
              {issue.line ? ` — ligne ${issue.line}` : ""} {issue.path ? `(${issue.path})` : ""}
            </ErrorNote>
          ))}
          {report?.warnings?.map((issue, index) => (
            <p key={index} className="mt-2 rounded border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">
              {issue.message}
            </p>
          ))}
        </Card>

        <Card title="Graphe">
          {report?.graph ? (
            <>
              <WorkflowGraph graph={report.graph} />
              <Lanes graph={report.graph} />
            </>
          ) : (
            <Empty>le graphe apparaît dès que le workflow est valide</Empty>
          )}
        </Card>
      </div>
    </div>
  );
}

function Lanes({ graph }: { graph: NonNullable<WorkflowValidation["graph"]> }) {
  const lanes = ["agent", "human", "system", "train", "wait", "terminal"];
  const nodes = graph.nodes as Array<{ id: string; display: string; lane?: string; terminal?: boolean }>;
  const edges = graph.edges as Array<{
    id?: string;
    from: string;
    to: string;
    kind?: string;
    label?: string;
    actor?: string | null;
    gates?: string[];
  }>;
  return (
    <details className="mt-3 space-y-4">
      <summary className="cursor-pointer text-xs text-ink-muted">détail par couloir</summary>
      {lanes.map((lane) => {
        const inLane = nodes.filter((node) => (node.lane ?? "system") === lane);
        if (inLane.length === 0) return null;
        return (
          <div key={lane}>
            <p className="mb-1 text-xs uppercase tracking-wide text-ink-muted">{lane}</p>
            <div className="flex flex-wrap gap-2">
              {inLane.map((node) => (
                <span
                  key={node.id}
                  title={node.id}
                  className="rounded border border-line bg-surface-muted px-2 py-1 text-xs"
                >
                  {node.display}
                </span>
              ))}
            </div>
          </div>
        );
      })}
      <div>
        <p className="mb-1 text-xs uppercase tracking-wide text-ink-muted">transitions</p>
        <ul className="space-y-1 font-mono text-xs text-ink-muted">
          {edges.map((edge, index) => (
            <li key={edge.id ?? index} className={edge.kind && edge.kind !== "nominal" ? "opacity-70" : ""}>
              {edge.from} {edge.kind && edge.kind !== "nominal" ? "⇢" : "→"} {edge.to}
              {edge.actor ? ` · ${edge.actor}` : ""}
              {edge.kind && edge.kind !== "nominal" && edge.label ? ` · ${edge.label}` : ""}
              {edge.gates?.length ? ` [${edge.gates.join(", ")}]` : ""}
            </li>
          ))}
        </ul>
      </div>
    </details>
  );
}
