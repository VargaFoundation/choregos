// SPDX-License-Identifier: Apache-2.0
/**
 * The release channels of an agent, played in memory for the demo (S40-02, ADR 0071): production and testing
 * pointers, the checks, approvals, the history, the project's switch. It follows the API's rules closely
 * enough for the pages to render and for Playwright to walk a release; it is not the API.
 */
import { ApiError } from "@/lib/api";
import type {
  Agent,
  AgentChannelStats,
  AgentEvaluation,
  AgentExposure,
  AgentRelease,
  AgentReleaseCheck,
  AgentReleaseCreate,
  AgentReleaseDecision,
  AgentReleasePolicy,
  AgentReleaseReadiness,
  ProjectAgentChannel,
} from "@/lib/types";

type State = {
  stable: number | null;
  candidate: number | null;
  percent: number;
  since: string | null;
  policy: Required<Pick<AgentReleasePolicy, "on_publish">> & { stable: NonNullable<AgentReleasePolicy["stable"]> };
  releases: AgentRelease[];
  /** Finished runs of the candidate since it became the candidate: `[succeeded, failed]`. */
  runs: [number, number];
};

const states = new Map<string, State>();
const evaluations = new Map<string, unknown[]>();
let sequence = 0;
let projectChannel: ProjectAgentChannel["channel"] = "stable";
const nowIso = () => new Date().toISOString();
const DEFAULT_GATES = { require_candidate: false, min_candidate_runs: 0, step_up_minutes: 10, approvals: 0, separation_of_duties: true };

/** The agent the demo shows with a saved revision, a stricter policy and a baseline: the one to release. */
const SEEDED = "onboarding-coordinator";

function row(partial: Partial<AgentRelease> & Pick<AgentRelease, "channel" | "kind">, agent: string, by: string): AgentRelease {
  sequence += 1;
  return {
    id: `rel-${sequence}`,
    agent,
    status: "done",
    approvals_required: 0,
    decisions: [],
    checks: [],
    requested_by: by,
    requested_at: nowIso(),
    done_at: nowIso(),
    ...partial,
  };
}

function stateOf(agent: Agent): State {
  let state = states.get(agent.slug);
  if (state) return state;
  const seeded = agent.slug === SEEDED;
  const stable = seeded ? 1 : agent.latest_version;
  state = {
    stable,
    candidate: null,
    percent: 0,
    since: null,
    policy: seeded
      ? { on_publish: "candidate", stable: { ...DEFAULT_GATES, require_candidate: true, min_candidate_runs: 2 } }
      : { on_publish: "stable", stable: { ...DEFAULT_GATES } },
    releases: [row({ channel: "stable", kind: "baseline", version: stable }, agent.slug, "migration")],
    runs: [0, 0],
  };
  states.set(agent.slug, state);
  return state;
}

/** The agent, with the channels the demo holds for it: what `GET /agents/{slug}` would answer. */
export function decorateAgent(agent: Agent): Agent {
  const state = stateOf(agent);
  const pending = state.releases.find((r) => r.status === "pending_approval") ?? null;
  const channels = (version: number): Array<"stable" | "candidate"> => [
    ...(state.stable === version ? (["stable"] as const) : []),
    ...(state.candidate === version ? (["candidate"] as const) : []),
  ];
  const latest = Math.max(0, ...(agent.versions ?? []).map((v) => v.version));
  return {
    ...agent,
    latest_version: latest || agent.latest_version,
    can: { ...(agent.can ?? {}), release: agent.can?.release ?? agent.can?.publish ?? true } as Agent["can"],
    stable_version: state.stable,
    candidate_version: state.candidate,
    candidate_percent: state.percent,
    candidate_since: state.since,
    release_policy: structuredClone(state.policy),
    pending_release: pending ? structuredClone(pending) : null,
    versions: agent.versions?.map((v) => ({ ...v, channels: channels(v.version) })),
  };
}

/** The mock's way to say "two runs of the candidate finished": a real deployment gets them from work. */
export function addCandidateRuns(agents: Agent[], slug: string, succeeded: number, failed = 0): void {
  const agent = agents.find((a) => a.slug === slug);
  if (!agent) return;
  const state = stateOf(agent);
  state.runs = [state.runs[0] + succeeded, state.runs[1] + failed];
}

if (typeof window !== "undefined") {
  (window as unknown as { __mockAddCandidateRuns?: (slug: string, n: number) => void }).__mockAddCandidateRuns = (slug, n) => {
    const hook = (window as unknown as { __mockAgents?: Agent[] }).__mockAgents ?? [];
    addCandidateRuns(hook, slug, n);
  };
}

