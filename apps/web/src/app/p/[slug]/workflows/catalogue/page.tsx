// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useState } from "react";
import { Badge, Button, Card, Empty, ErrorNote, EtatDeLecture, Field, Input, Select } from "@/components/ui";
import {
  filterEntries,
  readinessPreview,
  RESOLUTION_TEXT,
  sourceLabel,
  unresolved,
  type RepositoryFilter,
} from "@/components/workflows/catalogue";
import { ApiError, api } from "@/lib/api";
import type { ConnectorDto, WorkflowCatalogueEntry } from "@/lib/types";

const NAME = /^[a-z][a-z0-9-]{0,62}$/;

/**
 * The workflow catalogue (ADR 0108): what this project can adopt — the platform's workflows, the
 * templates', the installed plugins' and the ones the organisation shared — with what each needs and
 * where its agents come from. Adopting copies the workflow into the project and installs the agents the
 * platform's catalogue provides; it never installs a plugin.
 */
export default function WorkflowCataloguePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const project = useQuery({ queryKey: ["project", slug], queryFn: () => api.project(slug) });
  const org = project.data?.org;
  const catalogue = useQuery({
    queryKey: ["workflow-catalogue", org],
    queryFn: () => api.workflowCatalogue(org!),
    enabled: !!org,
  });
  const connectors = useQuery({ queryKey: ["connectors", slug], queryFn: () => api.connectors(slug) });
  const hasRepository = !!project.data?.config?.repo?.url;
  const [query, setQuery] = useState("");
  const [repository, setRepository] = useState<RepositoryFilter | null>(null);
  // Without a repository, the catalogue opens on what such a project can run (ADR 0107).
  const filter: RepositoryFilter = repository ?? (project.data && !hasRepository ? "without" : "any");
  const shown = filterEntries(catalogue.data ?? [], query, filter);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <p className="max-w-2xl text-sm text-ink-muted">
          Workflows this project can adopt. Adopting copies one into the project — later changes to the catalogue are
          offered, never applied — and installs the agents the platform provides. A plugin is never installed from here.
        </p>
        <Link href={`/p/${slug}/workflows`} className="text-sm underline">
          Back to the project&apos;s workflows
        </Link>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Search">{(p) => <Input {...p} value={query} onChange={(e) => setQuery(e.target.value)} />}</Field>
        <Field label="Repository">
          {(p) => (
            <Select {...p} value={filter} onChange={(e) => setRepository(e.target.value as RepositoryFilter)}>
              <option value="any">Any workflow</option>
              <option value="without">Needs no repository</option>
              <option value="with">Delivers code from a repository</option>
            </Select>
          )}
        </Field>
      </div>
      {!catalogue.data && <EtatDeLecture lecture={catalogue} quoi="the workflow catalogue" />}
      {catalogue.data && shown.length === 0 && <Empty title="No workflow">Nothing in the catalogue matches.</Empty>}
      <ul className="grid gap-4 md:grid-cols-2" aria-label="Workflow catalogue">
        {shown.map((entry) => (
          <li key={`${entry.source}/${entry.name}`}>
            <CatalogueCard slug={slug} entry={entry} connectors={connectors.data ?? []} hasRepository={hasRepository} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function CatalogueCard({
  slug,
  entry,
  connectors,
  hasRepository,
}: {
  slug: string;
  entry: WorkflowCatalogueEntry;
  connectors: ConnectorDto[];
  hasRepository: boolean;
}) {
  const [open, setOpen] = useState(false);
  const missing = unresolved(entry);
  return (
    <Card title={entry.title} eyebrow={sourceLabel(entry.source)}>
      <div className="space-y-3 text-sm">
        {entry.summary && <p>{entry.summary}</p>}
        <div className="flex flex-wrap gap-2">
          <Badge tone={entry.needs_repository ? "neutral" : "ok"}>
            {entry.needs_repository ? "Needs a repository" : "Needs no repository"}
          </Badge>
          {entry.tags.map((tag) => (
            <Badge key={tag} dot={false}>
              {tag}
            </Badge>
          ))}
        </div>
        {entry.agents.length > 0 && (
          <ul className="space-y-1" aria-label={`Agents of ${entry.name}`}>
            {entry.agents.map((agent) => (
              <li key={agent.actor} className={agent.resolution === "unresolved" ? "text-danger" : undefined}>
                <code>{agent.slug}</code> — {RESOLUTION_TEXT[agent.resolution]}
                {agent.plugin ? ` (${agent.plugin})` : ""}
              </li>
            ))}
          </ul>
        )}
        {entry.groups.length > 0 && (
          <p className="text-ink-muted">People: {entry.groups.map((g) => `@${g}`).join(", ")}</p>
        )}
        {entry.needs_repository && !hasRepository && (
          <p className="text-warn">This project names no repository: this workflow could not run here.</p>
        )}
        {missing.length > 0 ? (
          <p className="text-ink-muted">
            Cannot be adopted until an organisation administrator installs the plugin that ships{" "}
            {missing.map((a) => a.slug).join(", ")}.
          </p>
        ) : open ? (
          <AdoptForm slug={slug} entry={entry} connectors={connectors} onCancel={() => setOpen(false)} />
        ) : (
          <Button tone="accent" onClick={() => setOpen(true)}>
            Adopt {entry.name}
          </Button>
        )}
      </div>
    </Card>
  );
}

function AdoptForm({
  slug,
  entry,
  connectors,
  onCancel,
}: {
  slug: string;
  entry: WorkflowCatalogueEntry;
  connectors: ConnectorDto[];
  onCancel: () => void;
}) {
  const router = useRouter();
  const client = useQueryClient();
  const [name, setName] = useState(entry.name);
  const [makeDefault, setMakeDefault] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const preview = readinessPreview(entry, connectors);
  const valid = NAME.test(name);

  async function adopt() {
    setBusy(true);
    setError(null);
    try {
      await api.adoptWorkflow(slug, {
        source: entry.source,
        name: entry.name,
        version: entry.version,
        as: name === entry.name ? null : name,
        make_default: makeDefault,
      });
      await Promise.all(
        [["workflows", slug], ["workflow-routing", slug], ["readiness", slug]].map((queryKey) =>
          client.invalidateQueries({ queryKey }),
        ),
      );
      router.push(`/p/${slug}/workflows/${encodeURIComponent(name)}`);
    } catch (cause) {
      setError(cause instanceof ApiError || cause instanceof Error ? cause.message : "adoption refused");
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 border-t border-line pt-3">
      <ul className="space-y-1" aria-label={`What ${entry.name} needs here`}>
        {preview.map((line) => (
          <li key={line.capability} className="flex flex-wrap items-baseline gap-2">
            <Badge tone={line.state === "missing" ? "warn" : "ok"} variant="plain">
              {line.capability}: {line.state}
            </Badge>
            <span className="text-xs text-ink-muted">{line.reason}</span>
          </li>
        ))}
      </ul>
      <Field label="Name in this project" error={name && !valid ? "Lowercase letters, digits and dashes only" : undefined}>
        {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} />}
      </Field>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={makeDefault} onChange={(e) => setMakeDefault(e.target.checked)} />
        Make it the project&apos;s default workflow
      </label>
      {error && <ErrorNote>{error}</ErrorNote>}
      <div className="flex gap-2">
        <Button tone="accent" onClick={() => void adopt()} disabled={!valid || busy}>
          Adopt as {name || "…"}
        </Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}
