// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { Alert, Badge, Card } from "@/components/ui";
import { remoteReasonText, shortDigest } from "@/lib/remote-run";
import type { A2aRuntime, AgentSpec } from "@/lib/types";

/** The `a2a` runtime of a version, if it has one. */
export function a2aOf(spec: AgentSpec | undefined): A2aRuntime | undefined {
  return spec?.runtime?.kind === "a2a" ? (spec.runtime as A2aRuntime) : undefined;
}

/** What a remote agent's current version says: where it is, which card was pinned, how it is paid. */
export function RemoteDefinition({ spec, version }: { spec: AgentSpec; version: number }) {
  const runtime = a2aOf(spec);
  if (!runtime) return null;
  const pinned = runtime.pinned_card;
  const cost = runtime.declared_cost;
  const declared = [
    cost?.per_task_usd != null ? `${cost.per_task_usd} USD per task` : null,
    cost?.per_minute_usd != null ? `${cost.per_minute_usd} USD per minute` : null,
  ].filter(Boolean);
  return (
    <Card title={`Version ${version}`} action={<Badge tone="accent">Remote · A2A</Badge>} data-testid="remote-definition">
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
        <dt className="text-ink-muted">Endpoint</dt>
        <dd className="break-all font-mono" data-testid="remote-endpoint">
          {pinned?.url ?? runtime.card_url}
        </dd>
        <dt className="text-ink-muted">Card</dt>
        <dd className="break-all font-mono">{runtime.card_url}</dd>
        <dt className="text-ink-muted">Pinned digest</dt>
        <dd className="font-mono" title={pinned?.digest} data-testid="remote-digest">
          {shortDigest(pinned?.digest)} · A2A {pinned?.protocol_version ?? "?"}
        </dd>
        <dt className="text-ink-muted">Skill</dt>
        <dd className="font-mono">{runtime.skill ?? "any"}</dd>
        <dt className="text-ink-muted">Credential</dt>
        <dd className="font-mono">{runtime.credential ?? "none"}</dd>
        <dt className="text-ink-muted">Time limit</dt>
        <dd>{runtime.timeout_minutes ?? 30} min</dd>
        <dt className="text-ink-muted">Declared cost</dt>
        <dd data-testid="remote-cost">{declared.length > 0 ? declared.join(" + ") : "Nothing declared: booked as unmeasured"}</dd>
      </dl>
      {spec.instructions && (
        <pre aria-label="Instructions" className="mt-3 whitespace-pre-wrap break-words rounded bg-surface-muted p-3 text-xs">
          {spec.instructions}
        </pre>
      )}
    </Card>
  );
}

/** Shown on the agent's overview when its latest run stopped on a card that moved (ADR 0062 §4). */
export function CardChangedWarning({ slug }: { slug: string }) {
  return (
    <div data-testid="card-changed">
      <Alert tone="warn" title="The agent's card changed">
        {remoteReasonText("remote_card_changed")}{" "}
        <Link href={`/agents/${slug}?tab=definition`} className="underline">
          Read the card again and publish a new version
        </Link>
        .
      </Alert>
    </div>
  );
}