function stats(state: State, version: number | null, kind: "candidate" | "production"): AgentChannelStats | null {
  if (version == null) return null;
  const [ok, ko] = kind === "candidate" ? state.runs : [12, 1];
  const finished = ok + ko;
  return { version, runs: finished, succeeded: ok, failed: ko, success_rate: finished ? Math.round((ok / finished) * 10000) / 10000 : null, since: state.since };
}

function checksFor(state: State, channel: "stable" | "candidate", version: number, rollback: boolean): AgentReleaseCheck[] {
  const gates = state.policy.stable;
  const checks: AgentReleaseCheck[] = [];
  if (rollback) {
    checks.push({ name: "previously_in_production", required: true, passed: true, detail: `v${version} was in production before` });
  } else {
    checks.push({ name: "eval_suite", required: true, passed: true, detail: "0 case(s) pass" });
    if (state.stable != null)
      checks.push({
        name: "newer_than_production",
        required: true,
        passed: version > state.stable,
        detail: version > state.stable ? `v${version} is newer than production (v${state.stable})` : `v${version} is not newer than production (v${state.stable})`,
      });
    if (channel === "stable") {
      const needs = gates.require_candidate || (gates.min_candidate_runs ?? 0) > 0;
      const is = state.candidate === version;
      checks.push({
        name: "is_candidate",
        required: needs,
        passed: is,
        detail: is ? `v${version} is the candidate` : "production only takes the current candidate",
      });
      if (needs && (gates.min_candidate_runs ?? 0) > 0) {
        const runs = is ? state.runs[0] + state.runs[1] : 0;
        checks.push({ name: "candidate_runs", required: true, passed: runs >= (gates.min_candidate_runs ?? 0), detail: `${runs} of ${gates.min_candidate_runs} finished runs` });
      }
      if ((gates.approvals ?? 0) > 0)
        checks.push({ name: "approvals", required: false, passed: null, detail: `needs ${gates.approvals} approval(s) from other people before production moves` });
    }
  }
  if (channel === "stable" || state.percent > 0) {
    checks.push({ name: "human_session", required: true, passed: true, detail: "a person in a signed-in session" });
    checks.push({ name: "fresh_authentication", required: true, passed: true, detail: "signed in 0 min ago" });
  }
  return checks;
}

function wasInProduction(state: State, version: number): boolean {
  const done = state.releases.filter((r) => r.channel === "stable" && r.status === "done" && r.version != null);
  const baseline = Math.max(0, ...done.filter((r) => r.kind === "baseline").map((r) => r.version ?? 0));
  return done.some((r) => r.version === version) || version <= baseline;
}

function refuse(status: number, detail: string, errors?: unknown[]): never {
  throw new ApiError(status, { detail, errors });
}

function release(agent: Agent, state: State, body: AgentReleaseCreate, by: string): AgentRelease {
  if (agent.can?.release === false || (agent.can?.release == null && agent.can?.publish === false))
    refuse(403, agent.cannot_reason ?? "you may not release this agent");
  if (body.channel === "candidate" && body.version == null) {
    if (state.candidate == null) refuse(409, "nothing is in testing: there is no candidate to withdraw");
    const withdrawn = row({ channel: "candidate", kind: "withdraw", previous_version: state.candidate, version: null }, agent.slug, by);
    state.candidate = null;
    state.percent = 0;
    state.since = null;
    state.runs = [0, 0];
    state.releases.unshift(withdrawn);
    return withdrawn;
  }
  const version = body.version ?? state.candidate;
  if (version == null) refuse(409, "nothing is in testing: name the revision to release to production");
  if (!agent.versions?.some((v) => v.version === version)) refuse(404, `Agent version \`${agent.slug}@${version}\` does not exist`);
  if (body.channel === "stable") {
    if (version === state.stable) refuse(409, `v${version} is already in production`);
    const rollback = state.stable != null && version < state.stable && wasInProduction(state, version);
    const checks = checksFor(state, "stable", version, rollback);
    const failing = checks.filter((c) => c.required && c.passed !== true);
    if (failing.length)
      refuse(
        409,
        `v${version} cannot go to production: ${failing.length} check(s) fail`,
        failing.map((c) => ({ loc: ["checks", c.name], msg: c.detail ?? c.name, type: "release_check_failed" })),
      );
    const approvals = state.policy.stable.approvals ?? 0;
    if (!rollback && approvals > 0) {
      if (state.releases.some((r) => r.status === "pending_approval")) refuse(409, "a release already waits for approval: approve, reject or cancel it first");
      const pending = row(
        { channel: "stable", kind: "promote", version, previous_version: state.stable, status: "pending_approval", approvals_required: approvals, reason: body.reason ?? null, checks, done_at: null },
        agent.slug,
        by,
      );
      state.releases.unshift(pending);
      return pending;
    }
    const made = row({ channel: "stable", kind: rollback ? "rollback" : "promote", version, previous_version: state.stable, reason: body.reason ?? null, checks }, agent.slug, by);
    state.stable = version;
    if (state.candidate != null && state.candidate <= version) {
      state.candidate = null;
      state.percent = 0;
      state.since = null;
      state.runs = [0, 0];
    }
    state.releases.unshift(made);
    return made;
  }
  const checks = checksFor(state, "candidate", version, false);
  const failing = checks.filter((c) => c.required && c.passed !== true && c.name !== "human_session" && c.name !== "fresh_authentication");
  if (failing.length)
    refuse(409, `v${version} cannot go to testing: ${failing.length} check(s) fail`, failing.map((c) => ({ loc: ["checks", c.name], msg: c.detail ?? c.name, type: "release_check_failed" })));
  const percent = body.percent ?? (state.candidate != null ? state.percent : 0);
  const made = row({ channel: "candidate", kind: "promote", version, previous_version: state.candidate, percent, reason: body.reason ?? null, checks }, agent.slug, by);
  if (state.candidate !== version) {
    state.since = nowIso();
    state.runs = [0, 0];
  }
  state.candidate = version;
  state.percent = percent;
  state.releases.unshift(made);
  return made;
}

