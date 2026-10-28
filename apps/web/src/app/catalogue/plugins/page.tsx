// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, BookOpen, Bot, ScrollText, ServerCog, Workflow, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";
import { Confirmation } from "@/components/geste-confirme";
import { Alert, Badge, Button, Card, Code, Empty, ErrorNote, EtatDeLecture, Select } from "@/components/ui";
import { api } from "@/lib/api";
import {
  admissibleVersions,
  configureHref,
  CONTENT_KINDS,
  installedOf,
  isOrgAdmin,
  isPending,
  NOT_ADMIN,
  serverConnector,
  serverState,
  shortDigest,
  trustLabel,
} from "@/lib/plugins";
import { useSession } from "@/lib/session";
import type {
  InstalledPlugin,
  OrgConnector,
  PluginApproval,
  PluginContents,
  PluginInstallPending,
  PluginSource,
  PluginSourceEntry,
  PluginUninstallResult,
} from "@/lib/types";

/** The icon of each kind of content: the navigation's own (Agents, Skills, Workflows, MCP servers…). */
const KIND_ICONS: Record<keyof PluginContents, LucideIcon> = {
  agents: Bot,
  skills: BookOpen,
  templates: Workflow,
  evals: BadgeCheck,
  procedures: ScrollText,
  servers: ServerCog,
};

/** What the last gesture answered, said once at the top of the page. */
type Outcome =
  | { kind: "installed"; name: string; version: string }
  | { kind: "pending"; name: string; version?: string; pending: PluginInstallPending }
  | { kind: "upgraded"; name: string; from: string; to: string; offered: string[] }
  | { kind: "uninstalled"; result: PluginUninstallResult }
  | { kind: "approved"; approval: PluginApproval };

const why = (cause: unknown) => (cause instanceof Error ? cause.message : "refused");

/**
 * Catalogue → Plugins (S43-08, ADR 0093): the sources the deployment declares and what their indexes
 * offer, the plugins installed in the organisation with what each brought, and the installs that wait
 * for a second administrator (ADR 0050). Every member reads; installing, upgrading, uninstalling and
 * approving are an organisation administrator's (ADR 0074), and the API refuses the administrator who
 * asked their own approval.
 */
