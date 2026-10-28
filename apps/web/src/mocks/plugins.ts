// SPDX-License-Identifier: Apache-2.0
/**
 * The demo's plugins (S43-08, ADR 0093), answering as the API does: three sources (one keyless, the
 * offline directory, one whose index cannot be read), two installed plugins (the default `servicenow`
 * with its server, and `triage` with an upgrade waiting), and one request from another administrator.
 * A plugin that brings a server answers 202; the administrator who asked cannot approve it (403).
 *
 * Who is looking: an administrator unless `sessionStorage["choregos.mock.role"]` is `developer`.
 */
import { ApiError } from "@/lib/api";
import type {
  InstalledPlugin,
  OrgConnector,
  PluginApproval,
  PluginInstall,
  PluginSource,
  PluginUninstallResult,
  PluginUpgrade,
} from "@/lib/types";

const iso = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
const digest = (seed: string) => `sha256:${seed.repeat(64).slice(0, 64)}`;

/** What each plugin version brings, and whether it needs a second approval (a server). */
const CATALOGUE: Record<string, { description: string; publisher: string; servers: string[]; contents: InstalledPlugin["contents"] }> = {
  servicenow: {
    description: "ServiceNow as a tracker and a governed system: incidents, changes, the CMDB.",
    publisher: "Varga Foundation",
    servers: ["servicenow"],
    contents: {
      agents: ["incident-triager", "change-author"],
      skills: ["servicenow-encoded-queries"],
      templates: ["plugin:servicenow/incident-flow"],
      evals: ["incident-triager/triage.yaml"],
      procedures: ["connector.mcp.servicenow"],
      servers: ["servicenow"],
    },
  },
  triage: {
    description: "Sorts incoming work items by size and risk, and says why.",
    publisher: "Varga Foundation",
    servers: [],
    contents: {
      agents: ["triage-bot", "triage-lead"],
      skills: ["sizing"],
      templates: [],
      evals: ["triage-bot/sizing.yaml"],
      procedures: [],
      servers: [],
    },
  },
  hello: {
    description: "A plugin that ships one of everything a plugin may ship.",
    publisher: "Varga Foundation",
    servers: ["hello"],
    contents: {
      agents: ["greeter"],
      skills: ["greeting"],
      templates: ["plugin:hello/hello-flow"],
      evals: ["greeter/greeter.yaml"],
      procedures: ["connector.mcp.hello"],
      servers: ["hello"],
    },
  },
  "release-notes": {
    description: "Writes release notes from merged changes.",
    publisher: "Acme platform team",
    servers: [],
    contents: { agents: ["release-writer"], skills: [], templates: [], evals: [], procedures: [], servers: [] },
  },
};

export const pluginSources: PluginSource[] = [
  {
    name: "varga",
    index: "oci://ghcr.io/vargafoundation/choregos-agents/index:stable",
    trust: {
      mode: "keyless",
      identity: "https://github.com/VargaFoundation/choregos-agents/.*",
      issuer: "https://token.actions.githubusercontent.com",
      key_fingerprint: null,
    },
    plugins: [
      {
        name: "servicenow",
        description: CATALOGUE.servicenow!.description,
        versions: [
          { version: "0.2.0", core: ">=0.20.0", admissible: true },
          { version: "0.1.0", core: ">=0.20.0", admissible: true },
        ],
      },
      {
        name: "triage",
        description: CATALOGUE.triage!.description,
        versions: [
          { version: "1.2.0", core: ">=0.20.0", admissible: true },
          { version: "1.0.0", core: ">=0.19.0", admissible: true },
        ],
      },
      {
        name: "hello",
        description: CATALOGUE.hello!.description,
        versions: [
          { version: "1.1.0", core: ">=0.22.0", admissible: false },
          { version: "1.0.0", core: ">=0.20.0", admissible: true },
        ],
      },
    ],
    error: null,
  },
  {
    name: "offline",
    index: "/opt/choregos/plugins",
    trust: { mode: "none", identity: null, issuer: null, key_fingerprint: null },
    plugins: [
      {
        name: "release-notes",
        description: CATALOGUE["release-notes"]!.description,
        versions: [{ version: "0.3.0", core: ">=0.20.0", admissible: true }],
      },
    ],
    error: null,
  },
  {
    name: "partner",
    index: "https://plugins.partner.example/index.yaml",
    trust: { mode: "key", identity: null, issuer: null, key_fingerprint: "sha256:9c2e71d0aa41" },
    plugins: [],
    error: "fetch: plugins.partner.example did not answer",
  },
];

function plugin(name: string, version: string, source: string, extra: Partial<InstalledPlugin> = {}): InstalledPlugin {
  const known = CATALOGUE[name]!;
  return {
    name,
    version,
    source,
    digest: digest(`${name.length}${version.replace(/\D/g, "")}ab`),
    publisher: known.publisher,
    license: "Apache-2.0",
    description: known.description,
    default: false,
    contents: structuredClone(known.contents),
    installed_by: "augustin@acme.dev",
    installed_at: iso(60 * 24 * 3),
    latest: null,
    ...extra,
  };
}

