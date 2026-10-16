// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState, type ReactNode } from "react";
import { ChannelBadges, RevisionChannels } from "@/components/agents/channels";
import { GesteConfirme } from "@/components/geste-confirme";
import {
  Alert,
  Badge,
  Button,
  Card,
  Disclosure,
  Drawer,
  Empty,
  ErrorNote,
  Field,
  Input,
  Select,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  type Tone,
} from "@/components/ui";
import { api } from "@/lib/api";
import { percent, relative, shortDate } from "@/lib/format";
import { useSession } from "@/lib/session";
import type {
  Agent,
  AgentChannelStats,
  AgentRelease,
  AgentReleaseCheck,
  AgentReleasePolicy,
  AgentVersion,
} from "@/lib/types";

/**
 * The lifecycle of an agent's revisions (S40-02, ADR 0071): eval, testing, production. A revision is a
 * version that never changes; production and testing each point at one. The server decides what is
 * allowed (`can.release`, the checks, the sign-in age): this file shows its answers and never guesses.
 */

const SEPARATION =
  "Separation of duties: neither the person who asked for this release nor the author of the revision approves it.";

/** Everything a release changes: what the page reads has to be read again. */
function useRefresh(org: string, slug: string) {
  const client = useQueryClient();
  return async () => {
    await Promise.all(
      [
        ["agent", org, slug],
        ["agents", org],
        ["agent-releases", org, slug],
        ["agent-exposure", org, slug],
        ["agent-readiness", org, slug],
        ["agent-metrics", org, slug],
        ["agent-releases-pending", org],
      ].map((queryKey) => client.invalidateQueries({ queryKey })),
    );
  };
}

/** The revisions a rollback may go back to: those that were in production before (the history says). */
export function rollbackTargets(agent: Pick<Agent, "stable_version" | "versions">, history: AgentRelease[]): number[] {
  const done = history.filter((r) => r.channel === "stable" && r.status === "done" && r.version != null);
  const baseline = Math.max(0, ...done.filter((r) => r.kind === "baseline").map((r) => r.version ?? 0));
  const seen = new Set(done.map((r) => r.version as number));
  return (agent.versions ?? [])
    .map((v) => v.version)
    .filter((v) => v !== agent.stable_version && (seen.has(v) || v <= baseline))
    .sort((a, b) => b - a);
}

/** The latest revision that sits in no channel and is newer than production: the one to send to testing. */
export function unreleasedRevision(agent: Pick<Agent, "stable_version" | "versions">): AgentVersion | undefined {
  const latest = [...(agent.versions ?? [])].sort((a, b) => b.version - a.version)[0];
  if (!latest || (latest.channels ?? []).length > 0) return undefined;
  if (agent.stable_version != null && latest.version <= agent.stable_version) return undefined;
  return latest;
}

const CHECK_LABEL: Record<string, string> = {
  eval_suite: "The eval suite passes",
  newer_than_production: "Newer than production",
  is_candidate: "Is the revision in testing",
  candidate_runs: "Enough finished runs in testing",
  candidate_success_rate: "Success rate in testing",
  soak: "Time spent in testing",
  previously_in_production: "Was in production before",
  human_session: "Made by a person in a signed-in session",
  fresh_authentication: "A recent sign-in",
  approvals: "Approvals",
};

function checkMark(check: AgentReleaseCheck): { mark: string; word: string; tone: Tone } {
  if (check.passed === true) return { mark: "✓", word: "passes", tone: "ok" };
  if (check.passed === false) return { mark: "✗", word: check.required === false ? "does not pass (not required)" : "fails", tone: "danger" };
  return { mark: "—", word: "not applicable", tone: "neutral" };
}

