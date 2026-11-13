// SPDX-License-Identifier: Apache-2.0
/**
 * The demo's outposts (ADR 0147), answering as the management API does: one outpost in service with
 * an approved instance and one that waits for approval, one paused, one created and never enrolled,
 * their recent calls as digests, and the API's own refusals (a slug taken, a fingerprint that differs,
 * an outpost that is not revoked or still in use). The connectors at an outpost (ADR 0149) are the
 * organisation's of type `mcp-outpost`: declaring, editing and discovering one meets the refusals of
 * its route, and an agent's access says where its tools live.
 *
 * Nothing dials in here: there is no daemon in a demo. So that the enrolment can be followed to its
 * end, an instance "enrols" by itself a moment after a token is issued: it is there, pending, the next
 * time the instances are read.
 */
import { ApiError } from "@/lib/api";
import type {
  AgentAccessOutpost,
  ConnectorOperation,
  OperationPatch,
  OrgConnector,
  Outpost,
  OutpostCallPage,
  OutpostCallRecord,
  OutpostEnrolmentCreated,
  OutpostInstance,
  OutpostInstanceList,
  OutpostList,
} from "@/lib/types";

const iso = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
const SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/;
const FINGERPRINT = /^SHA256:[A-Za-z0-9_-]{43}$/;
const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
const HEX = "0123456789abcdef";

/** Characters that look drawn at random and are the same at every load: nothing here is a real key. */
function drawn(seed: string, length: number, alphabet = ALPHABET): string {
  let state = [...seed].reduce((sum, character) => (sum * 31 + character.charCodeAt(0)) >>> 0, 7);
  let out = "";
  for (let i = 0; i < length; i += 1) {
    state = (state * 1_103_515_245 + 12_345) >>> 0;
    out += alphabet[(state >>> 16) % alphabet.length];
  }
  return out;
}

/** A public key as the contract writes one: 43 characters, the last of which leaves no stray bit. */
const publicKey = (seed: string) => `${drawn(seed, 42)}A`;
const fingerprint = (seed: string) => `SHA256:${drawn(`fingerprint ${seed}`, 43)}`;
const digest = (seed: string) => drawn(`digest ${seed}`, 64, HEX);

/** What the demo deployment's door was given: the key that signs, and one announced for a rotation. */
export const MOCK_DOOR_ORIGIN = "https://outposts.example.com";
export const MOCK_DOOR_KEYS = [publicKey("door, signing"), publicKey("door, announced")];

type Stored = Pick<Outpost, "slug" | "display_name" | "status" | "capabilities" | "epoch" | "created_by" | "created_at"> & {
  /** Its id, as the instances' ids start with it. */
  id: string;
  /** A token was issued: an instance enrols at the next read of the instances. */
  enrolling: boolean;
};

const outposts: Stored[] = [
  {
    id: drawn("factory-floor", 32, HEX),
    slug: "factory-floor",
    display_name: "Factory floor",
    status: "active",
    capabilities: ["tools"],
    epoch: 1,
    created_by: "augustin@acme.dev",
    created_at: iso(60 * 24 * 12),
    enrolling: false,
  },
  {
    id: drawn("cold-store", 32, HEX),
    slug: "cold-store",
    display_name: "Cold store",
    status: "paused",
    capabilities: ["tools"],
    epoch: 0,
    created_by: "augustin@acme.dev",
    created_at: iso(60 * 24 * 3),
    enrolling: false,
  },
  {
    // Created before its operator installed anything: no instance, so it announces no endpoint yet.
    id: drawn("branch-office", 32, HEX),
    slug: "branch-office",
    display_name: "Branch office",
    status: "active",
    capabilities: ["tools"],
    epoch: 0,
    created_by: "augustin@acme.dev",
    created_at: iso(60 * 6),
    enrolling: false,
  },
];