export const installedPlugins: InstalledPlugin[] = [
  plugin("servicenow", "0.1.0", "varga", { default: true, installed_by: null, latest: "0.2.0" }),
  plugin("triage", "1.0.0", "varga", { latest: "1.2.0" }),
];

export const pluginApprovals: PluginApproval[] = [
  {
    id: "pa-1",
    action: "install",
    source: "varga",
    name: "hello",
    version: "1.0.0",
    digest: digest("1100ab"),
    reason: "it brings the server `hello`; its skill `greeting` is high risk (script)",
    default: false,
    requested_by: "lea@acme.dev",
    status: "pending",
    decided_by: null,
    created_at: iso(45),
    decided_at: null,
  },
];

/** The server connector a plugin brings, `unconfigured` until its schema is satisfied (ADR 0096). */
export function serverConnectorOf(plugin: string, version: string, server: string): OrgConnector {
  return {
    name: server,
    kind: "mcp",
    type: "mcp",
    config: {},
    secret_refs: { _server_token: `store:connector/${server}/_server_token` },
    secrets: [{ field: "_server_token", source: "store", version: 1, rotated_at: iso(60), rotated_by: "platform" }],
    status: "unconfigured",
    created_by: `plugin:${plugin}`,
    catalogue_entry: `plugin:${plugin}/${server}@${version}`,
    availability: "all_projects",
    enabled_projects: [],
    operations: [
      { name: "search_records", access: "read", policy: "allowed", groups: [] },
      { name: "create_incident", access: "write", policy: "approval", groups: [] },
    ],
    server: {
      plugin,
      version,
      server,
      config_schema: {
        type: "object",
        required: ["instance_url"],
        properties: {
          instance_url: { type: "string", format: "uri", description: "https://<instance>.service-now.com" },
          client_id: { type: "string" },
        },
      },
      secret_fields: ["client_secret"],
    },
  };
}

let counter = pluginApprovals.length;

function role(): "org_admin" | "developer" {
  try {
    return typeof window !== "undefined" && window.sessionStorage.getItem("choregos.mock.role") === "developer" ? "developer" : "org_admin";
  } catch {
    return "org_admin";
  }
}

function mustAdmin(): void {
  if (role() !== "org_admin") throw new ApiError(403, { title: "Forbidden", detail: "installing plugins is an organisation administrator's" });
}

function sourceEntry(source: string, name: string) {
  const entry = pluginSources.find((s) => s.name === source)?.plugins.find((p) => p.name === name);
  if (!entry) throw new ApiError(404, { title: "Not Found", detail: `the source \`${source}\` offers no plugin \`${name}\`` });
  return entry;
}

/** The newest admissible version the request names: exact, or the newest when a range was typed. */
function resolve(source: string, name: string, version: string | undefined): string {
  const entry = sourceEntry(source, name);
  const admissible = entry.versions.filter((v) => v.admissible);
  const exact = admissible.find((v) => v.version === version);
  if (exact) return exact.version;
  if (version && entry.versions.some((v) => v.version === version)) {
    throw new ApiError(422, { title: "Unprocessable", detail: `core: ${name} ${version} requires another core` });
  }
  if (!admissible[0]) throw new ApiError(422, { title: "Unprocessable", detail: `version: no version of ${name} admits this core` });
  return admissible[0].version;
}

function latestOf(source: string, name: string, version: string): string | null {
  const newest = sourceEntry(source, name).versions.find((v) => v.admissible)?.version ?? null;
  return newest && newest !== version ? newest : null;
}

function pending(action: "install" | "upgrade", source: string, name: string, version: string) {
  const asked = pluginApprovals.find((a) => a.status === "pending" && a.name === name && a.version === version && a.action === action);
  const approval: PluginApproval = asked ?? {
    id: `pa-${++counter}`,
    action,
    source,
    name,
    version,
    digest: digest(`${name.length}${version.replace(/\D/g, "")}ab`),
    reason: CATALOGUE[name]!.servers.map((s) => `it brings the server \`${s}\``).join("; "),
    default: false,
    requested_by: "augustin@acme.dev",
    status: "pending",
    decided_by: null,
    created_at: new Date().toISOString(),
    decided_at: null,
  };
  if (!asked) pluginApprovals.unshift(approval);
  return { status: "pending_approval" as const, approval_id: approval.id, reason: approval.reason };
}

function put(installed: InstalledPlugin, connectors: OrgConnector[]): void {
  const at = installedPlugins.findIndex((p) => p.name === installed.name);
  if (at >= 0) installedPlugins[at] = installed;
  else installedPlugins.push(installed);
  for (const server of installed.contents.servers) {
    if (!connectors.some((c) => c.name === server)) connectors.push(serverConnectorOf(installed.name, installed.version, server));
  }
}

