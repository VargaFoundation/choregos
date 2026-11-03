// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { DecisionBar } from "@/components/decision-bar";
import { verdictsDe } from "@/components/garanties";
import { LiveLog } from "@/components/live-log";
import { PlanDeLAgent } from "@/components/plan-de-l-agent";
import { Provenance } from "@/components/provenance";
import { Empty, LEGENDE, PhraseDuMoteur } from "@/components/ui";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { duration, relative, shortDate, usd } from "@/lib/format";
import { agentPlan } from "@/lib/agent-plan";
import { evidenceProvenance } from "@/lib/provenance";
import { useEventStream } from "@/lib/sse";
import type { JourneyStep, ProcessStep, RunEventDto } from "@/lib/types";
import { type Step, type StepState, outputsOf } from "./model";

const KIND_LABEL: Record<Step["kind"], string> = { agent: "agent", human: "person", platform: "platform", train: "release train", decision: "decision" };
const RUNNING = new Set(["queued", "running", "waiting", "pending_approval"]);

/**
 * The panel of a journey step (S22-02): what it does, then each of its attempts. For an agent,
 * what it returned, its evidence, its gates, what it touched, its diff and its log, live while it
 * runs. For a person, the request, who decided and why; and, if it is still awaited, what is
 * needed to decide right here.
 */
export function StepPanel({
  step,
  state,
  process,
  slug,
  itemId,
  onClose,
  onDecided,
  decisionAnchor,
}: {
  step: Step;
  state: StepState;
  process?: ProcessStep;
  slug: string;
  itemId: string;
  onClose: () => void;
  onDecided?: () => void;
  decisionAnchor?: string;
}) {
  const attempts = state.attempts;
  const [chosen, setChosen] = useState<string | null>(null);
  const attempt = attempts.find((t) => t.id === chosen) ?? attempts.at(-1);
  return (
    <aside
      className="raised min-w-0 self-start border border-line p-4 xl:sticky xl:top-16"
      aria-label={`${step.label}: attempts, actions and log`}
      data-testid="panneau-parcours"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className={LEGENDE}>
            {KIND_LABEL[step.kind]}
            {step.actor && step.kind !== "platform" ? ` · ${step.actor}` : ""}
          </p>
          <h3 className="text-lg font-semibold text-ink">
            {step.label} <span className="font-normal text-ink-muted">{step.action}</span>
          </h3>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="px-2 py-1 text-sm text-ink-muted hover:text-ink pointer-coarse:min-h-11"
          aria-label="Close the panel"
        >
          ✕
        </button>
      </div>

      {/* The step's technical name and its checks are there for who needs them, folded (S55-05). */}
      {
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-ink-muted">What this step does</summary>
          <p className="mt-1 text-xs text-ink-muted">
            Transition <span className="font-mono">{step.id}</span>
          </p>
          {step.sentence && (
            <p className="mt-1 text-ink-muted">
              <PhraseDuMoteur texte={step.sentence} />
            </p>
          )}
          {outputsOf(step) && (
            <p className="mt-1 text-xs text-ink-muted" data-testid="sorties-de-l-etape">
              Produces {outputsOf(step)!.slice(2)}
            </p>
          )}
          {(process?.gates ?? []).length > 0 && (
            <ul className="mt-1 space-y-0.5 text-xs text-ink-muted">
              {process!.gates!.map((g) => (
                <li key={g.name}>
                  <span className="font-mono">{g.name}</span> — {g.summary}
                </li>
              ))}
            </ul>
          )}
        </details>
      }

      {attempts.length === 0 ? (
        <p className="mt-4 text-sm text-ink-muted">
          {state.status === "en_cours"
            ? "The platform is on it: this step has no agent run of its own."
            : state.status === "fait"
              ? "Done by the platform, without an agent run."
              : "Not reached yet."}
        </p>
      ) : (
        <>
          <div className="mt-4 flex flex-wrap gap-1.5" role="tablist" aria-label="Attempts">
            {attempts.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={t.id === attempt?.id}
                data-testid={`tentative-${t.id}`}
                onClick={() => setChosen(t.id)}
                className={cn(
                  "rounded-full border px-2.5 py-0.5 text-xs pointer-coarse:min-h-11",
                  t.id === attempt?.id
                    ? "border-accent-line bg-accent-soft text-accent-strong"
                    : "border-line text-ink-muted hover:text-ink",
                )}
              >
                Attempt {t.attempt} · {t.status.replace(/_/g, " ")}
              </button>
            ))}
          </div>
          {attempt && (
            <div className="mt-3" role="tabpanel">
              {attempt.kind === "agent" ? (
                <AgentAttempt attempt={attempt} slug={slug} />
              ) : attempt.kind === "human" ? (
                <HumanAttempt attempt={attempt} itemId={itemId} onDecided={onDecided} decisionAnchor={decisionAnchor} />
              ) : (
                <ActionAttempt attempt={attempt} slug={slug} />
              )}
            </div>
          )}
        </>
      )}
    </aside>
  );
}