function instance(outpost: Stored, seed: string, values: Partial<OutpostInstance>): OutpostInstance {
  return {
    id: outpost.id + drawn(`instance ${seed}`, 32, HEX),
    status: "pending",
    fingerprint: fingerprint(seed),
    daemon_version: "0.27.0",
    protocol: 1,
    capabilities: ["tools"],
    endpoints: [],
    facts: {},
    enrolled_at: iso(5),
    approved_by: null,
    approved_at: null,
    revoked_by: null,
    revoked_at: null,
    last_seen_at: null,
    online: false,
    in_flight: 0,
    ...values,
  };
}

const [factory, cold] = outposts as [Stored, Stored];

const instances: Record<string, OutpostInstance[]> = {
  "factory-floor": [
    instance(factory, "factory, waiting", {
      facts: { hostname: "outpost-1.tools.svc", platform: "linux/amd64" },
      enrolled_at: iso(4),
    }),
    instance(factory, "factory, in service", {
      status: "active",
      endpoints: ["tracker", "warehouse"],
      facts: { hostname: "outpost-0.tools.svc", platform: "linux/amd64" },
      enrolled_at: iso(60 * 24 * 12),
      approved_by: "augustin@acme.dev",
      approved_at: iso(60 * 24 * 12 - 3),
      last_seen_at: iso(0),
      online: true,
      in_flight: 1,
    }),
    instance(factory, "factory, retired", {
      status: "revoked",
      daemon_version: "0.26.2",
      facts: { hostname: "outpost-old.tools.svc", platform: "linux/amd64" },
      enrolled_at: iso(60 * 24 * 40),
      approved_by: "augustin@acme.dev",
      approved_at: iso(60 * 24 * 40 - 5),
      revoked_by: "augustin@acme.dev",
      revoked_at: iso(60 * 24 * 12),
      last_seen_at: iso(60 * 24 * 12 + 20),
    }),
  ],
  "cold-store": [
    instance(cold, "cold, in service", {
      status: "active",
      endpoints: ["sensors"],
      facts: { hostname: "cold-0.plant.example", platform: "linux/arm64" },
      enrolled_at: iso(60 * 24 * 3),
      approved_by: "augustin@acme.dev",
      approved_at: iso(60 * 24 * 3 - 2),
      last_seen_at: iso(60 * 5),
    }),
  ],
};

function call(outpost: Stored, seed: string, values: Partial<OutpostCallRecord>): OutpostCallRecord {
  const claimedBy = instances[outpost.slug]?.find((i) => i.status === "active")?.id ?? null;
  return {
    id: `call-${drawn(seed, 12, HEX)}`,
    kind: "call_tool",
    state: "answered",
    connector: null,
    endpoint: "tracker",
    tool: "search_issues",
    write: false,
    instance_id: claimedBy,
    epoch: outpost.epoch,
    outcome: "result",
    is_error: false,
    refusal: null,
    arguments_sha256: digest(`${seed} arguments`),
    result_sha256: digest(`${seed} result`),
    project: "billing-api",
    run_id: null,
    action_id: null,
    created_at: iso(10),
    claimed_at: iso(10),
    finished_at: iso(10),
    ...values,
  };
}