function decide(agent: Agent, state: State, id: string, body: AgentReleaseDecision, by: string): AgentRelease {
  const waiting = state.releases.find((r) => r.id === id);
  if (!waiting) refuse(404, `Agent release \`${id}\` does not exist`);
  if (waiting.status !== "pending_approval") refuse(409, `the release is already ${waiting.status}`);
  const entry = { by, decision: body.decision, reason: body.reason ?? null, at: nowIso(), auth_age_seconds: 12 };
  if (body.decision === "approve") {
    if (state.policy.stable.separation_of_duties !== false && (waiting.requested_by ?? "") === by)
      refuse(422, "separation of duties: neither the person who asked for this release nor the author of the revision approves it");
    if ((waiting.decisions ?? []).some((d) => d.by === by && d.decision === "approve")) refuse(409, "you have already approved this release");
    waiting.decisions = [...(waiting.decisions ?? []), entry];
    if ((waiting.decisions ?? []).filter((d) => d.decision === "approve").length >= (waiting.approvals_required ?? 1)) {
      waiting.status = "done";
      waiting.done_at = nowIso();
      state.stable = waiting.version ?? state.stable;
      if (state.candidate != null && state.candidate <= (waiting.version ?? 0)) state.candidate = null;
    }
    return waiting;
  }
  if (body.decision === "reject" && !(body.reason ?? "").trim()) refuse(422, "a rejection must say why");
  waiting.decisions = [...(waiting.decisions ?? []), entry];
  waiting.status = body.decision === "reject" ? "rejected" : "cancelled";
  return waiting;
}

/**
 * The channel routes of the demo. `undefined`: not one of them. `by` is who is signed in; `agents` the
 * registry the demo holds (the channels hang on its agents).
 */