export default function PluginsPage() {
  const { org, me } = useSession();
  const client = useQueryClient();
  const admin = isOrgAdmin(me, org);
  const installed = useQuery({ queryKey: ["plugins", org], queryFn: () => api.plugins(org) });
  const sources = useQuery({ queryKey: ["plugin-sources", org], queryFn: () => api.pluginSources(org) });
  const approvals = useQuery({ queryKey: ["plugin-approvals", org], queryFn: () => api.pluginApprovals(org) });
  const connectors = useQuery({ queryKey: ["org-connectors", org], queryFn: () => api.orgConnectors(org) });
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  async function refresh() {
    await Promise.all(
      ["plugins", "plugin-sources", "plugin-approvals", "org-connectors"].map((key) =>
        client.invalidateQueries({ queryKey: [key, org] }),
      ),
    );
  }
  async function done(next: Outcome) {
    setOutcome(next);
    await refresh();
  }

  const items = installed.data?.items ?? [];
  const waiting = (approvals.data?.items ?? []).filter((a) => a.status === "pending");
  const decided = (approvals.data?.items ?? []).filter((a) => a.status !== "pending");
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Plugins</h1>
      <p className="max-w-3xl text-sm text-ink-muted">
        A plugin packages agents and what they need: skills, templates, evals, setup procedures and MCP servers the
        platform runs. It comes from a source the deployment declares and trusts by its signature. A plugin that brings
        a server or a high-risk skill waits for a second administrator before anything is installed.
      </p>
      {!admin && (
        <p id="plugins-not-admin" className="max-w-3xl text-sm text-ink-muted" data-testid="plugins-rights">
          {NOT_ADMIN}
        </p>
      )}
      {outcome && <OutcomeNote outcome={outcome} />}

      <section className="space-y-3" aria-labelledby="plugins-installed">
        <h2 id="plugins-installed" className="text-lg font-semibold">
          Installed ({items.length})
        </h2>
        <EtatDeLecture lecture={installed} quoi="the installed plugins" />
        {installed.data &&
          (items.length === 0 ? (
            <Empty title="No plugin installed">Install one from a source below.</Empty>
          ) : (
            items.map((plugin) => (
              <InstalledCard
                key={plugin.name}
                org={org}
                plugin={plugin}
                admin={admin}
                connectors={connectors.data}
                onDone={done}
              />
            ))
          ))}
      </section>

      <section className="space-y-3" aria-labelledby="plugins-approvals">
        <h2 id="plugins-approvals" className="text-lg font-semibold">
          Waiting for a second administrator ({waiting.length})
        </h2>
        <p className="max-w-3xl text-sm text-ink-muted">
          Each request was checked in full at the version and content digest shown. Another administrator than the one
          who asked approves it; the plugin is then fetched again and installed only if its digest is unchanged.
        </p>
        <EtatDeLecture lecture={approvals} quoi="the plugin approvals" />
        {approvals.data &&
          (waiting.length === 0 ? (
            <p className="text-sm text-ink-muted">Nothing waits.</p>
          ) : (
            <ul className="space-y-2">
              {waiting.map((approval) => (
                <ApprovalRow key={approval.id} org={org} approval={approval} admin={admin} me={me?.email} onDone={done} />
              ))}
            </ul>
          ))}
        {decided.length > 0 && (
          <details className="text-sm">
            <summary className="cursor-pointer text-ink-muted">Approved earlier ({decided.length})</summary>
            <ul className="mt-2 space-y-1" data-testid="approvals-decided">
              {decided.map((a) => (
                <li key={a.id}>
                  <span data-donnee>{a.name}</span> {a.version} — {a.action}, approved by {a.decided_by ?? "?"}
                </li>
              ))}
            </ul>
          </details>
        )}
      </section>

      <section className="space-y-3" aria-labelledby="plugins-sources">
        <h2 id="plugins-sources" className="text-lg font-semibold">
          Sources
        </h2>
        <p className="max-w-3xl text-sm text-ink-muted">
          Declared by the operator of this deployment, not here. Each lists what its index offers; a version outside
          this core&apos;s range is shown but cannot be installed.
        </p>
        <EtatDeLecture lecture={sources} quoi="the plugin sources" />
        {sources.data &&
          (sources.data.sources.length === 0 ? (
            <Empty title="No source declared">The operator declares sources in the chart (`global.plugins.sources`).</Empty>
          ) : (
            sources.data.sources.map((source) => (
              <SourceCard
                key={source.name}
                org={org}
                source={source}
                admin={admin}
                installed={items}
                onDone={done}
              />
            ))
          ))}
      </section>
    </div>
  );
}

function OutcomeNote({ outcome }: { outcome: Outcome }) {
  if (outcome.kind === "pending") {
    return (
      <Alert tone="warn" title="Waiting for a second administrator">
        <span data-testid="outcome-pending">
          Nothing is installed yet: {outcome.name}
          {outcome.version ? ` ${outcome.version}` : ""} needs another administrator&apos;s approval. Reason:{" "}
          <span data-donnee>{outcome.pending.reason}</span>
        </span>
      </Alert>
    );
  }
  if (outcome.kind === "installed") {
    return (
      <Alert tone="ok" title="Installed">
        {outcome.name} {outcome.version} is installed.
      </Alert>
    );
  }
  if (outcome.kind === "approved") {
    return (
      <Alert tone="ok" title="Approved and installed">
        {outcome.approval.name} {outcome.approval.version} is installed.
      </Alert>
    );
  }
  if (outcome.kind === "upgraded") {
    return (
      <Alert tone="ok" title="Upgraded">
        {outcome.name} moved from {outcome.from} to {outcome.to}.
        {outcome.offered.length > 0 && (
          <> Changed by the organisation, so offered the update and not overwritten: {outcome.offered.join(", ")}.</>
        )}
      </Alert>
    );
  }
  const { result } = outcome;
  const list = (names: string[]) => (names.length ? names.join(", ") : "none");
  return (
    <Alert tone="ok" title={`Uninstalled ${result.name} ${result.version}`}>
      <span data-testid="outcome-uninstalled">
        Removed — agents: {list(result.removed.agents)}; skills: {list(result.removed.skills)}.{" "}
        {result.kept.agents.length + result.kept.skills.length > 0 ? (
          <>
            Kept as the organisation&apos;s own (changed here, pinned by a project, or carried by a kept agent) — agents:{" "}
            {list(result.kept.agents)}; skills: {list(result.kept.skills)}.
          </>
        ) : (
          "Nothing was kept."
        )}
      </span>
    </Alert>
  );
}

