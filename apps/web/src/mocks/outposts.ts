// SPDX-License-Identifier: Apache-2.0
/**
 * The demo's outposts (ADR 0147), answering as the management API does: one outpost in service with
 * an approved instance and one that waits for approval, one paused, their recent calls as digests,
 * and the API's own refusals (a slug taken, a fingerprint that differs, an outpost that is not
 * revoked or still in use).
 *
 * Nothing dials in here: there is no daemon in a demo. So that the enrolment can be followed to its
 * end, an instance "enrols" by itself a moment after a token is issued: it is there, pending, the next
 * time the instances are read.
 */
import { ApiError } from "@/lib/api";
import type {
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