function Facts({ attempt }: { attempt: JourneyStep }) {
  const seconds = attempt.started_at && attempt.ended_at ? (Date.parse(attempt.ended_at) - Date.parse(attempt.started_at)) / 1000 : null;
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-xs">
      <dt className="text-ink-muted">Status</dt>
      <dd className={cn(attempt.status === "failed" || attempt.status === "rejected" ? "text-failed-ink" : "text-ink")}>
        {attempt.status.replace(/_/g, " ")}
      </dd>
      <dt className="text-ink-muted">Started</dt>
      <dd title={shortDate(attempt.started_at)}>{relative(attempt.started_at)}</dd>
      {attempt.ended_at && (
        <>
          <dt className="text-ink-muted">Took</dt>
          <dd>{duration(seconds)}</dd>
        </>
      )}
      {attempt.model && (
        <>
          <dt className="text-ink-muted">Model</dt>
          <dd className="font-mono">{attempt.model}</dd>
        </>
      )}
      {attempt.kind === "agent" && (
        <>
          <dt className="text-ink-muted">Cost</dt>
          <dd>{usd(attempt.cost_usd)}</dd>
        </>
      )}
      {attempt.decided_by && (
        <>
          <dt className="text-ink-muted">Decided by</dt>
          <dd>{attempt.decided_by}</dd>
        </>
      )}
      {attempt.due_at && !attempt.ended_at && (
        <>
          <dt className="text-ink-muted">Due</dt>
          <dd title={shortDate(attempt.due_at)}>{relative(attempt.due_at)}</dd>
        </>
      )}
    </dl>
  );
}

function EvidenceList({ evidence }: { evidence: Record<string, unknown> }) {
  const factEntries = Object.entries((evidence.facts ?? {}) as Record<string, unknown>);
  // [label, value, contract field]: the field says, through `measured`, who established it (ADR 0045).
  const rows: [string, string, string][] = factEntries.length
    ? factEntries.map(([k, v]) => [k.replace(/_/g, " "), typeof v === "boolean" ? (v ? "✓" : "✗") : String(v), `facts.${k}`])
    : [
        ...(typeof evidence.tests_run === "number"
          ? [
              [
                "tests",
                `${Number(evidence.tests_failed ?? 0) > 0 ? "✗" : "✓"} ${evidence.tests_run} run, ${evidence.tests_failed ?? 0} failed`,
                "tests_run",
              ] as [string, string, string],
            ]
          : []),
        ...(["lint", "typecheck", "security_scan"] as const)
          .filter((k) => evidence[k])
          .map((k) => [k.replace(/_/g, " "), String(evidence[k]), k] as [string, string, string]),
        ...(typeof evidence.coverage_delta === "number"
          ? [
              [
                "coverage",
                `${evidence.coverage_delta > 0 ? "+" : ""}${evidence.coverage_delta}`,
                "coverage_delta",
              ] as [string, string, string],
            ]
          : []),
      ];
  if (rows.length === 0) return null;
  const measured = Array.isArray(evidence.measured) ? { measured: evidence.measured.map(String) } : undefined;
  return (
    <ul className="flex flex-wrap gap-1.5 text-xs" aria-label="Evidence">
      {rows.map(([name, value, key]) => (
        <li key={name} className="flex items-baseline gap-1.5 border border-line px-1.5 py-0.5">
          <span className="text-ink-muted">{name}</span> {value}
          <Provenance de={evidenceProvenance(measured, key)} />
        </li>
      ))}
    </ul>
  );
}

