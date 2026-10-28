// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { CatalogueCard, CatalogueGrid } from "@/components/catalogue/catalogue-grid";
import { CataloguePurpose } from "@/components/catalogue/catalogue-purpose";
import { CatalogueFacets } from "@/components/catalogue/facets";
import { EnablementDrawer } from "@/components/catalogue/enablement-drawer";
import { InstallDrawer } from "@/components/catalogue/install-drawer";
import { estUneReference } from "@/components/connecteurs";
import { resumeDeLaDecouverte } from "@/components/decouverte";
import { egressFlag, useEgressGuard } from "@/components/egress/egress-provider";
import { ProcedurePanel } from "@/components/procedures/procedure-panel";
import { Alert, Badge, Button, EtatDeLecture, ErrorNote, Input } from "@/components/ui";
import { api } from "@/lib/api";
import { categoriesOf, NO_FILTERS, stars, type CatalogueFilters } from "@/lib/catalogue";
import { authLabel, filterMcpEntries, hostOf, suggestedReference, whyNotInstallable } from "@/lib/mcp-catalogue";
import { useSession } from "@/lib/session";
import type { ConnectorEnablement, McpCatalogueEntry } from "@/lib/types";

const TOGGLES = [
  { key: "official", label: "Official only" },
  { key: "installable", label: "Installable only" },
];

/**
 * The catalogue of well-known MCP servers (ADR 0065). An entry is an offer: installing one declares
 * an ordinary MCP connector — the secret by reference, its tools born closed — and only the
 * organisation's administrator can. A server that needs OAuth or runs as a local process is listed,
 * with the reason it cannot be installed from here.
 */
