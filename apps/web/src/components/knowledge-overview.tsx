// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { Archive, Bot, CircleAlert, CircleCheck, Inbox, LoaderCircle, Lock, Users, type LucideIcon } from "lucide-react";
import { Badge, Card, Select } from "@/components/ui";
import { cn } from "@/lib/cn";
import {
  SERVING_MODES,
  baseHealth,
  indexLine,
  readersLabel,
  servedRunsLabel,
  type BaseHealth,
  type HealthTone,
} from "@/lib/knowledge";
import type { KnowledgeBase } from "@/lib/types";

/** A banner's surface by tone: the console's status tokens, so dark mode follows. */
const SURFACE: Record<HealthTone, string> = {
  ok: "border-succeeded-line bg-succeeded-soft",
  running: "border-running-line bg-running-soft",
  danger: "border-failed-line bg-failed-soft",
  warn: "border-waiting-line bg-waiting-soft",
  neutral: "border-neutral-line bg-neutral-soft",
};
const INK: Record<HealthTone, string> = {
  ok: "text-succeeded-ink",
  running: "text-running-ink",
  danger: "text-failed-ink",
  warn: "text-waiting-ink",
  neutral: "text-neutral-ink",
};
const ICON: Record<BaseHealth["state"], LucideIcon> = {
  ready: CircleCheck,
  converting: LoaderCircle,
  attention: CircleAlert,
  empty: Inbox,
  archived: Archive,
};

/**
 * Whether the base can be used now, before anything else on the page: ready, still converting or
 * syncing (with how far), something to fix, or empty — and what that means for the runs.
 */
export function KnowledgeStatusBanner({ base }: { base: KnowledgeBase }) {
  const health = baseHealth(base);
  const Icon = ICON[health.state];
  const share = health.progress && health.progress.total ? Math.round((health.progress.done / health.progress.total) * 100) : 0;
  return (
    <section
      aria-label="Status"
      data-testid="knowledge-status"
      data-state={health.state}
      className={cn("flex items-start gap-3 rounded-md border px-4 py-3", SURFACE[health.tone])}
    >
      <Icon
        aria-hidden
        className={cn("mt-0.5 size-5 shrink-0", INK[health.tone], health.state === "converting" && "motion-safe:animate-spin")}
        strokeWidth={1.75}
      />
      <div className="min-w-0 flex-1 space-y-1">
        <p className={cn("text-sm font-semibold", INK[health.tone])} data-testid="knowledge-status-title">
          {health.title}
        </p>
        <p className="text-sm text-ink">{health.detail}</p>
        {health.progress && (
          <div
            role="progressbar"
            aria-label="Documents ready"
            aria-valuemin={0}
            aria-valuemax={health.progress.total}
            aria-valuenow={health.progress.done}
            className="h-1.5 w-full max-w-md overflow-hidden rounded-full bg-surface"
          >
            <div className="h-full rounded-full bg-running transition-[width] duration-500" style={{ width: `${share}%` }} />
          </div>
        )}
      </div>
    </section>
  );
}

/**
 * Who is served the base and who reads it: the agents whose current revision names it (a run gets a
 * base only through its agent), the projects whose runs may be served it, and the people who read it.
 */
export function KnowledgeUsedBy({ base }: { base: KnowledgeBase }) {
  const agents = base.used_by ?? [];
  const readers = readersLabel(base);
  return (
    <Card title="Who uses it" icon={Users}>
      <dl className="space-y-4 text-sm" data-testid="knowledge-used-by">
        <div className="space-y-1">
          <dt className="text-xs font-medium text-ink-muted">Agents that read it</dt>
          <dd>
            {base.used_by === undefined || base.used_by === null ? (
              <span className="text-ink-muted">Not reported by this server.</span>
            ) : agents.length === 0 ? (
              <span data-testid="knowledge-no-agent">
                <Badge tone="warn">No agent reads it yet</Badge>{" "}
                <span className="text-ink-muted">Name it in an agent&apos;s knowledge to use it:</span>{" "}
                <Link href="/agents" className="underline">
                  open the agents
                </Link>
              </span>
            ) : (
              <ul className="flex flex-wrap gap-2" data-testid="knowledge-agents">
                {agents.map((agent) => (
                  <li key={agent.agent}>
                    <Link
                      href={`/agents/${agent.agent}`}
                      className="inline-flex items-center gap-1.5 rounded-full border border-agent-line bg-agent-soft px-2 py-0.5 text-xs font-medium text-agent-ink hover:underline"
                    >
                      <Bot aria-hidden className="size-3.5" strokeWidth={1.75} />
                      {agent.display_name}
                      <span className="font-mono text-[11px] opacity-80">v{agent.version}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-1 text-xs text-ink-muted">
              A run is served this base only when its agent names it. A direct conversation with an agent is not.
            </p>
          </dd>
        </div>
        <div className="space-y-1">
          <dt className="text-xs font-medium text-ink-muted">Runs it may be served to</dt>
          <dd data-testid="knowledge-served-runs">{servedRunsLabel(base)}</dd>
        </div>
        <div className="space-y-1">
          <dt className="text-xs font-medium text-ink-muted">People who read it</dt>
          <dd className="flex flex-wrap items-start gap-2" data-testid="knowledge-readers">
            {readers.open ? (
              <Badge tone="ok">Open</Badge>
            ) : (
              <Badge tone="warn" dot={false}>
                <Lock aria-hidden className="size-3" strokeWidth={2} />
                Restricted
              </Badge>
            )}
            <span className="min-w-0 flex-1">{readers.text}</span>
          </dd>
        </div>
      </dl>
    </Card>
  );
}

/**
 * How the base reaches a run: copied as files, or searched as an index — each said as what it does
 * and when it suits, never as a limit — and, for an index, what it holds.
 */
export function KnowledgeServing({
  base,
  canEdit,
  why,
  onChange,
  children,
}: {
  base: KnowledgeBase;
  canEdit: boolean;
  why?: string;
  onChange: (mode: "files" | "index") => void;
  /** What follows in the card: the principles skill. */
  children?: React.ReactNode;
}) {
  const mode = base.serving_mode;
  return (
    <Card title="How runs read it">
      <div className="space-y-4 text-sm">
        <div className="space-y-2" data-testid="knowledge-serving">
          <label className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-ink-muted">Serving mode</span>
            <Select
              aria-label="serving mode"
              className="w-auto"
              value={mode}
              disabled={!canEdit}
              title={canEdit ? undefined : why}
              onChange={(e) => onChange(e.target.value as "files" | "index")}
            >
              <option value="files">{SERVING_MODES.files.label}</option>
              <option value="index">{SERVING_MODES.index.label}</option>
            </Select>
          </label>
          <ul className="space-y-1.5">
            {(["files", "index"] as const).map((option) => (
              <li
                key={option}
                data-testid={`serving-${option}`}
                className={cn(
                  "rounded border px-3 py-2",
                  option === mode ? "border-accent-line bg-accent-soft" : "border-line text-ink-muted",
                )}
              >
                <span className={cn("font-medium", option === mode ? "text-ink" : undefined)}>
                  {SERVING_MODES[option].label}
                  {option === mode && <span className="font-normal text-ink-muted"> · current</span>}
                </span>
                <span className="block text-xs">{SERVING_MODES[option].text}</span>
              </li>
            ))}
          </ul>
          {mode === "index" && (
            <p className="text-xs text-ink-muted" data-testid="knowledge-index-line">
              Index: {indexLine(base.index)}
            </p>
          )}
        </div>
        {children}
      </div>
    </Card>
  );
}