export function CheckList({ checks }: { checks: AgentReleaseCheck[] }) {
  return (
    <ul className="space-y-1.5 text-sm" aria-label="Checks" data-testid="checks">
      {checks.map((check) => {
        const { mark, word, tone } = checkMark(check);
        return (
          <li key={check.name} className="flex gap-2" data-check={check.name} data-passed={String(check.passed)}>
            <Badge tone={tone} dot={false} aria-hidden>
              {mark}
            </Badge>
            <span>
              <span className="font-medium">{CHECK_LABEL[check.name] ?? check.name}</span>
              <span className="sr-only"> {word}</span>
              {check.detail && <span className="block text-xs text-ink-muted">{check.detail}</span>}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function stepUpMinutes(agent: Agent): number {
  return agent.release_policy?.stable?.step_up_minutes ?? 10;
}

/** What blocks the click: the required checks about the revision. A stale sign-in does not — the click asks for a new one. */
export function blockers(checks: AgentReleaseCheck[]): AgentReleaseCheck[] {
  return checks.filter((c) => c.required !== false && c.passed !== true && c.name !== "fresh_authentication");
}

function StatsLine({ label, stats, testid }: { label: string; stats: AgentChannelStats | null | undefined; testid?: string }) {
  if (!stats) return null;
  return (
    <p className="text-xs text-ink-muted" data-testid={testid ?? `stats-${label.toLowerCase()}`}>
      {label}: {stats.runs ?? 0} finished run{(stats.runs ?? 0) === 1 ? "" : "s"}
      {stats.success_rate != null ? `, ${percent(stats.success_rate)} succeeded` : ""}
    </p>
  );
}

/** The checklist before a release to production (or a rollback), then the release itself. */
export function ReadinessDialog({
  org,
  agent,
  version,
  open,
  onClose,
}: {
  org: string;
  agent: Agent;
  version: number;
  open: boolean;
  onClose: () => void;
}) {
  const refresh = useRefresh(org, agent.slug);
  const readiness = useQuery({
    queryKey: ["agent-readiness", org, agent.slug, version, "stable"],
    queryFn: () => api.agentReleaseReadiness(org, agent.slug, version, "stable"),
    enabled: open,
    // The checklist is read afresh each time it is opened: two runs may have finished since.
    staleTime: 0,
    refetchOnMount: "always",
  });
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const r = readiness.data;
  const blocking = r ? blockers(r.checks ?? []) : [];
  const stale = (r?.checks ?? []).some((c) => c.name === "fresh_authentication" && c.passed === false);
  const approvals = agent.release_policy?.stable?.approvals ?? 0;
  const rollback = r?.kind === "rollback";
  const waits = !rollback && approvals > 0;
  const verb = rollback ? `Roll back to v${version}` : waits ? "Ask for approval" : "Release to production";

  async function release() {
    setError(null);
    setBusy(true);
    try {
      const made = await api.releaseAgentVersion(org, agent.slug, { channel: "stable", version, reason: reason.trim() || null });
      setDone(
        made.status === "pending_approval"
          ? `v${version} waits for ${made.approvals_required} approval${made.approvals_required === 1 ? "" : "s"}; production still runs v${agent.stable_version}.`
          : `v${version} is in production.`,
      );
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Drawer open={open} onClose={onClose} title={rollback ? `Roll back to v${version}` : `Release v${version} to production`}>
      <div className="space-y-4">
        {!r ? (
          <p className="text-sm text-ink-muted">{readiness.error ? String(readiness.error) : "Checking the revision…"}</p>
        ) : (
          <>
            <CheckList checks={r.checks ?? []} />
            <div className="space-y-1">
              <StatsLine label="Testing" stats={r.candidate} />
              <StatsLine label="Production" stats={r.production} />
            </div>
            {agent.scope !== "personal" && (
              <p className="text-sm text-ink-muted" data-testid="needs-sign-in">
                {stale
                  ? `Needs a sign-in less than ${stepUpMinutes(agent)} min old: yours is older, so you will be asked to sign in again.`
                  : `Needs a sign-in less than ${stepUpMinutes(agent)} min old.`}
              </p>
            )}
            {waits && (
              <p className="text-sm text-ink-muted" data-testid="needs-approvals">
                Needs {approvals} approval{approvals === 1 ? "" : "s"} from other people before production moves.
              </p>
            )}
            {blocking.length > 0 && (
              <Alert tone="warn" title="Not ready">
                {blocking.map((c) => CHECK_LABEL[c.name] ?? c.name).join(", ")}.
              </Alert>
            )}
            <Field label="Reason" hint="Kept in the history.">
              {(props) => <Input {...props} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />}
            </Field>
            <div className="flex gap-2">
              <Button tone="primary" disabled={busy || blocking.length > 0 || done != null} onClick={() => void release()}>
                {verb}
              </Button>
              <Button onClick={onClose}>{done ? "Close" : "Cancel"}</Button>
            </div>
          </>
        )}
        {done && <p role="status">{done}</p>}
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Drawer>
  );
}

/** The sentence under a disabled control: the server's own words, never a guess from roles. */
function WhyNot({ agent, id }: { agent: Agent; id: string }) {
  if (agent.can?.release !== false) return null;
  return (
    <p id={id} className="text-xs text-ink-muted" data-testid="release-cannot-reason">
      {agent.cannot_reason ?? "You may not release this agent."}
    </p>
  );
}

function Who({ exposure, agent }: { exposure: Awaited<ReturnType<typeof api.agentExposure>> | undefined; agent: Agent }) {
  const following = (exposure?.projects ?? []).filter((p) => p.channel === "candidate");
  return (
    <ul className="space-y-1 text-xs text-ink-muted" data-testid="who-gets-testing">
      {following.map((p) => (
        <li key={p.project}>
          {p.project} — {p.source === "project_channel" ? "follows testing" : "pinned on testing"}
        </li>
      ))}
      <li>
        {(agent.candidate_percent ?? 0) > 0
          ? `${agent.candidate_percent} % of the work items of the projects that follow production`
          : "No share of the other projects' work items"}
      </li>
    </ul>
  );
}

/** Three cards: the revision not in a channel yet, testing, production. */
export function ReleasePipeline({ org, agent, history }: { org: string; agent: Agent; history: AgentRelease[] }) {
  const refresh = useRefresh(org, agent.slug);
  const locked = agent.can?.release === false;
  const whyId = `why-${agent.slug}`;
  const exposure = useQuery({ queryKey: ["agent-exposure", org, agent.slug], queryFn: () => api.agentExposure(org, agent.slug) });
  const metrics = useQuery({ queryKey: ["agent-metrics", org, agent.slug], queryFn: () => api.agentMetrics(org, agent.slug) });
  const fresh = unreleasedRevision(agent);
  const candidate = agent.candidate_version ?? null;
  const readyForTesting = useQuery({
    queryKey: ["agent-readiness", org, agent.slug, fresh?.version, "candidate"],
    queryFn: () => api.agentReleaseReadiness(org, agent.slug, fresh!.version, "candidate"),
    enabled: fresh != null,
  });
  const forProduction = useQuery({
    queryKey: ["agent-readiness", org, agent.slug, candidate, "stable"],
    queryFn: () => api.agentReleaseReadiness(org, agent.slug, candidate!, "stable"),
    enabled: candidate != null,
  });
  const [share, setShare] = useState(10);
  const [newShare, setNewShare] = useState<number | null>(null);
  const [promoting, setPromoting] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rollbackTo, setRollbackTo] = useState("");
  const targets = rollbackTargets(agent, history);
  const lastStable = history.find((r) => r.channel === "stable" && r.status === "done" && r.version === agent.stable_version);
  const byVersion = new Map((metrics.data?.by_version ?? []).map((v) => [v.version, v]));
  const pending = agent.pending_release ?? null;

  async function act(body: Parameters<typeof api.releaseAgentVersion>[2], done: string) {
    setError(null);
    setMessage(null);
    try {
      await api.releaseAgentVersion(org, agent.slug, body);
      setMessage(done);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    }
  }

  const testingBlockers = blockers(readyForTesting.data?.checks ?? []);
  const evalCheck = (readyForTesting.data?.checks ?? []).find((c) => c.name === "eval_suite");

  return (
    <div className="space-y-3" data-testid="release-pipeline">
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Revisions" eyebrow="Eval">
          <div className="space-y-3 text-sm">
            {fresh ? (
              <>
                <p>
                  <span className="font-mono">v{fresh.version}</span> is saved and sits in no channel
                  {fresh.created_by ? ` (by ${fresh.created_by})` : ""}.
                </p>
                {evalCheck && (
                  <p className="text-xs" data-testid="revision-eval">
                    <Badge tone={evalCheck.passed ? "ok" : "danger"} dot={false}>
                      {evalCheck.passed ? "✓" : "✗"}
                    </Badge>{" "}
                    {evalCheck.passed ? "Its eval suite passes." : "Its eval suite fails."}
                    {evalCheck.detail ? ` ${evalCheck.detail}.` : ""}
                  </p>
                )}
                <Field label={`Share of work items: ${share} %`} hint="Of the projects that follow production. Projects that follow testing always get it.">
                  {(props) => (
                    <input
                      {...props}
                      type="range"
                      min={0}
                      max={100}
                      step={5}
                      value={share}
                      onChange={(e) => setShare(Number(e.target.value))}
                      className="w-full"
                    />
                  )}
                </Field>
                <Button
                  tone="primary"
                  disabled={locked || testingBlockers.length > 0}
                  aria-describedby={locked ? whyId : undefined}
                  onClick={() => void act({ channel: "candidate", version: fresh.version, percent: share }, `v${fresh.version} is in testing at ${share} %.`)}
                >
                  Send to testing
                </Button>
                {testingBlockers.length > 0 && (
                  <p className="text-xs text-ink-muted" role="status">
                    Not ready: {testingBlockers.map((c) => c.detail ?? CHECK_LABEL[c.name] ?? c.name).join("; ")}.
                  </p>
                )}
              </>
            ) : (
              <p className="text-ink-muted">Every revision is in a channel, or older than production.</p>
            )}
            <p className="text-xs text-ink-muted">
              {agent.latest_version} revision{agent.latest_version === 1 ? "" : "s"}; the latest is v{agent.latest_version}.
            </p>
          </div>
        </Card>

        <Card title="Testing" eyebrow="Candidate">
          <div className="space-y-3 text-sm" data-testid="testing-card">
            {candidate == null ? (
              <p className="text-ink-muted">Nothing is in testing: every project runs production.</p>
            ) : (
              <>
                <p>
                  <span className="font-mono">v{candidate}</span>
                  {(agent.candidate_percent ?? 0) > 0 ? ` · ${agent.candidate_percent} % of work items` : ""}
                  {agent.candidate_since ? ` · since ${relative(agent.candidate_since)}` : ""}
                </p>
                <Who exposure={exposure.data} agent={agent} />
                <StatsLine label="Testing" stats={forProduction.data?.candidate} />
                <StatsLine label="Production" testid="compare-production" stats={forProduction.data?.production ?? byVersionStats(byVersion, agent.stable_version)} />
                {newShare == null ? (
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" disabled={locked} aria-describedby={locked ? whyId : undefined} onClick={() => setNewShare(agent.candidate_percent ?? 0)}>
                      Change share
                    </Button>
                    <GesteConfirme
                      question={`Withdraw v${candidate} from testing? Projects that follow testing go back to production.`}
                      confirmer="Withdraw"
                      disabled={locked}
                      action={async () => {
                        await api.releaseAgentVersion(org, agent.slug, { channel: "candidate", version: null });
                        await refresh();
                      }}
                    >
                      Withdraw
                    </GesteConfirme>
                    {pending == null && (
                      <Button size="sm" tone="primary" disabled={locked} aria-describedby={locked ? whyId : undefined} onClick={() => setPromoting(candidate)}>
                        Promote to production…
                      </Button>
                    )}
                  </div>
                ) : (
                  <div className="flex flex-wrap items-end gap-2">
                    <Field label={`Share: ${newShare} %`}>
                      {(props) => (
                        <input {...props} type="range" min={0} max={100} step={5} value={newShare} onChange={(e) => setNewShare(Number(e.target.value))} />
                      )}
                    </Field>
                    <Button
                      size="sm"
                      tone="primary"
                      onClick={() => {
                        void act({ channel: "candidate", version: candidate, percent: newShare }, `The share of v${candidate} is ${newShare} %.`);
                        setNewShare(null);
                      }}
                    >
                      Apply share
                    </Button>
                    <Button size="sm" onClick={() => setNewShare(null)}>
                      Cancel
                    </Button>
                  </div>
                )}
                {pending != null && <p className="text-xs text-ink-muted">v{pending.version} waits for approval: see below.</p>}
              </>
            )}
          </div>
        </Card>

        <Card title="Production" eyebrow="Stable">
          <div className="space-y-3 text-sm" data-testid="production-card">
            {agent.stable_version == null ? (
              <p className="text-ink-muted">No production pointer: runs use the latest revision.</p>
            ) : (
              <>
                <p>
                  <span className="font-mono">v{agent.stable_version}</span>
                  {lastStable?.done_at ? ` · since ${relative(lastStable.done_at)}` : ""}
                  {lastStable?.requested_by ? ` · by ${lastStable.requested_by}` : ""}
                </p>
                <StatsLine label="Production" stats={forProduction.data?.production ?? byVersionStats(byVersion, agent.stable_version)} />
              </>
            )}
            {targets.length > 0 ? (
              <div className="flex flex-wrap items-end gap-2">
                <Field label="Roll back to">
                  {(props) => (
                    <Select {...props} value={rollbackTo} onChange={(e) => setRollbackTo(e.target.value)} disabled={locked}>
                      <option value="">—</option>
                      {targets.map((v) => (
                        <option key={v} value={v}>
                          v{v}
                        </option>
                      ))}
                    </Select>
                  )}
                </Field>
                <Button size="sm" disabled={locked || !rollbackTo} aria-describedby={locked ? whyId : undefined} onClick={() => setPromoting(Number(rollbackTo))}>
                  {rollbackTo ? `Roll back to v${rollbackTo}…` : "Roll back…"}
                </Button>
              </div>
            ) : (
              <p className="text-xs text-ink-muted">No earlier revision was in production: nothing to roll back to.</p>
            )}
          </div>
        </Card>
      </div>
      <WhyNot agent={agent} id={whyId} />
      {message && <p role="status" className="text-sm">{message}</p>}
      {error && <ErrorNote>{error}</ErrorNote>}
      {promoting != null && (
        <ReadinessDialog key={promoting} org={org} agent={agent} version={promoting} open onClose={() => setPromoting(null)} />
      )}
    </div>
  );
}

function byVersionStats(map: Map<number, { runs?: number; succeeded?: number; failed?: number; success_rate?: number | null }>, version: number | null | undefined): AgentChannelStats | null {
  const v = version == null ? undefined : map.get(version);
  return v && version != null ? { version, runs: v.runs, succeeded: v.succeeded, failed: v.failed, success_rate: v.success_rate } : null;
}

/** A promotion that waits: who asked, the answers so far, and what the caller may answer. */
export function PendingRelease({ org, agent }: { org: string; agent: Agent }) {
  const refresh = useRefresh(org, agent.slug);
  const { me } = useSession();
  const pending = agent.pending_release;
  const [error, setError] = useState<string | null>(null);
  if (!pending) return null;
  const mine = (me?.email ?? "").toLowerCase();
  const asked = (pending.requested_by ?? "").toLowerCase() === mine;
  const approved = (pending.decisions ?? []).filter((d) => d.decision === "approve");
  const alreadyApproved = approved.some((d) => d.by.toLowerCase() === mine);
  const separation = agent.release_policy?.stable?.separation_of_duties !== false;
  const locked = agent.can?.release === false;
  const reason = locked
    ? (agent.cannot_reason ?? "You may not decide on this release.")
    : alreadyApproved
      ? "You have already approved this release."
      : separation && asked
        ? SEPARATION
        : null;
  const decide = (decision: "approve" | "reject" | "cancel") => async (text = "") => {
    setError(null);
    try {
      await api.decideAgentRelease(org, agent.slug, pending.id, { decision, reason: text || null });
      await refresh();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "refused";
      setError(message);
      throw cause;
    }
  };
  return (
    <Card title="Waiting for approval" eyebrow="Production" data-testid="pending-release">
      <div className="space-y-3 text-sm">
        <p>
          <span className="font-mono">v{pending.version}</span> to production, asked by {pending.requested_by ?? "someone"}{" "}
          {relative(pending.requested_at)}
          {pending.reason ? ` — “${pending.reason}”` : ""}. Production still runs v{agent.stable_version}.
        </p>
        <p data-testid="approvals-count">
          {approved.length} of {pending.approvals_required} approval{pending.approvals_required === 1 ? "" : "s"}.
        </p>
        {(pending.decisions ?? []).length > 0 && (
          <ul className="space-y-1 text-xs text-ink-muted">
            {(pending.decisions ?? []).map((d) => (
              <li key={`${d.by}-${d.at}`}>
                {d.by}: {d.decision}
                {d.reason ? ` — ${d.reason}` : ""} ({relative(d.at)})
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap items-start gap-2">
          <Button tone="primary" size="sm" disabled={reason != null} aria-describedby="why-no-approve" onClick={() => void decide("approve")().catch(() => undefined)}>
            Approve
          </Button>
          <GesteConfirme
            question={`Reject v${pending.version}? The request closes; ask again after a change.`}
            confirmer="Reject"
            raison={{ label: "Why", requise: true }}
            disabled={locked}
            action={decide("reject")}
          >
            Reject
          </GesteConfirme>
          <GesteConfirme
            question="Cancel this request?"
            confirmer="Cancel request"
            tonDuBouton="default"
            disabled={locked && !asked}
            action={decide("cancel")}
          >
            Cancel request
          </GesteConfirme>
        </div>
        {reason && (
          <p id="why-no-approve" className="text-xs text-ink-muted" data-testid="approve-reason">
            {reason}
          </p>
        )}
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Card>
  );
}

function num(value: string): number | null {
  const n = Number(value);
  return value.trim() === "" || Number.isNaN(n) ? null : n;
}

/** What saving a revision does, and the gates of production. Folded: most agents never touch it. */
export function ReleasePolicyForm({ org, agent }: { org: string; agent: Agent }) {
  const refresh = useRefresh(org, agent.slug);
  const policy = agent.release_policy ?? {};
  const gates = policy.stable ?? {};
  const locked = agent.can?.release === false;
  const [onPublish, setOnPublish] = useState<NonNullable<AgentReleasePolicy["on_publish"]>>(policy.on_publish ?? "stable");
  const [requireCandidate, setRequireCandidate] = useState(gates.require_candidate ?? false);
  const [runs, setRuns] = useState(String(gates.min_candidate_runs ?? 0));
  const [rate, setRate] = useState(gates.min_success_rate != null ? String(gates.min_success_rate) : "");
  const [soak, setSoak] = useState(gates.min_soak_hours != null ? String(gates.min_soak_hours) : "");
  const [stepUp, setStepUp] = useState(String(gates.step_up_minutes ?? 10));
  const [approvals, setApprovals] = useState(String(gates.approvals ?? 0));
  const [separation, setSeparation] = useState(gates.separation_of_duties ?? true);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const toStable = onPublish === "stable";
  const summary =
    `${{ stable: "Saving goes to production", candidate: "Saving goes to testing", none: "Saving moves nothing" }[policy.on_publish ?? "stable"]}` +
    (gates.approvals ? ` · ${gates.approvals} approval${gates.approvals === 1 ? "" : "s"}` : "");

  async function save() {
    setError(null);
    setDone(null);
    try {
      await api.setAgentReleasePolicy(org, agent.slug, {
        on_publish: onPublish,
        stable: {
          require_candidate: toStable ? false : requireCandidate,
          min_candidate_runs: toStable ? 0 : (num(runs) ?? 0),
          min_success_rate: toStable ? null : num(rate),
          min_soak_hours: toStable ? null : num(soak),
          step_up_minutes: num(stepUp) ?? 10,
          approvals: num(approvals) ?? 0,
          separation_of_duties: separation,
        },
      });
      setDone("Policy saved.");
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    }
  }

  const field = (label: string, value: string, set: (v: string) => void, extra: { min?: number; max?: number; step?: string; hint?: string; disabled?: boolean } = {}) => (
    <Field label={label} hint={extra.hint}>
      {(props) => <Input {...props} type="number" min={extra.min} max={extra.max} step={extra.step} value={value} onChange={(e) => set(e.target.value)} disabled={locked || extra.disabled} />}
    </Field>
  );

  return (
    <Disclosure title="Release policy" summary={summary} id="release-policy">
      <form
        className="space-y-3"
        aria-label="Release policy"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <Field label="When a revision is saved" hint="A saved revision can go to production at once (what happened before channels), to testing, or nowhere.">
          {(props) => (
            <Select {...props} value={onPublish} onChange={(e) => setOnPublish(e.target.value as typeof onPublish)} disabled={locked}>
              <option value="stable">It goes to production</option>
              <option value="candidate">It goes to testing</option>
              <option value="none">It moves nothing</option>
            </Select>
          )}
        </Field>
        <fieldset className="grid gap-3 md:grid-cols-3" disabled={locked}>
          <legend className="mb-1 text-xs font-medium text-ink-muted">Gates of production</legend>
          <label className="flex items-center gap-2 text-sm md:col-span-3">
            <input type="checkbox" checked={requireCandidate && !toStable} onChange={(e) => setRequireCandidate(e.target.checked)} disabled={toStable || locked} />
            Production only takes the revision that was in testing
          </label>
          {field("Finished runs in testing", runs, setRuns, { min: 0, max: 10000, disabled: toStable })}
          {field("Success rate, 0 to 1", rate, setRate, { min: 0, max: 1, step: "0.05", disabled: toStable })}
          {field("Hours in testing", soak, setSoak, { min: 0, max: 720, disabled: toStable })}
          {field("Sign-in age, minutes", stepUp, setStepUp, { min: 1, max: 120 })}
          {field("Approvals from other people", approvals, setApprovals, { min: 0, max: 3 })}
          <label className="flex items-center gap-2 text-sm md:col-span-3">
            <input type="checkbox" checked={separation} onChange={(e) => setSeparation(e.target.checked)} disabled={locked} />
            Neither the requester nor the revision&apos;s author approves
          </label>
        </fieldset>
        {toStable && <p className="text-xs text-ink-muted">A revision saved straight to production is never a candidate: the testing gates apply when saving goes to testing.</p>}
        <p className="text-xs text-ink-muted">Loosening a policy needs a sign-in less than {stepUpMinutes(agent)} min old.</p>
        <div className="flex gap-2">
          <Button type="submit" tone="primary" size="sm" disabled={locked}>
            Save policy
          </Button>
        </div>
        {done && <p role="status" className="text-sm">{done}</p>}
        {error && <ErrorNote>{error}</ErrorNote>}
      </form>
    </Disclosure>
  );
}

const STATUS_TONE: Record<AgentRelease["status"], Tone> = {
  done: "ok",
  pending_approval: "warn",
  rejected: "danger",
  cancelled: "neutral",
  superseded: "neutral",
};

const KIND_LABEL: Record<AgentRelease["kind"], string> = {
  publish: "Saved",
  promote: "Released",
  rollback: "Rolled back",
  withdraw: "Withdrawn",
  baseline: "Before channels",
};

/** Every move of the channels, newest first: when, what, from what to what, who, why, and how it ended. */
export function ReleaseHistory({ history }: { history: AgentRelease[] }) {
  if (history.length === 0) return <Empty title="No release yet">The history starts with the first revision.</Empty>;
  return (
    <Card title="Release history" padding="none" className="overflow-hidden">
      <Table aria-label="Release history" data-testid="release-history">
        <THead>
          <TR>
            <TH>When</TH>
            <TH>What</TH>
            <TH>Channel</TH>
            <TH>Revision</TH>
            <TH>By</TH>
            <TH>Reason</TH>
            <TH>Status</TH>
          </TR>
        </THead>
        <TBody>
          {history.map((r) => (
            <TR key={r.id}>
              <TD className="font-mono text-xs">{shortDate(r.requested_at)}</TD>
              <TD>{KIND_LABEL[r.kind]}</TD>
              <TD>{r.channel === "stable" ? "Production" : "Testing"}</TD>
              <TD className="font-mono">
                {r.kind === "withdraw"
                  ? `v${r.previous_version ?? "?"} out`
                  : `${r.previous_version != null && r.kind !== "baseline" ? `v${r.previous_version} → ` : ""}v${r.version}${r.channel === "candidate" && r.percent ? ` · ${r.percent} %` : ""}`}
              </TD>
              <TD className="text-xs text-ink-muted">{r.requested_by ?? "—"}</TD>
              <TD className="text-xs text-ink-muted">{r.reason ?? ""}</TD>
              <TD>
                <Badge tone={STATUS_TONE[r.status]}>{r.status.replace("_", " ")}</Badge>
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </Card>
  );
}

/** The Lifecycle tab: the pipeline, a waiting approval, the history, the policy, and `children` (the agent's own status). */
export function LifecycleTab({ org, agent, children }: { org: string; agent: Agent; children?: ReactNode }) {
  const history = useQuery({ queryKey: ["agent-releases", org, agent.slug], queryFn: () => api.agentReleases(org, agent.slug) });
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <ChannelBadges agent={agent} />
        <span className="text-xs text-ink-muted">
          A revision never changes; production and testing each point at one. Saving a revision and putting it to work are two gestures.
        </span>
      </div>
      {agent.can?.release === false && (
        <Alert tone="warn" title="Read only for you">
          {agent.cannot_reason ?? "You may not release this agent."}
        </Alert>
      )}
      {history.error ? (
        <ErrorNote>{String(history.error)}</ErrorNote>
      ) : (
        <ReleasePipeline org={org} agent={agent} history={history.data ?? []} />
      )}
      <PendingRelease org={org} agent={agent} />
      <ReleaseHistory history={history.data ?? []} />
      <ReleasePolicyForm key={JSON.stringify(agent.release_policy)} org={org} agent={agent} />
      {children}
    </div>
  );
}

/** For the versions table and the history S40-12 builds: where a revision sits (re-exported). */
export { RevisionChannels };

/** A link from the overview: the lifecycle tab is where releases are managed. */
export function ManageReleases({ slug }: { slug: string }) {
  return (
    <Link href={`/agents/${slug}?tab=lifecycle`} className="text-sm underline">
      Manage releases
    </Link>
  );
}