const calls: Record<string, OutpostCallRecord[]> = {
  "factory-floor": [
    call(factory, "in flight", { state: "claimed", tool: "get_issue", outcome: null, is_error: null, result_sha256: null, created_at: iso(0), claimed_at: iso(0), finished_at: null }),
    call(factory, "answered", { created_at: iso(6), claimed_at: iso(6), finished_at: iso(6) }),
    call(factory, "write refused", {
      state: "refused",
      tool: "create_issue",
      write: true,
      outcome: null,
      is_error: null,
      refusal: "write_denied",
      result_sha256: null,
      created_at: iso(32),
      claimed_at: iso(32),
      finished_at: iso(32),
    }),
    call(factory, "tool refused", {
      state: "refused",
      endpoint: "warehouse",
      tool: "drop_table",
      write: true,
      outcome: null,
      is_error: null,
      refusal: "tool_not_allowed",
      result_sha256: null,
      created_at: iso(95),
      claimed_at: iso(95),
      finished_at: iso(95),
    }),
    call(factory, "listing", { kind: "list_tools", tool: null, arguments_sha256: null, project: null, created_at: iso(60 * 3), claimed_at: iso(60 * 3), finished_at: iso(60 * 3) }),
    call(factory, "server error", { endpoint: "warehouse", tool: "stock_level", is_error: true, created_at: iso(60 * 5), claimed_at: iso(60 * 5), finished_at: iso(60 * 5) }),
    call(factory, "in doubt", {
      state: "in_doubt",
      tool: "close_issue",
      write: true,
      outcome: null,
      is_error: null,
      result_sha256: null,
      created_at: iso(60 * 26),
      claimed_at: iso(60 * 26),
      finished_at: iso(60 * 26 - 2),
    }),
    call(factory, "nobody there", {
      state: "expired",
      instance_id: null,
      epoch: null,
      outcome: null,
      is_error: null,
      result_sha256: null,
      created_at: iso(60 * 24 * 12 + 5),
      claimed_at: null,
      finished_at: iso(60 * 24 * 12 + 4),
    }),
  ],
  "cold-store": [],
};

/** The instances a daemon would be polling for: the demo has none, so they are seen "now" at every read. */
const polling = new Set<string>(instances["factory-floor"]?.filter((i) => i.status === "active").map((i) => i.id));

function seenNow(outpost: Stored): OutpostInstance[] {
  const own = (instances[outpost.slug] ??= []);
  for (const each of own) {
    each.online = each.status === "active" && polling.has(each.id);
    if (each.online) each.last_seen_at = iso(0);
  }
  return own;
}

function conflict(code: string, detail: string, extra: Record<string, unknown> = {}): ApiError {
  return new ApiError(409, { title: "Conflict", detail, code, ...extra });
}

function revokedRefusal(outpost: Stored): ApiError {
  return conflict("outpost_revoked", `\`${outpost.slug}\` is revoked: that is final`, { outpost: outpost.slug });
}

function stored(slug: string): Stored {
  const outpost = outposts.find((o) => o.slug === slug);
  if (!outpost) throw new ApiError(404, { title: "Outpost not found", detail: `Outpost \`${slug}\` does not exist` });
  return outpost;
}

function connectorsOf(outpost: Stored, connectors: OrgConnector[]): string[] {
  return connectors
    .filter((c) => c.type === "mcp-outpost" && (c.config as { outpost?: string } | undefined)?.outpost === outpost.slug)
    .map((c) => c.name)
    .sort();
}

function view(outpost: Stored, connectors: OrgConnector[]): Outpost {
  const own = seenNow(outpost);
  const seen = own.map((i) => i.last_seen_at).filter((at): at is string => !!at).sort();
  return {
    slug: outpost.slug,
    display_name: outpost.display_name,
    status: outpost.status,
    capabilities: outpost.capabilities,
    epoch: outpost.epoch,
    online: own.some((i) => i.status === "active" && i.online),
    last_seen_at: seen.at(-1) ?? null,
    instances: {
      pending: own.filter((i) => i.status === "pending").length,
      active: own.filter((i) => i.status === "active").length,
      revoked: own.filter((i) => i.status === "revoked").length,
    },
    endpoints: [...new Set(own.filter((i) => i.status === "active").flatMap((i) => i.endpoints))].sort(),
    connectors: connectorsOf(outpost, connectors),
    door_keys: MOCK_DOOR_KEYS,
    created_by: outpost.created_by,
    created_at: outpost.created_at,
  };
}

const ORDER = { pending: 0, active: 1, revoked: 2 } as const;