/** The plugin routes, or `undefined` for any other path. */
export function pluginsApi(method: string, path: string, body: unknown, connectors: OrgConnector[]): unknown {
  if (method === "GET" && /^\/orgs\/[^/]+\/plugins$/.test(path)) return { items: structuredClone(installedPlugins) };
  if (method === "GET" && /^\/orgs\/[^/]+\/plugin-sources$/.test(path)) return { sources: structuredClone(pluginSources) };
  if (method === "GET" && /^\/orgs\/[^/]+\/plugin-approvals$/.test(path)) return { items: structuredClone(pluginApprovals) };
  if (method === "POST" && /^\/orgs\/[^/]+\/plugins\/install$/.test(path)) {
    mustAdmin();
    const ask = body as PluginInstall;
    if (installedPlugins.some((p) => p.name === ask.name)) throw new ApiError(409, { title: "Conflict", detail: `\`${ask.name}\` is already installed` });
    const version = resolve(ask.source, ask.name, ask.version);
    if (CATALOGUE[ask.name]!.servers.length > 0) return pending("install", ask.source, ask.name, version);
    const made = plugin(ask.name, version, ask.source, { installed_at: new Date().toISOString(), latest: latestOf(ask.source, ask.name, version) });
    put(made, connectors);
    return structuredClone(made);
  }
  const upgrade = /^\/orgs\/[^/]+\/plugins\/([^/]+)\/upgrade$/.exec(path);
  if (method === "POST" && upgrade) {
    mustAdmin();
    const current = installedPlugins.find((p) => p.name === decodeURIComponent(upgrade[1]!));
    if (!current) throw new ApiError(404, { title: "Not Found", detail: "no such plugin installed" });
    const version = resolve(current.source, current.name, (body as PluginUpgrade | undefined)?.version);
    if (CATALOGUE[current.name]!.servers.length > 0) return pending("upgrade", current.source, current.name, version);
    const made = { ...current, version, digest: digest(`${current.name.length}${version.replace(/\D/g, "")}ab`), latest: latestOf(current.source, current.name, version) };
    put(made, connectors);
    return { plugin: structuredClone(made), previous_version: current.version, offered: current.name === "triage" ? ["triage-lead"] : [] };
  }
  const uninstall = /^\/orgs\/[^/]+\/plugins\/([^/]+)$/.exec(path);
  if (method === "DELETE" && uninstall) {
    mustAdmin();
    const at = installedPlugins.findIndex((p) => p.name === decodeURIComponent(uninstall[1]!));
    if (at < 0) throw new ApiError(404, { title: "Not Found", detail: "no such plugin installed" });
    const [gone] = installedPlugins.splice(at, 1);
    for (const server of gone!.contents.servers) {
      const index = connectors.findIndex((c) => c.name === server);
      if (index >= 0) connectors.splice(index, 1);
    }
    for (let i = pluginApprovals.length - 1; i >= 0; i--) {
      if (pluginApprovals[i]!.name === gone!.name && pluginApprovals[i]!.status === "pending") pluginApprovals.splice(i, 1);
    }
    // The organisation changed one of triage's agents: it stays, as the organisation's own.
    const kept = gone!.name === "triage" ? { agents: ["triage-lead"], skills: ["sizing"] } : { agents: [], skills: [] };
    const result: PluginUninstallResult = {
      name: gone!.name,
      version: gone!.version,
      removed: {
        agents: gone!.contents.agents.filter((a) => !kept.agents.includes(a)),
        skills: gone!.contents.skills.filter((s) => !kept.skills.includes(s)),
      },
      kept,
    };
    return result;
  }
  const approve = /^\/orgs\/[^/]+\/plugin-approvals\/([^/]+)\/approve$/.exec(path);
  if (method === "POST" && approve) {
    mustAdmin();
    const approval = pluginApprovals.find((a) => a.id === decodeURIComponent(approve[1]!));
    if (!approval || approval.status !== "pending") throw new ApiError(404, { title: "Not Found", detail: "no such request waiting" });
    if (approval.requested_by === "augustin@acme.dev") {
      throw new ApiError(403, { title: "Forbidden", detail: "the administrator who asked cannot approve: another administrator of the organisation must" });
    }
    const previous = installedPlugins.find((p) => p.name === approval.name);
    put(
      plugin(approval.name, approval.version, approval.source, {
        ...(previous ? { default: previous.default, installed_at: previous.installed_at } : { installed_at: new Date().toISOString() }),
        installed_by: approval.requested_by,
        latest: latestOf(approval.source, approval.name, approval.version),
      }),
      connectors,
    );
    approval.status = "approved";
    approval.decided_by = "augustin@acme.dev";
    approval.decided_at = new Date().toISOString();
    return structuredClone(approval);
  }
  return undefined;
}