function AgentAttempt({ attempt, slug }: { attempt: JourneyStep; slug: string }) {
  const [view, setView] = useState<"actions" | "log">("actions");
  const running = RUNNING.has(attempt.status) && !attempt.ended_at;
  const stored = useQuery({ queryKey: ["run-events", attempt.id], queryFn: () => api.runEvents(attempt.id) });
  const live = useEventStream<RunEventDto>({
    path: `/runs/${attempt.id}/events`,
    enabled: running,
    mockEvents: () => import("@/mocks/data").then((fixtures) => fixtures.runEvents),
  });
  const events = dedupe([...(stored.data ?? []), ...live.events]);
  return (
    <div className="space-y-3">
      <Facts attempt={attempt} />
      {attempt.summary && (
        <p className="text-sm text-ink">
          {attempt.summary} <Provenance de="declared" />
        </p>
      )}
      {attempt.verdict && (
        <p className={cn("text-sm font-medium", attempt.verdict === "approve" ? "text-succeeded-ink" : "text-retrying-ink")}>
          Verdict: {attempt.verdict.replace(/_/g, " ")} <Provenance de="declared" />
        </p>
      )}
      <EvidenceList evidence={(attempt.evidence ?? {}) as Record<string, unknown>} />
      <div className="flex gap-4 border-b border-line text-sm" role="tablist" aria-label="What the agent did">
        {(
          [
            ["actions", "What it did"],
            ["log", running ? "Log · live" : "Log"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={view === key}
            onClick={() => setView(key)}
            className={cn(
              "-mb-px border-b-2 py-1.5",
              view === key ? "border-accent font-medium text-ink" : "border-transparent text-ink-muted hover:text-ink",
            )}
          >
            {label}
          </button>
        ))}
      </div>
      {view === "actions" ? <WhatItDid runId={attempt.id} events={events} /> : <LiveLog events={events} height={320} />}
      <Link href={`/p/${slug}/runs/${attempt.id}`} className="inline-block text-sm">
        Open the full run →
      </Link>
    </div>
  );
}

/** What the agent did: its gates, what it touched (refusals first), the files it changed. */
function WhatItDid({ runId, events }: { runId: string; events: RunEventDto[] }) {
  const access = useQuery({ queryKey: ["run-access", runId], queryFn: () => api.runAccess(runId) });
  const diff = useQuery({ queryKey: ["run-diff", runId], queryFn: () => api.runDiff(runId) });
  const gates = verdictsDe(events);
  const rows = access.data?.access ?? [];
  const files = diff.data?.files ?? [];
  const plan = agentPlan(events);
  return (
    <div className="space-y-3 text-sm" data-testid="ce-qu-il-a-fait">
      {plan && (
        <section>
          <h4 className={cn(LEGENDE, "mb-1")}>Agent plan</h4>
          <PlanDeLAgent plan={plan} />
        </section>
      )}
      <section>
        <h4 className={LEGENDE}>Gates</h4>
        {gates.length === 0 ? (
          <p className="text-xs text-ink-muted">No gate evaluated on this run.</p>
        ) : (
          <ul className="space-y-0.5">
            {gates.map((g, i) => (
              <li key={`${g.name}-${i}`} className="flex gap-2 text-xs">
                <span
                  className={g.pending ? "text-waiting-ink" : g.passed ? "text-succeeded-ink" : "text-failed-ink"}
                  aria-label={g.pending ? "Pending" : g.passed ? "Passed" : "Refused"}
                >
                  {g.pending ? "…" : g.passed ? "✓" : "✗"}
                </span>
                <span>
                  <span className="font-mono">{g.name}</span>
                  {g.detail && <span className="text-ink-muted"> — {g.detail}</span>}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section>
        <h4 className={LEGENDE}>
          Access {access.data ? `· ${access.data.events} events, ${access.data.refusals} refused` : ""}
        </h4>
        {rows.length === 0 ? (
          <p className="text-xs text-ink-muted">No access recorded.</p>
        ) : (
          <ul className="space-y-0.5 text-xs">
            {[...rows]
              .sort((a, b) => b.refusals - a.refusals)
              .map((l) => (
                <li key={`${l.nature}:${l.target}`} className={cn("flex gap-2", l.refusals > 0 && "text-failed-ink")}>
                  <span className="w-14 shrink-0 text-ink-muted">{l.nature}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block break-all font-mono">{l.target}</span>
                    {l.refusals > 0 && <span className="block">Refused{l.reasons?.length ? `: ${l.reasons.join(" · ")}` : ""}</span>}
                  </span>
                </li>
              ))}
          </ul>
        )}
      </section>
      <section>
        <h4 className={LEGENDE}>
          Files changed {diff.data ? `· +${diff.data.additions} −${diff.data.deletions}` : ""}
        </h4>
        {files.length === 0 ? (
          <Empty>No file changed</Empty>
        ) : (
          <ul className="space-y-0.5 text-xs">
            {files.map((f) => (
              <li key={f.path} className="flex gap-2">
                <span className="min-w-0 flex-1 break-all font-mono">{f.path}</span>
                <span className="text-succeeded-ink">+{f.additions}</span>
                <span className="text-failed-ink">−{f.deletions}</span>
                {f.in_scope === false && <span className="text-failed-ink">Out of scope</span>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function HumanAttempt({
  attempt,
  itemId,
  onDecided,
  decisionAnchor,
}: {
  attempt: JourneyStep;
  itemId: string;
  onDecided?: () => void;
  decisionAnchor?: string;
}) {
  const awaited = attempt.status === "waiting";
  return (
    <div className="space-y-3">
      <Facts attempt={attempt} />
      {attempt.summary && <p className="text-sm text-ink">{attempt.summary}</p>}
      {awaited && decisionAnchor ? (
        // One place to decide (S55-05): the page shows the decision with what the agent proposes.
        <p className="border border-line border-l-2 border-l-waiting p-3 text-sm" data-testid="decision-elsewhere">
          This decision is taken at the top of the page, with what the agent proposes.{" "}
          <a href={`#${decisionAnchor}`}>Go to the decision</a>
        </p>
      ) : awaited ? (
        <div className="border border-line border-l-2 border-l-waiting p-3">
          <p className={cn(LEGENDE, "mb-2")}>Decide here</p>
          <DecisionBar itemId={itemId} kind={attempt.role ?? "approval"} onDone={onDecided} />
        </div>
      ) : (
        <p className={cn("text-sm", attempt.status === "rejected" ? "text-failed-ink" : "text-succeeded-ink")}>
          {attempt.status.replace(/_/g, " ")}
          {attempt.decided_by ? ` by ${attempt.decided_by}` : ""}
          {attempt.ended_at ? `, ${relative(attempt.ended_at)}` : ""}
        </p>
      )}
    </div>
  );
}

function ActionAttempt({ attempt, slug }: { attempt: JourneyStep; slug: string }) {
  const train = attempt.actor === "release_train";
  return (
    <div className="space-y-3">
      <Facts attempt={attempt} />
      {attempt.summary && <p className="text-sm text-ink">{attempt.summary}</p>}
      <Link href={train ? `/p/${slug}/trains` : `/p/${slug}/actions/${attempt.id}`} className="inline-block text-sm">
        {train ? "Open the release trains →" : "Open the governed action →"}
      </Link>
    </div>
  );
}

function dedupe(events: RunEventDto[]): RunEventDto[] {
  const seen = new Map<number, RunEventDto>();
  for (const e of events) seen.set(e.seq, e);
  return [...seen.values()].sort((a, b) => a.seq - b.seq);
}