function listed(outpost: Stored): OutpostInstance[] {
  const own = (instances[outpost.slug] ??= []);
  if (outpost.enrolling) {
    // The daemon the demo does not have: an instance enrolled with the token that was just issued.
    outpost.enrolling = false;
    own.push(
      instance(outpost, `enrolled ${own.length}`, {
        facts: { hostname: `outpost-${own.length}.tools.svc`, platform: "linux/amd64" },
        enrolled_at: iso(0),
      }),
    );
  }
  return [...seenNow(outpost)].sort((a, b) => ORDER[a.status] - ORDER[b.status] || a.id.localeCompare(b.id));
}

function oneInstance(outpost: Stored, id: string): OutpostInstance {
  const found = (instances[outpost.slug] ?? []).find((i) => i.id === id);
  if (!found) throw new ApiError(404, { title: "Outpost instance not found", detail: `Outpost instance \`${id}\` does not exist` });
  return found;
}

function page(outpost: Stored, query: string): OutpostCallPage {
  const params = new URLSearchParams(query);
  const state = params.get("state");
  const limit = Number(params.get("limit") ?? "50");
  const offset = Number(params.get("cursor") ?? "0");
  const all = (calls[outpost.slug] ?? []).filter((c) => !state || c.state === state);
  const items = all.slice(offset, offset + limit);
  const more = offset + limit < all.length;
  return { items, meta: { next_cursor: more ? String(offset + limit) : null, has_more: more } };
}

// ─── connectors at an outpost (ADR 0149) ───

const ALIAS = /^[a-z][a-z0-9-]{0,62}$/;
/** The tools the demo's servers declare read-only; every other one is a write. */
const READS = new Set(["search_issues", "get_issue", "stock_level"]);

function routeRefusal(code: string, detail: string, outpost: string, endpoint: string, extra: Record<string, unknown> = {}): ApiError {
  return conflict(code, detail, { outpost, endpoint, ...extra });
}

/**
 * A declaration or an edit of a connector at an outpost, judged as the API judges it: an outpost of the
 * organisation enabled for tools (a 422 that names `body.config.outpost`), an alias, never an address
 * (a 422), and an outpost that is handed calls (a 409 `outpost_paused`). An instance need not be there.
 */
export function checkOutpostConnector(config: Record<string, unknown> | undefined): void {
  const named = String(config?.outpost ?? "");
  const endpoint = String(config?.endpoint ?? "");
  const extra = Object.keys(config ?? {}).filter((key) => !["outpost", "endpoint", "timeout_s"].includes(key));
  if (extra.length > 0)
    throw new ApiError(422, {
      title: "Unprocessable entity",
      detail: `${extra.map((key) => `\`${key}\``).join(", ")} has no place in an \`mcp-outpost\` connector: it stores only \`outpost\`, \`endpoint\` and \`timeout_s\``,
    });
  if (!ALIAS.test(endpoint))
    throw new ApiError(422, { title: "Unprocessable entity", detail: "`endpoint` is the alias of an endpoint in the outpost's policy file, never an address" });
  const outpost = outposts.find((o) => o.slug === named);
  if (!outpost || !outpost.capabilities.includes("tools")) {
    const detail = outpost ? `the outpost \`${named}\` is not enabled for tools: it serves no connector` : `\`${named}\` is not an outpost of acme: an administrator creates it first`;
    throw new ApiError(422, { title: "Unprocessable entity", detail, errors: [{ loc: ["body", "config", "outpost"], msg: detail }] });
  }
  if (outpost.status !== "active") {
    const resumed = outpost.status === "paused" ? "an administrator resumes it" : "that is final";
    throw routeRefusal("outpost_paused", `the outpost \`${outpost.slug}\` is ${outpost.status}: it is handed nothing (${resumed})`, outpost.slug, endpoint);
  }
}

/** Whether an instance that serves the alias polled lately: the presence the route reads. */
function serves(outpost: Stored, endpoint: string): boolean {
  return seenNow(outpost).some((i) => i.status === "active" && i.online && i.endpoints.includes(endpoint));
}