export default function McpCataloguePage() {
  const { org } = useSession();
  const catalogue = useQuery({ queryKey: ["mcp-catalogue", org], queryFn: () => api.mcpCatalogue(org) });
  const [filters, setFilters] = useState<CatalogueFilters>(NO_FILTERS);
  const [installing, setInstalling] = useState<McpCatalogueEntry | null>(null);
  const [enabling, setEnabling] = useState<{ enablement: ConnectorEnablement; projects: string[] } | null>(null);
  const entries = catalogue.data?.entries ?? [];
  const shown = filterMcpEntries(entries, filters);
  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold">MCP servers</h1>
      <p className="max-w-3xl text-sm text-ink-muted">
        Well-known MCP servers, ready to declare. Installing one creates an MCP connector of the organisation: its
        tools are discovered after the install and each one is born closed, so nothing runs until you open it. The
        catalogue enables nothing by itself.
        {entries.some((entry) => entry.can.install) && (
          <>
            {" "}
            Installed servers are governed in{" "}
            <Link href="/admin/connectors" className="underline">
              Administration, Connectors
            </Link>
            .
          </>
        )}
      </p>
      <CataloguePurpose kind="mcp-servers" />
      {catalogue.data && (
        <p className="text-xs text-ink-muted" data-testid="catalogue-version">
          Catalogue {catalogue.data.meta.catalogue_version}, generated {catalogue.data.meta.generated_at.slice(0, 10)}.
          Stars are a snapshot.
        </p>
      )}
      <EtatDeLecture lecture={catalogue} quoi="the catalogue" />
      {catalogue.data && (
        <>
          <CatalogueFacets
            filters={filters}
            onChange={setFilters}
            categories={categoriesOf(entries)}
            toggles={TOGGLES}
            shown={shown.length}
            total={entries.length}
            noun="servers"
          />
          <CatalogueGrid
            items={shown}
            keyOf={(entry) => entry.id}
            empty="Loosen a filter, or search for another word."
            render={(entry) => (
              <Entry
                entry={entry}
                onInstall={() => setInstalling(entry)}
                onProjects={(enablement) => setEnabling({ enablement, projects: entry.can.enable_projects })}
              />
            )}
          />
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
      {installing && (
        <InstallDrawer title={installing.title} onClose={() => setInstalling(null)}>
          <InstallForm org={org} entry={installing} onDone={() => setInstalling(null)} />
        </InstallDrawer>
      )}
    </div>
  );
}

function Entry({
  entry,
  onInstall,
  onProjects,
}: {
  entry: McpCatalogueEntry;
  onInstall: () => void;
  onProjects: (enablement: ConnectorEnablement) => void;
}) {
  // The server decides what the caller may do and says why not (ADR 0074); the entry's own limits
  // (OAuth, local process) come second.
  const why = entry.can.install ? whyNotInstallable(entry) : (entry.cannot_reason ?? whyNotInstallable(entry));
  const host = hostOf(entry);
  return (
    <CatalogueCard
      id={entry.id}
      title={entry.title}
      publisher={entry.publisher}
      description={entry.description}
      badges={
        <>
          {entry.official && <Badge tone="ok">Official</Badge>}
          {entry.origin === "site" && <Badge tone="accent">Your organisation</Badge>}
          {entry.reference && <Badge tone="neutral">Reference server</Badge>}
          <Badge tone="neutral">{entry.transport === "remote" ? "Hosted" : "Local process"}</Badge>
          <Badge tone="neutral">{authLabel(entry.auth)}</Badge>
          {entry.state === "installed" && <Badge tone="ok">Installed</Badge>}
          {entry.state === "update" && <Badge tone="warn">Update available</Badge>}
        </>
      }
      facts={
        <>
          {entry.stars != null && (
            <p>
              <span aria-hidden>★</span> {stars(entry.stars)} stars as of {entry.stars_as_of}
            </p>
          )}
          {entry.licence && <p>Licence {entry.licence}</p>}
          {entry.reference && <p>Usually unnecessary in a Choregos run: the run already has a shell, git and files.</p>}
          {host && entry.egress === "open" && <p>Host declared reachable: {host}</p>}
          {host && entry.egress === "undeclared" && (
            <p className="text-waiting-ink">Host not declared reachable from the API: {host}</p>
          )}
          {(entry.enablement ?? []).map((item) => (
            <p key={item.connector} data-testid={`enablement-${item.connector}`}>
              Installed as <strong data-donnee>{item.connector}</strong> — {where(item)}
            </p>
          ))}
        </>
      }
      footer={
        <>
          <Button
            tone="accent"
            size="sm"
            disabled={!entry.can.install || why !== null}
            aria-describedby={why ? `why-${entry.id}` : undefined}
            onClick={onInstall}
          >
            {entry.state === "installed" ? "Install again" : "Install"}
          </Button>
          {entry.can.enable_projects.length > 0 &&
            (entry.enablement ?? [])
              .filter((item) => item.availability === "selected_projects")
              .map((item) => (
                <Button key={item.connector} size="sm" onClick={() => onProjects(item)}>
                  Projects…
                </Button>
              ))}
          {why && (
            <p id={`why-${entry.id}`} className="text-xs text-ink-muted">
              {why}
            </p>
          )}
        </>
      }
    />
  );
}

/** Where an installed server is on, as the caller sees it. */
function where(item: ConnectorEnablement): string {
  if (item.availability === "all_projects") return "on for every project";
  if (item.enabled_projects.length === 0) return "on in no project yet";
  return `on in ${item.enabled_projects.join(", ")}`;
}

/** The name, the secret by reference, then the install and the first discovery, then the connector's page. */
function InstallForm({ org, entry, onDone }: { org: string; entry: McpCatalogueEntry; onDone: () => void }) {
  const router = useRouter();
  const client = useQueryClient();
  const needsSecret = entry.auth !== "none";
  const [name, setName] = useState(entry.id);
  const [reference, setReference] = useState("");
  // Available is not enabled (ADR 0065, 0074): the console proposes selected projects.
  const [availability, setAvailability] = useState<"selected_projects" | "all_projects">("selected_projects");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const guard = useEgressGuard();
  const host = hostOf(entry);
  const unreadable = reference !== "" && !estUneReference(reference);
  async function install() {
    setError(null);
    setBusy(true);
    let created = name;
    try {
      const connector = await guard(
        (allow) =>
          api.installMcpCatalogueEntry(org, entry.id, {
            name,
            secret_refs: needsSecret && reference ? { token: reference } : {},
            availability,
            ...egressFlag(allow),
          }),
        {
          gesture: `Install the ${entry.id} MCP server`,
          origin: `mcp-catalogue:${entry.id}`,
          reason: `the ${entry.id} MCP server`,
        },
      );
      created = connector.name;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
      setBusy(false);
      return;
    }
    await client.invalidateQueries({ queryKey: ["org-connectors", org] });
    await client.invalidateQueries({ queryKey: ["mcp-catalogue", org] });
    // The tools are asked for right away; a server that does not answer yet leaves the connector in
    // place, its page says why and offers the discovery again.
    let summary = "";
    try {
      summary = resumeDeLaDecouverte(await api.discoverConnectorOperations(org, created));
    } catch (cause) {
      summary = cause instanceof Error ? `Discovery failed: ${cause.message}` : "Discovery failed";
    }
    onDone();
    router.push(`/admin/connectors?name=${encodeURIComponent(created)}&discovery=${encodeURIComponent(summary)}`);
  }
  return (
    <form
      className="space-y-4 text-sm"
      onSubmit={(event) => {
        event.preventDefault();
        void install();
      }}
    >
      {entry.procedure && (
        <div className="flex flex-wrap items-center gap-2" data-testid="install-procedure">
          <ProcedurePanel target="connector:mcp/mcp" procedure={entry.procedure} autoOpen={false} inline />
        </div>
      )}
      <p className="text-ink-muted" data-donnee>
        {entry.description}
      </p>
      {entry.remote && (
        <p className="text-xs text-ink-muted">
          Server: <code>{entry.remote.url}</code>
        </p>
      )}
      {host && entry.egress === "undeclared" && (
        <Alert tone="warn" title="Host not declared">
          This deployment does not declare <code>{host}</code> reachable from the API. Ask your operator to open it
          first, or the connector will time out.
        </Alert>
      )}
      <label className="block space-y-1">
        <span className="text-xs text-ink-muted">Connector name</span>
        <Input
          className="w-full"
          value={name}
          pattern="^[a-z][a-z0-9-]{1,62}$"
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      {needsSecret ? (
        <div className="space-y-1">
          <label className="block space-y-1">
            <span className="text-xs text-ink-muted">Secret, by reference</span>
            <Input
              className="w-full font-mono"
              placeholder={suggestedReference(entry.id)}
              aria-describedby="secret-help"
              value={reference}
              onChange={(event) => setReference(event.target.value)}
            />
          </label>
          <p id="secret-help" className="text-xs text-ink-muted">
            A reference to a secret the platform holds, never the key itself. Your operator creates the secret.
          </p>
        </div>
      ) : (
        <p className="text-xs text-ink-muted">This server takes no credential.</p>
      )}
      <fieldset className="space-y-1">
        <legend className="text-xs text-ink-muted">Available to</legend>
        <label className="flex items-center gap-2">
          <input
            type="radio"
            name="availability"
            checked={availability === "selected_projects"}
            onChange={() => setAvailability("selected_projects")}
          />
          Selected projects (enable it per project)
        </label>
        <label className="flex items-center gap-2">
          <input
            type="radio"
            name="availability"
            checked={availability === "all_projects"}
            onChange={() => setAvailability("all_projects")}
          />
          Every project
        </label>
      </fieldset>
      {unreadable && (
        <p className="text-xs text-danger" role="alert">
          A reference is expected (env:NAME), not a value
        </p>
      )}
      {error && <ErrorNote>{error}</ErrorNote>}
      <div className="flex gap-2">
        <Button type="submit" tone="accent" disabled={busy || !name || unreadable || (needsSecret && !reference)}>
          {busy ? "Installing…" : "Install"}
        </Button>
        <Button onClick={onDone}>Cancel</Button>
      </div>
    </form>
  );
}
