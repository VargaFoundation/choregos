// SPDX-License-Identifier: Apache-2.0
/** Plugins as the console reads them (ADR 0093, S43-08): the pure parts of Catalogue → Plugins. */
import type {
  InstalledPlugin,
  MeDto,
  OrgConnector,
  PluginContents,
  PluginInstallPending,
  PluginSourceEntry,
  PluginTrust,
} from "./types";

/** A 202 of install or upgrade: nothing was installed, a second administrator must approve. */
export function isPending(answer: object): answer is PluginInstallPending {
  return (answer as { status?: unknown }).status === "pending_approval";
}

/** `sha256:` and the first 12 hex digits: enough to compare two digests by eye. */
export function shortDigest(digest: string): string {
  const hex = digest.startsWith("sha256:") ? digest.slice("sha256:".length) : digest;
  return `sha256:${hex.slice(0, 12)}`;
}

/** How a source's signatures are checked, said in one line. */
export function trustLabel(trust: PluginTrust): string {
  if (trust.mode === "keyless") return `Signed by ${trust.identity ?? "?"} (issuer ${trust.issuer ?? "?"})`;
  if (trust.mode === "key") return `Signed with the key ${trust.key_fingerprint ?? "?"}`;
  return "Offline directory: trusted because it ships in the image";
}

/** The kinds of what a plugin brings, in the order the manifest lists them. */
export const CONTENT_KINDS: { key: keyof PluginContents; label: string }[] = [
  { key: "agents", label: "Agents" },
  { key: "skills", label: "Skills" },
  { key: "templates", label: "Templates" },
  { key: "evals", label: "Evals" },
  { key: "procedures", label: "Procedures" },
  { key: "servers", label: "Servers" },
];

/** The versions this core admits, newest first (the index's order). */
export function admissibleVersions(entry: PluginSourceEntry): string[] {
  return entry.versions.filter((v) => v.admissible).map((v) => v.version);
}

/** Install, upgrade, uninstall and approve are an organisation administrator's (ADR 0074); members read. */
export function isOrgAdmin(me: MeDto | null, org: string): boolean {
  return (me?.memberships ?? []).some((m) => m.org === org && !m.project_slug && m.role === "org_admin");
}

export const NOT_ADMIN = "Only an organisation administrator installs, upgrades, uninstalls or approves plugins. You can read them.";

/** The connector a plugin's server runs as: the organisation `mcp` connector named after the server. */
export function serverConnector(connectors: OrgConnector[] | undefined, server: string): OrgConnector | undefined {
  return (connectors ?? []).find((c) => c.name === server && c.kind === "mcp");
}

/** A server connector's state in words: `deployed`, or `unconfigured` until its schema is satisfied. */
export function serverState(connector: OrgConnector | undefined): { label: string; tone: "ok" | "warn" | "danger" | "neutral" } {
  if (!connector) return { label: "not running", tone: "neutral" };
  if (connector.status === "deployed") return { label: "deployed", tone: "ok" };
  if (connector.status === "unconfigured") return { label: "unconfigured", tone: "warn" };
  if (connector.status === "error") return { label: "error", tone: "danger" };
  return { label: connector.status, tone: "neutral" };
}

/** Where a server is configured: its connector, in the administration's connector page, opened on it. */
export function configureHref(server: string): string {
  return `/admin/connectors?connector=${encodeURIComponent(server)}`;
}

/** The installed plugin of that name, if any. */
export function installedOf(installed: InstalledPlugin[] | undefined, name: string): InstalledPlugin | undefined {
  return (installed ?? []).find((p) => p.name === name);
}