/**
 * Discovery through an outpost: the route's refusals first (paused, offline), then the tools of the
 * alias, each born closed. The demo's servers answer for the aliases its instances announce.
 */
export function discoverAtOutpost(connector: OrgConnector): { added: string[]; changed: string[]; removed: string[]; unchanged: number } {
  const named = String(connector.config?.outpost ?? "");
  const endpoint = String(connector.config?.endpoint ?? "");
  const outpost = outposts.find((o) => o.slug === named);
  if (!outpost || outpost.status !== "active") {
    const detail = outpost
      ? `the outpost \`${outpost.slug}\` is ${outpost.status}: it is handed nothing`
      : `\`${connector.name}\` names the outpost \`${named}\`, which its organisation does not have`;
    throw routeRefusal("outpost_paused", detail, named, endpoint);
  }
  if (!serves(outpost, endpoint)) {
    const online = seenNow(outpost).some((i) => i.online);
    const missing = online ? `no instance of it that serves \`${endpoint}\`` : "no instance of it";
    throw routeRefusal(
      "outpost_offline",
      `the outpost \`${outpost.slug}\` is offline: ${missing} polled within the poll window, and nothing reached its server`,
      outpost.slug,
      endpoint,
    );
  }
  const known = new Set((connector.operations ?? []).map((o) => o.name));
  const tools = endpoint === "warehouse" ? ["stock_level", "reserve_stock"] : ["search_issues", "get_issue", "create_issue"];
  for (const tool of tools) {
    if (!known.has(tool))
      (connector.operations ??= []).push({ name: tool, access: READS.has(tool) ? "read" : "write", policy: "forbidden", groups: [], input_schema: toolSchema(tool) });
  }
  return { added: tools.filter((tool) => !known.has(tool)), changed: [], removed: [], unchanged: tools.filter((tool) => known.has(tool)).length };
}

/** The input schema of the demo's tools at an outpost: `create_issue` takes the platform's idempotency key. */
export function toolSchema(tool: string): Record<string, unknown> {
  const properties: Record<string, unknown> = { title: { type: "string" } };
  if (tool === "create_issue") properties.action_key = { type: "string", description: "an idempotency key" };
  return { type: "object", properties };
}

function unprocessable(detail: string, errors: Array<{ loc: string[]; msg: string }>): ApiError {
  return new ApiError(422, { title: "Unprocessable entity", detail, errors });
}

/**
 * A change of an operation's rule at an outpost, judged as the API judges it (`outpost_writes.stated`,
 * ADR 0149 §6) on the rule as the change would leave it, then applied: `in_doubt_accepted` for a write
 * only, `platform_arguments` for a write whose input schema has each argument, and a write left open
 * under neither statement is a 422 that names both. Absent leaves a statement; null, false or an empty
 * list withdraws it.
 */