/** A gesture button an administrator may press; for anyone else, disabled and explained (ADR 0074). */
function Gesture({
  admin,
  onClick,
  tone,
  busy,
  children,
}: {
  admin: boolean;
  onClick: () => void;
  tone?: "default" | "primary" | "accent" | "danger";
  busy?: boolean;
  children: ReactNode;
}) {
  return (
    <Button
      size="sm"
      tone={tone}
      disabled={!admin || busy}
      aria-describedby={admin ? undefined : "plugins-not-admin"}
      onClick={onClick}
    >
      {children}
    </Button>
  );
}

function InstalledCard({
  org,
  plugin,
  admin,
  connectors,
  onDone,
}: {
  org: string;
  plugin: InstalledPlugin;
  admin: boolean;
  connectors: OrgConnector[] | undefined;
  onDone: (outcome: Outcome) => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function upgrade(version: string) {
    setError(null);
    setBusy(true);
    try {
      const answer = await api.upgradePlugin(org, plugin.name, { version });
      if (isPending(answer)) await onDone({ kind: "pending", name: plugin.name, version, pending: answer });
      else
        await onDone({
          kind: "upgraded",
          name: plugin.name,
          from: answer.previous_version,
          to: answer.plugin.version,
          offered: answer.offered,
        });
    } catch (cause) {
      setError(why(cause));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card
      title={
        <h3 className="text-sm font-semibold">
          <span data-donnee>{plugin.name}</span> {plugin.version}
        </h3>
      }
      action={
        <span className="flex flex-wrap items-center gap-2">
          {plugin.default && <Badge tone="accent">Default</Badge>}
          {plugin.latest && <Badge tone="warn">{plugin.latest} available</Badge>}
        </span>
      }
      data-testid={`installed-${plugin.name}`}
    >
      <div className="space-y-3 text-sm">
        <p className="text-ink-muted" data-donnee>
          {plugin.description}
        </p>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
          <dt className="text-ink-muted">Source</dt>
          <dd>{plugin.source}</dd>
          <dt className="text-ink-muted">Digest</dt>
          <dd>
            <Code title={plugin.digest}>{shortDigest(plugin.digest)}</Code>
          </dd>
          <dt className="text-ink-muted">Publisher</dt>
          <dd>
            {plugin.publisher} · {plugin.license}
          </dd>
          <dt className="text-ink-muted">Installed</dt>
          <dd>
            {plugin.installed_at.slice(0, 10)} by {plugin.installed_by ?? "the platform (a default plugin)"}
          </dd>
        </dl>
        <Contents contents={plugin.contents} />
        {plugin.contents.servers.length > 0 && (
          <ul className="space-y-1" aria-label={`Servers of ${plugin.name}`}>
            {plugin.contents.servers.map((server) => (
              <ServerLine key={server} server={server} admin={admin} connector={serverConnector(connectors, server)} />
            ))}
          </ul>
        )}
        <div className="flex flex-wrap items-center gap-2">
          {plugin.latest && (
            <Gesture admin={admin} tone="accent" busy={busy} onClick={() => void upgrade(plugin.latest!)}>
              {busy ? "Upgrading…" : `Upgrade to ${plugin.latest}`}
            </Gesture>
          )}
          <Gesture admin={admin} tone="danger" onClick={() => setConfirming(true)}>
            Uninstall
          </Gesture>
        </div>
        {confirming && (
          <Confirmation
            question={
              <>
                Uninstall {plugin.name} {plugin.version}? What it brought and nobody changed is removed and its servers
                stop; what the organisation changed or a project pins stays as the organisation&apos;s own.
              </>
            }
            confirmer="Uninstall"
            action={async () => {
              const result = await api.uninstallPlugin(org, plugin.name);
              await onDone({ kind: "uninstalled", result });
            }}
            onAnnuler={() => setConfirming(false)}
          />
        )}
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Card>
  );
}

function Contents({ contents }: { contents: PluginContents }) {
  const kinds = CONTENT_KINDS.filter(({ key }) => contents[key].length > 0);
  if (kinds.length === 0) return <p className="text-xs text-ink-muted">It brought nothing.</p>;
  return (
    <ul className="space-y-1" aria-label="What it brought">
      {kinds.map(({ key, label }) => {
        const Icon = KIND_ICONS[key];
        return (
          <li key={key} className="flex items-start gap-2" data-testid={`contents-${key}`}>
            <Icon aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-ink-muted" />
            <span>
              <span className="text-ink-muted">{label}:</span> <span data-donnee>{contents[key].join(", ")}</span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function ServerLine({ server, admin, connector }: { server: string; admin: boolean; connector: OrgConnector | undefined }) {
  const state = serverState(connector);
  return (
    <li className="flex flex-wrap items-center gap-2" data-testid={`server-${server}`}>
      <ServerCog aria-hidden className="h-4 w-4 text-ink-muted" />
      <span>
        Server <strong data-donnee>{server}</strong>, connector <Code>{server}</Code>
      </span>
      <Badge tone={state.tone}>{state.label}</Badge>
      {state.label === "unconfigured" && <span className="text-xs text-ink-muted">no replica until its settings are complete</span>}
      {admin ? (
        <Link href={configureHref(server)} className="text-xs underline">
          Configure
        </Link>
      ) : (
        <Link href={configureHref(server)} className="text-xs underline" aria-describedby="plugins-not-admin">
          View its connector
        </Link>
      )}
    </li>
  );
}

function ApprovalRow({
  org,
  approval,
  admin,
  me,
  onDone,
}: {
  org: string;
  approval: PluginApproval;
  admin: boolean;
  me: string | undefined;
  onDone: (outcome: Outcome) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mine = !!me && approval.requested_by === me;
  async function approve() {
    setError(null);
    setBusy(true);
    try {
      await onDone({ kind: "approved", approval: await api.approvePlugin(org, approval.id) });
    } catch (cause) {
      setError(why(cause));
    } finally {
      setBusy(false);
    }
  }
  return (
    <li className="space-y-2 rounded border border-line p-3 text-sm" data-testid={`approval-${approval.id}`}>
      <p>
        <strong data-donnee>{approval.name}</strong> {approval.version} — {approval.action} from {approval.source}{" "}
        <Code title={approval.digest}>{shortDigest(approval.digest)}</Code>
        {approval.default && (
          <>
            {" "}
            <Badge tone="accent">Default</Badge>
          </>
        )}
      </p>
      <p className="text-ink-muted">
        Asked by {approval.requested_by ?? "the platform (a default plugin)"} on {approval.created_at.slice(0, 10)}.
        Reason: <span data-donnee>{approval.reason}</span>
      </p>
      {mine && <p className="text-xs text-ink-muted">You asked for it: another administrator approves it.</p>}
      <Gesture admin={admin} tone="primary" busy={busy} onClick={() => void approve()}>
        {busy ? "Approving…" : "Approve and install"}
      </Gesture>
      {error && <ErrorNote>{error}</ErrorNote>}
    </li>
  );
}

function SourceCard({
  org,
  source,
  admin,
  installed,
  onDone,
}: {
  org: string;
  source: PluginSource;
  admin: boolean;
  installed: InstalledPlugin[];
  onDone: (outcome: Outcome) => Promise<void>;
}) {
  return (
    <Card title={<h3 className="text-sm font-semibold" data-donnee>{source.name}</h3>} data-testid={`source-${source.name}`}>
      <div className="space-y-3 text-sm">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
          <dt className="text-ink-muted">Index</dt>
          <dd className="break-all">
            <Code>{source.index}</Code>
          </dd>
          <dt className="text-ink-muted">Trust</dt>
          <dd data-testid={`trust-${source.name}`}>
            <Badge tone="neutral">{source.trust.mode}</Badge> {trustLabel(source.trust)}
          </dd>
        </dl>
        {source.error ? (
          <Alert tone="danger" title="The index could not be read">
            {source.error}
          </Alert>
        ) : source.plugins.length === 0 ? (
          <p className="text-ink-muted">Its index offers no plugin.</p>
        ) : (
          <ul className="space-y-2">
            {source.plugins.map((entry) => (
              <SourceEntry
                key={entry.name}
                org={org}
                source={source.name}
                entry={entry}
                admin={admin}
                installed={installedOf(installed, entry.name)}
                onDone={onDone}
              />
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

function SourceEntry({
  org,
  source,
  entry,
  admin,
  installed,
  onDone,
}: {
  org: string;
  source: string;
  entry: PluginSourceEntry;
  admin: boolean;
  installed: InstalledPlugin | undefined;
  onDone: (outcome: Outcome) => Promise<void>;
}) {
  const choices = admissibleVersions(entry);
  const [version, setVersion] = useState(choices[0] ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = `entry-${source}-${entry.name}`;
  const reason = installed
    ? `Installed (${installed.version}${installed.source !== source ? ` from ${installed.source}` : ""}): upgrade or uninstall it above.`
    : choices.length === 0
      ? "No version admits this core."
      : null;
  async function install() {
    setError(null);
    setBusy(true);
    try {
      const answer = await api.installPlugin(org, { source, name: entry.name, version });
      if (isPending(answer)) await onDone({ kind: "pending", name: entry.name, version, pending: answer });
      else await onDone({ kind: "installed", name: answer.name, version: answer.version });
    } catch (cause) {
      setError(why(cause));
    } finally {
      setBusy(false);
    }
  }
  return (
    <li className="space-y-2 rounded border border-line p-3" data-testid={id}>
      <p>
        <strong data-donnee>{entry.name}</strong>
        {installed && (
          <>
            {" "}
            <Badge tone="ok">Installed {installed.version}</Badge>
          </>
        )}
      </p>
      {entry.description && (
        <p className="text-ink-muted" data-donnee>
          {entry.description}
        </p>
      )}
      <p className="text-xs text-ink-muted">
        Versions:{" "}
        {entry.versions.map((v, index) => (
          <span key={v.version}>
            {index > 0 && ", "}
            <span className={v.admissible ? undefined : "line-through"} title={`core ${v.core}`}>
              {v.version}
            </span>
            {!v.admissible && ` (needs core ${v.core})`}
          </span>
        ))}
      </p>
      {!installed && choices.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2 text-xs">
            <span className="text-ink-muted">Version</span>
            <Select value={version} onChange={(event) => setVersion(event.target.value)} disabled={!admin}>
              {choices.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </Select>
          </label>
          <Gesture admin={admin} tone="accent" busy={busy || !version} onClick={() => void install()}>
            {busy ? "Installing…" : "Install"}
          </Gesture>
        </div>
      )}
      {reason && <p className="text-xs text-ink-muted">{reason}</p>}
      {error && <ErrorNote>{error}</ErrorNote>}
    </li>
  );
}
