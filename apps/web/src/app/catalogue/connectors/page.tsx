// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { CatalogueCard, CatalogueGrid } from "@/components/catalogue/catalogue-grid";
import { EnablementDrawer } from "@/components/catalogue/enablement-drawer";
import { CatalogueFacets } from "@/components/catalogue/facets";
import { ConnectorIcon } from "@/components/connector-icon";
import { Badge, Button, EtatDeLecture } from "@/components/ui";
import { api } from "@/lib/api";
import { NO_FILTERS, type CatalogueFilters } from "@/lib/catalogue";
import { filterConnectorEntries } from "@/lib/connector-catalogue";
import { useSession } from "@/lib/session";
import type { ConnectorCatalogueEntry, ConnectorEnablement } from "@/lib/types";

const TOGGLES = [
  { key: "organisation", label: "Organisation connectors" },
  { key: "project", label: "Project connectors" },
];

/** The families in the order the server sent them, each once. */
function familiesOf(entries: ConnectorCatalogueEntry[]): string[] {
  return [...new Set(entries.map((entry) => entry.family))];
}

/**
 * The connectors the platform can reach (ADR 0074), by family. An organisation connector is installed
 * once by the administrator and enabled per project; a project connector is configured in the
 * project's settings. What the caller may do comes from the server (`can`, `cannot_reason`), never
 * from a role guessed here.
 */
export default function ConnectorCataloguePage() {
  const { org } = useSession();
  const catalogue = useQuery({ queryKey: ["connector-catalogue", org], queryFn: () => api.connectorCatalogue(org) });
  const [filters, setFilters] = useState<CatalogueFilters>(NO_FILTERS);
  const [enabling, setEnabling] = useState<{ enablement: ConnectorEnablement; projects: string[] } | null>(null);
  const entries = catalogue.data?.entries ?? [];
  const shown = filterConnectorEntries(entries, filters);
  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold">Connector types</h1>
      <p className="max-w-3xl text-sm text-ink-muted">
        The systems Choregos can reach, by family. Installing one happens in Administration → Installed connectors. An organisation connector is declared once by the administrator and
        turned on for the projects that need it; a project connector is configured in the project&apos;s settings.
        MCP servers have{" "}
        <Link href="/catalogue/mcp-servers" className="underline">
          their own catalogue
        </Link>
        .
      </p>
      <EtatDeLecture lecture={catalogue} quoi="the catalogue" />
      {catalogue.data && (
        <>
          <CatalogueFacets
            filters={filters}
            onChange={setFilters}
            categories={familiesOf(entries)}
            toggles={TOGGLES}
            shown={shown.length}
            total={entries.length}
            noun="connectors"
          />
          {shown.length === 0 ? (
            <CatalogueGrid items={shown} keyOf={(e) => e.type} empty="Loosen a filter, or search for another word." render={() => null} />
          ) : (
            familiesOf(shown).map((family) => (
              <section key={family} aria-labelledby={`family-${family}`} className="space-y-3" data-testid={`family-${family}`}>
                <h2 id={`family-${family}`} className="text-base font-semibold">
                  {family}
                </h2>
                <CatalogueGrid
                  items={shown.filter((entry) => entry.family === family)}
                  keyOf={(entry) => `${entry.kind}/${entry.type}`}
                  empty=""
                  render={(entry) => (
                    <Entry
                      entry={entry}
                      onProjects={(enablement) => setEnabling({ enablement, projects: entry.can.enable_projects })}
                    />
                  )}
                />
              </section>
            ))
          )}
        </>
      )}
      {enabling && (
        <EnablementDrawer
          org={org}
          enablement={enabling.enablement}
          projects={enabling.projects}
          onClose={() => setEnabling(null)}
        />
      )}
    </div>
  );
}

function where(item: ConnectorEnablement): string {
  if (item.availability === "all_projects") return "on for every project";
  if (item.enabled_projects.length === 0) return "on in no project yet";
  return `on in ${item.enabled_projects.join(", ")}`;
}

function Entry({ entry, onProjects }: { entry: ConnectorCatalogueEntry; onProjects: (enablement: ConnectorEnablement) => void }) {
  const id = `${entry.kind}-${entry.type}`;
  const project = entry.scope === "project";
  const selected = entry.enablement.filter((item) => item.availability === "selected_projects");
  const canDeclare = !project && entry.can.install;
  const canConfigure = project && entry.can.configure_projects.length > 0;
  const canEnable = entry.can.enable_projects.length > 0 && selected.length > 0;
  return (
    <CatalogueCard
      id={id}
      titleLevel="h3"
      icon={<ConnectorIcon kind={entry.kind} type={entry.type} className="shrink-0 text-ink-muted" />}
      title={entry.display}
      publisher={`${entry.kind}/${entry.type}`}
      description={
        project
          ? "Configured in a project's settings, by its owner."
          : "Declared once in the organisation by its administrator, then turned on for projects."
      }
      badges={
        <>
          <Badge tone={project ? "neutral" : "accent"}>{project ? "Project" : "Organisation"}</Badge>
          {entry.installed_as.length > 0 && <Badge tone="ok">Installed</Badge>}
        </>
      }
      facts={
        <>
          {entry.capabilities.length > 0 && <p>Capabilities: {entry.capabilities.join(", ")}</p>}
          {entry.operations > 0 && <p>{entry.operations} operations an agent can call</p>}
          {entry.secret_fields.length > 0 && (
            <p>
              Needs {entry.secret_fields.length} {entry.secret_fields.length === 1 ? "secret" : "secrets"}
            </p>
          )}
          {entry.configured_in.length > 0 && <p>Configured in {entry.configured_in.join(", ")}</p>}
          {entry.enablement.map((item) => (
            <p key={item.connector} data-testid={`enablement-${item.connector}`}>
              Installed as <strong data-donnee>{item.connector}</strong> — {where(item)}
            </p>
          ))}
        </>
      }
      footer={
        <>
          <div className="flex flex-wrap gap-2">
            {canDeclare && (
              <Link
                href={`/admin/connectors?type=${entry.kind}/${entry.type}`}
                className="inline-flex items-center rounded border border-line px-2 py-1 text-sm underline pointer-coarse:min-h-11"
              >
                Declare
              </Link>
            )}
            {canConfigure &&
              entry.can.configure_projects.map((slug) => (
                <Link
                  key={slug}
                  href={`/p/${slug}/settings`}
                  className="inline-flex items-center rounded border border-line px-2 py-1 text-sm underline pointer-coarse:min-h-11"
                >
                  Configure in <span data-donnee>&nbsp;{slug}</span>
                </Link>
              ))}
            {canEnable &&
              selected.map((item) => (
                <Button key={item.connector} size="sm" onClick={() => onProjects(item)}>
                  Projects…
                </Button>
              ))}
          </div>
          {!canDeclare && !canConfigure && !canEnable && entry.cannot_reason && (
            <p className="text-xs text-ink-muted">{entry.cannot_reason}</p>
          )}
        </>
      }
    />
  );
}