export function changeOperationAtOutpost(connector: OrgConnector, name: string, body: OperationPatch): ConnectorOperation {
  const operation = (connector.operations ?? []).find((o) => o.name === name);
  if (!operation) throw new ApiError(404, { title: "Not found", detail: `\`${connector.name}\` has no operation \`${name}\`` });
  const where = `${connector.name}/${name}`;
  const write = operation.access !== "read";
  const platform = "platform_arguments" in body ? [...new Set(body.platform_arguments ?? [])].sort() : [...(operation.platform_arguments ?? [])];
  const accepted = "in_doubt_accepted" in body ? body.in_doubt_accepted === true : operation.in_doubt_accepted === true;
  const policy = body.policy ?? operation.policy;
  const touches = "platform_arguments" in body || "in_doubt_accepted" in body;
  if (touches || policy === "allowed" || policy === "approval") {
    if (accepted && !write) {
      const msg = "an in-doubt outcome exists for a write of a connector reached through an outpost and for nothing else: there is nothing to accept here";
      throw unprocessable(`${where}: ${msg}`, [{ loc: ["body", "in_doubt_accepted"], msg }]);
    }
    if (platform.length > 0) {
      if (!write) {
        const msg = "the platform fills its arguments on a governed write: a read takes none";
        throw unprocessable(`${where}: ${msg}`, [{ loc: ["body", "platform_arguments"], msg }]);
      }
      const has = Object.keys((operation.input_schema as { properties?: Record<string, unknown> } | null | undefined)?.properties ?? {});
      const missing = platform.filter((argument) => !has.includes(argument));
      if (missing.length > 0) {
        const msg = `the operation has no argument ${missing.map((m) => `\`${m}\``).join(", ")} (its input schema declares: ${has.sort().join(", ") || "none"}): the platform cannot hand it one`;
        throw unprocessable(`${where}: ${msg}`, [{ loc: ["body", "platform_arguments"], msg }]);
      }
    }
    if ((policy === "allowed" || policy === "approval") && write && !platform.includes("action_key") && !accepted) {
      const said = "a write through an outpost says how it is delivered: this, or the other";
      throw unprocessable(
        `${where} is a write of a connector reached through an outpost. Such a write is delivered at most once: when the instance that claimed it never answers, nobody knows whether the outpost's server executed it. It is opened only when its rule says one of two things, and it says neither: \`platform_arguments\` lists \`action_key\` (the operation takes the platform's idempotency key, and may be delivered again with the same key), or \`in_doubt_accepted\` is true (an action that calls it may end \`in_doubt\`: never replayed, never compensated automatically). The operation stays as it was`,
        [
          { loc: ["body", "platform_arguments"], msg: said },
          { loc: ["body", "in_doubt_accepted"], msg: said },
        ],
      );
    }
  }
  if ("platform_arguments" in body) operation.platform_arguments = platform as ConnectorOperation["platform_arguments"];
  if ("in_doubt_accepted" in body) operation.in_doubt_accepted = accepted;
  return operation;
}

/**
 * Where a connector at an outpost lives, as an agent's access says it (ADR 0149 §10): the outpost it
 * routes to, its alias, its status, and whether an instance that serves the alias polled lately.
 */
export function whereItLivesNow(connector: OrgConnector): AgentAccessOutpost {
  const endpoint = String(connector.config?.endpoint ?? "");
  const outpost = outposts.find((o) => o.slug === connector.config?.outpost);
  if (!outpost) return { name: null, endpoint, status: null, online: false };
  return { name: outpost.slug, endpoint, status: outpost.status, online: serves(outpost, endpoint) };
}

