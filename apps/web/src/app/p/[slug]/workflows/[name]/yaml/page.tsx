// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { use, useEffect, useState } from "react";
import { Button, Card, Empty, ErrorNote } from "@/components/ui";
import { useWorkflow } from "@/components/workflows/use-workflow";
import { ApiError, api } from "@/lib/api";
import type { WorkflowValidation } from "@/lib/types";

// L'éditeur (CodeMirror) n'est chargé qu'à l'ouverture de l'onglet : la page n'en paie pas le poids avant.
const YamlEditor = dynamic(
  () => import("@/components/yaml-editor").then((m) => m.YamlEditor),
  {
    ssr: false,
    loading: () => (
      <p className="text-sm text-ink-muted">loading the editor…</p>
    ),
  },
);

/**
 * Le YAML du workflow, validé à chaque frappe par l'API (le code de l'orchestrateur). Enregistrer
 * publie la version suivante SOUS CE NOM, en disant laquelle on a lue : si quelqu'un a publié
 * entre-temps, l'API refuse (409) au lieu d'écraser son travail.
 */
export default function YamlPage({
  params,
}: {
  params: Promise<{ slug: string; name: string }>;
}) {
  const { slug, name: brut } = use(params);
  const name = decodeURIComponent(brut);
  const { definition } = useWorkflow(slug, name);
  const client = useQueryClient();
  const [yaml, setYaml] = useState("");
  const [report, setReport] = useState<WorkflowValidation | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  // Amorçage depuis le serveur, ajusté pendant le rendu plutôt que dans un effet (pas de rendu en
  // cascade : l'éditeur ne s'affiche pas vide pour se remplir ensuite).
  const [seeded, setSeeded] = useState<string | null>(null);
  if (definition.data?.yaml && definition.data.yaml !== seeded) {
    setSeeded(definition.data.yaml);
    setYaml(definition.data.yaml);
  }

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
      const saved = await api.putWorkflowNamed(
        slug,
        name,
        yaml,
        definition.data?.version,
      );
      setMessage(
        `${saved.name} v${saved.version} published — running items finish on their version`,
      );
      await client.invalidateQueries({ queryKey: ["workflow", slug, name] });
      await client.invalidateQueries({ queryKey: ["workflows", slug] });
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) {
        setMessage(
          "someone published a newer version meanwhile: reload to edit it, your text is still here",
        );
      } else {
        setMessage(
          cause instanceof Error ? cause.message : "publication refused",
        );
      }
    } finally {
      setSaving(false);
    }
  }

  if (definition.error)
    return <ErrorNote>{String(definition.error)}</ErrorNote>;
  if (!definition.data) return <Empty>reading the workflow…</Empty>;
  return (
    <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
      <Card
        title={`${name} — YAML`}
        action={
          <Button
            tone="primary"
            onClick={save}
            disabled={saving || report?.valid === false || yaml === seeded}
          >
            publish v{(definition.data.version ?? 0) + 1}
          </Button>
        }
      >
        <YamlEditor
          label={`YAML of ${name}`}
          value={yaml}
          onChange={setYaml}
          issues={report?.errors ?? []}
          warnings={report?.warnings ?? []}
        />
        {message && (
          <p className="mt-2 text-sm" role="status">
            {message}
          </p>
        )}
      </Card>
      <Card title="validation" className="self-start">
        <div data-testid="validation-yaml">
        {!report && <Empty>validating…</Empty>}
        {report?.valid && <p className="text-sm text-ok">✓ valid workflow</p>}
        {report?.errors?.map((issue, index) => (
          <ErrorNote key={index}>
            {issue.message}
            {issue.line ? ` — line ${issue.line}` : ""}{" "}
            {issue.path ? `(${issue.path})` : ""}
          </ErrorNote>
        ))}
        {report?.warnings?.map((issue, index) => (
          <p
            key={index}
            className="mt-2 rounded border border-line border-l-2 border-l-warn bg-surface px-3 py-2 text-sm text-warn"
          >
            {issue.message}
          </p>
        ))}
        </div>
      </Card>
    </div>
  );
}