export function agentReleasesApi(method: string, path: string, query: string, body: unknown, agents: Agent[], by: string): unknown {
  const org = /^\/orgs\/[^/]+\/agent-releases$/.test(path);
  if (org && method === "GET") {
    const status = new URLSearchParams(query).get("status");
    return agents
      .flatMap((a) => stateOf(a).releases)
      .filter((r) => !status || r.status === status)
      .map((r) => structuredClone(r));
  }
  const m = /^\/orgs\/[^/]+\/agents\/([^/]+)\/(releases|exposure|release-policy|versions\/(\d+)\/readiness)(?:\/([^/]+)\/decision)?$/.exec(path);
  if (m) {
    const agent = agents.find((a) => a.slug === m[1]);
    if (!agent) return undefined;
    const state = stateOf(agent);
    if (m[4] && method === "POST") return structuredClone(decide(agent, state, m[4], body as AgentReleaseDecision, by));
    if (m[2] === "releases" && method === "GET") return structuredClone(state.releases);
    if (m[2] === "releases" && method === "POST") return structuredClone(release(agent, state, body as AgentReleaseCreate, by));
    if (m[2] === "exposure" && method === "GET") {
      const exposure: AgentExposure = {
        stable_version: state.stable,
        candidate_version: state.candidate,
        candidate_percent: state.percent,
        projects: projectChannel === "candidate" && state.candidate != null ? [{ project: "billing-api", source: "project_channel", channel: "candidate", version: state.candidate }] : [],
      };
      return exposure;
    }
    if (m[2] === "release-policy" && method === "PUT") {
      const policy = body as AgentReleasePolicy;
      if (policy.on_publish === "stable" && (policy.stable?.require_candidate || (policy.stable?.min_candidate_runs ?? 0) > 0))
        refuse(422, "`on_publish: stable` sends a revision straight to production, so it is never a candidate");
      state.policy = { on_publish: policy.on_publish ?? "stable", stable: { ...DEFAULT_GATES, ...(policy.stable ?? {}) } };
      return decorateAgent(agent);
    }
    if (m[3] !== undefined && method === "GET") {
      const channel = new URLSearchParams(query).get("channel") === "candidate" ? "candidate" : "stable";
      const version = Number(m[3]);
      const rollback = channel === "stable" && state.stable != null && version < state.stable && wasInProduction(state, version);
      const checks = checksFor(state, channel, version, rollback);
      const readiness: AgentReleaseReadiness = {
        version,
        channel,
        kind: rollback ? "rollback" : "promote",
        ready: checks.every((c) => !c.required || c.passed === true),
        checks,
        candidate: stats(state, state.candidate, "candidate"),
        production: stats(state, state.stable, "production"),
      };
      return readiness;
    }
  }
  const ev = /^\/orgs\/[^/]+\/agents\/([^/]+)\/versions\/(\d+)\/evaluations$/.exec(path);
  if (ev) {
    const agent = agents.find((a) => a.slug === ev[1]);
    if (!agent) return undefined;
    const version = Number(ev[2]);
    const list = (evaluations.get(`${agent.slug}@${version}`) ?? []) as AgentEvaluation[];
    if (method === "GET") return structuredClone(list);
    if (method === "POST") {
      const live = (agent.versions?.find((v) => v.version === version)?.spec.evals ?? []).filter((c) => c.live === true);
      if (live.length === 0) refuse(409, `v${version} has no live case in its eval suite: mark a case \`live\` first`);
      sequence += 1;
      const made: AgentEvaluation = {
        id: `eval-${sequence}`,
        version,
        project: (body as { project: string }).project,
        status: "passed",
        score: 1,
        budget_usd: live.reduce((t, c) => t + Math.min(0.5, c.max_cost_usd ?? 0.25), 0),
        started_by: by,
        started_at: nowIso(),
        cases: live.map((c, i) => ({ name: c.name, run_id: `trial-${sequence}-${i}`, status: "passed", passed: true, budget_usd: 0.25, cost_usd: 0.08, reason: null })),
      };
      evaluations.set(`${agent.slug}@${version}`, [made, ...list]);
      return structuredClone(made);
    }
  }
  if (/^\/projects\/[^/]+\/agent-channel$/.test(path)) {
    if (method === "GET") return { channel: projectChannel };
    if (method === "PUT") {
      projectChannel = (body as ProjectAgentChannel).channel;
      return { channel: projectChannel };
    }
  }
  return undefined;
}

/** Read before a revision is added: an agent seen for the first time is in production at its latest revision. */
export function touchAgent(agent: Agent): void {
  stateOf(agent);
}

/** What saving a revision does to the channels — the agent's `on_publish` — and where the revision sits now. */
export function savedRevision(agent: Agent, version: number): Array<"stable" | "candidate"> {
  const state = stateOf(agent);
  const policy = state.policy;
  if (policy.on_publish === "stable") {
    if ((policy.stable.approvals ?? 0) > 0) {
      state.releases.unshift(
        row(
          { channel: "stable", kind: "promote", version, previous_version: state.stable, status: "pending_approval", approvals_required: policy.stable.approvals, done_at: null, reason: "saved with on_publish: stable" },
          agent.slug,
          "you",
        ),
      );
      return [];
    }
    state.releases.unshift(row({ channel: "stable", kind: "publish", version, previous_version: state.stable }, agent.slug, "you"));
    state.stable = version;
    return ["stable"];
  }
  if (policy.on_publish === "candidate") {
    state.releases.unshift(row({ channel: "candidate", kind: "publish", version, previous_version: state.candidate, percent: state.percent }, agent.slug, "you"));
    state.candidate = version;
    state.since = nowIso();
    state.runs = [0, 0];
    return ["candidate"];
  }
  return [];
}

/** A new agent that came with a release policy keeps it. */
export function applyPolicy(agent: Agent, policy: AgentReleasePolicy | null | undefined): void {
  if (!policy) return;
  const state = stateOf(agent);
  state.policy = { on_publish: policy.on_publish ?? "stable", stable: { ...DEFAULT_GATES, ...(policy.stable ?? {}) } };
}