/** The twelve management routes of `/orgs/{org}/outposts…`; `undefined` for any other path. */
export function outpostsApi(method: string, bare: string, query: string, body: unknown, connectors: OrgConnector[], actor: string): unknown {
  const route = /^\/orgs\/[^/]+\/outposts(?:\/([^/]+)(?:\/(.+))?)?$/.exec(bare);
  if (!route) return undefined;
  const [, slug, rest] = route;
  if (!slug) {
    if (method === "POST") {
      const create = body as { slug?: string; display_name?: string | null };
      if (!create.slug || !SLUG.test(create.slug)) throw new ApiError(422, { title: "Unprocessable Entity", detail: "slug: lower case letters, digits and hyphens" });
      if (outposts.some((o) => o.slug === create.slug))
        throw conflict("outpost_exists", `\`${create.slug}\` is already an outpost of acme`, { outpost: create.slug });
      const made: Stored = {
        id: drawn(create.slug, 32, HEX),
        slug: create.slug,
        display_name: create.display_name || create.slug,
        status: "active",
        capabilities: ["tools"],
        epoch: 0,
        created_by: actor,
        created_at: iso(0),
        enrolling: false,
      };
      outposts.push(made);
      instances[made.slug] = [];
      calls[made.slug] = [];
      return view(made, connectors);
    }
    const all: OutpostList = { items: [...outposts].sort((a, b) => a.slug.localeCompare(b.slug)).map((o) => view(o, connectors)) };
    return all;
  }
  const outpost = stored(decodeURIComponent(slug));
  if (!rest) {
    if (method === "DELETE") {
      const serving = connectorsOf(outpost, connectors);
      if (serving.length > 0)
        throw conflict("outpost_in_use", `\`${outpost.slug}\` still has connectors that route to it: route them elsewhere or delete them`, {
          outpost: outpost.slug,
          connectors: serving,
        });
      if (outpost.status !== "revoked")
        throw conflict("outpost_not_revoked", `\`${outpost.slug}\` is ${outpost.status}: revoke it first`, { outpost: outpost.slug });
      outposts.splice(outposts.indexOf(outpost), 1);
      delete instances[outpost.slug];
      delete calls[outpost.slug];
      return undefined;
    }
    return view(outpost, connectors);
  }
  if (rest === "pause" || rest === "resume") {
    if (outpost.status === "revoked") throw revokedRefusal(outpost);
    outpost.status = rest === "pause" ? "paused" : "active";
    return view(outpost, connectors);
  }
  if (rest === "revoke") {
    if (outpost.status !== "revoked") {
      for (const each of instances[outpost.slug] ?? []) {
        if (each.status !== "revoked") Object.assign(each, { status: "revoked", revoked_by: actor, revoked_at: iso(0), online: false });
      }
      outpost.status = "revoked";
      outpost.epoch += 1;
    }
    return view(outpost, connectors);
  }
  if (rest === "enrolments") {
    if (outpost.status === "revoked") throw revokedRefusal(outpost);
    outpost.enrolling = true;
    const issued: OutpostEnrolmentCreated = {
      id: `enrolment-${drawn(`${outpost.slug} ${Date.now()}`, 8, HEX)}`,
      token: `choe_${outpost.id}${drawn(`token ${Date.now()}`, 43)}`,
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
      control_plane: MOCK_DOOR_ORIGIN,
      door_keys: MOCK_DOOR_KEYS,
    };
    return issued;
  }
  if (rest === "instances") {
    const list: OutpostInstanceList = { items: listed(outpost) };
    return list;
  }
  if (rest === "calls") return page(outpost, query);
  const gesture = /^instances\/([^/]+)\/(approve|revoke)$/.exec(rest);
  if (!gesture) return undefined;
  const target = oneInstance(outpost, decodeURIComponent(gesture[1] ?? ""));
  if (gesture[2] === "revoke") {
    if (target.status !== "revoked") {
      Object.assign(target, { status: "revoked", revoked_by: actor, revoked_at: iso(0), online: false });
      outpost.epoch += 1;
    }
    return structuredClone(target);
  }
  const sent = (body as { fingerprint?: string } | undefined)?.fingerprint ?? "";
  if (!FINGERPRINT.test(sent)) throw new ApiError(422, { title: "Unprocessable Entity", detail: "fingerprint: not a fingerprint" });
  if (outpost.status === "revoked") throw revokedRefusal(outpost);
  if (target.status !== "pending") throw conflict("instance_not_pending", "this instance was already approved or revoked", { outpost: outpost.slug });
  if (sent !== target.fingerprint)
    throw conflict(
      "fingerprint_mismatch",
      "the fingerprint sent is not the one this instance enrolled with: compare it with what `choregos-outpost enrol` printed, and approve nothing that differs",
      { outpost: outpost.slug },
    );
  // Approved, its daemon starts and polls: it is there from now on, and says what its policy file serves.
  polling.add(target.id);
  Object.assign(target, { status: "active", approved_by: actor, approved_at: iso(0), last_seen_at: iso(0), online: true, endpoints: ["tracker"] });
  return structuredClone(target);
}
