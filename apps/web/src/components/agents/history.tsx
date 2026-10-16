// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert, Badge, Button, Card, Drawer, Empty, ErrorNote, Field, Textarea } from "@/components/ui";
import { diffSpecs } from "@/lib/agent-diff";
import { canEdit, currentVersion, previousVersion, sortedVersions, versionOf } from "@/lib/agent-versions";
import { ApiError, api } from "@/lib/api";
import { relative } from "@/lib/format";
import type { Agent } from "@/lib/types";
import { RevisionChannels } from "./channels";
import { ChangeList } from "./spec-changes";

/**
 * An agent's history (S40-12, ADR 0076): a revision per line with its message, who and when, what it
 * is (current, restored from, pinned by), what changed since the one before, any two compared field by
 * field, and the way back: restoring appends a copy as the next revision and never rewrites one.
 */

/** The sentence a refused publication or restore deserves, from the problem's `code`. */
export function refusalSentence(cause: unknown): string {
  if (cause instanceof ApiError) {
    const { code, latest_version: latest, latest_author: author } = cause.problem;
    if (code === "stale_base") return `v${latest} was published${author ? ` by ${author}` : ""} since you started.`;
    if (code === "no_change") return `Nothing changed since v${latest}.`;
  }
  return cause instanceof Error ? cause.message : "Refused";
}

export const MAX_MESSAGE = 500;

/** Restore an older revision as the next one, with a message of one's own or the default. */
export function RestoreDialog({
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
  const client = useQueryClient();
  const router = useRouter();
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const next = currentVersion(agent) + 1;

  async function restore() {
    setError(null);
    setBusy(true);
    try {
      await api.restoreAgentVersion(org, agent.slug, version, {
        message: message.trim() || null,
        base_version: agent.latest_version,
      });
      await client.invalidateQueries({ queryKey: ["agent", org, agent.slug] });
      await client.invalidateQueries({ queryKey: ["agents", org] });
      onClose();
      router.push(`/agents/${agent.slug}?tab=history`);
    } catch (cause) {
      setError(refusalSentence(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Drawer open={open} onClose={onClose} title={`Restore v${version} as v${next}`}>
      <form
        className="space-y-4"
        aria-label="Restore"
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy) void restore();
        }}
      >
        <p className="text-sm text-ink-muted">
          This copies v{version} as a new revision, v{next}. Nothing is rewritten or deleted: v{version} and every
          other revision stay as they are, and projects keep the version they pinned.
        </p>
        <Field label="Message" hint={`Optional. Without one, the revision says "Restore v${version}".`}>
          {(props) => (
            <Textarea
              {...props}
              rows={3}
              maxLength={MAX_MESSAGE}
              value={message}
              placeholder={`Restore v${version}`}
              onChange={(e) => setMessage(e.target.value)}
            />
          )}
        </Field>
        {error && <ErrorNote>{error}</ErrorNote>}
        <div className="flex gap-2">
          <Button type="submit" tone="primary" disabled={busy}>
            {`Restore as v${next}`}
          </Button>
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
        </div>
      </form>
    </Drawer>
  );
}

/** Two revisions compared, field by field. */
export function CompareView({ agent, from, to }: { agent: Agent; from: number; to: number }) {
  const [older, newer] = from < to ? [from, to] : [to, from];
  const a = versionOf(agent, older);
  const b = versionOf(agent, newer);
  if (!a || !b) return <Empty title="Unknown revision">This agent has no v{!a ? older : newer}.</Empty>;
  return (
    <Card title={`Changes from v${older} to v${newer}`} data-testid="compare">
      <div className="space-y-3">
        <p className="text-xs text-ink-muted">
          {`v${newer}`}
          {b.message ? ` — “${b.message}”` : ""}
          {b.created_by ? ` · ${b.created_by}` : ""}
        </p>
        <ChangeList changes={diffSpecs(a.spec, b.spec)} empty={`v${older} and v${newer} say the same thing.`} />
      </div>
    </Card>
  );
}

export function History({ org, agent, compare }: { org: string; agent: Agent; compare: [number, number] | null }) {
  const router = useRouter();
  const versions = sortedVersions(agent);
  const current = currentVersion(agent);
  const [picked, setPicked] = useState<number[]>([]);
  const [restoring, setRestoring] = useState<number | null>(null);
  const may = canEdit(agent);
  if (versions.length === 0) return <Empty>No version</Empty>;
  const toggle = (n: number) => setPicked((now) => (now.includes(n) ? now.filter((x) => x !== n) : [...now.slice(-1), n]));
  const [low, high] = [...picked].sort((x, y) => x - y);
  const base = `/agents/${agent.slug}?tab=history`;
  return (
    <div className="space-y-4">
      {compare && <CompareView agent={agent} from={compare[0]} to={compare[1]} />}
      <Card title="History" padding="none" className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2 text-xs text-ink-muted">
          <span>Tick two revisions to compare them.</span>
          {picked.length === 2 && low !== undefined && high !== undefined && (
            <Button size="sm" onClick={() => router.push(`${base}&compare=${low}..${high}`)}>
              {`Compare v${low} and v${high}`}
            </Button>
          )}
        </div>
        <ol aria-label="Revisions" className="divide-y divide-line" data-testid="history">
          {versions.map((v) => {
            const before = previousVersion(agent, v.version);
            return (
              <li key={v.version} className="space-y-1.5 px-4 py-3 text-sm" data-testid={`revision-${v.version}`}>
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <label className="inline-flex items-center gap-2">
                    <input type="checkbox" checked={picked.includes(v.version)} onChange={() => toggle(v.version)} />
                    <span className="font-mono font-medium">v{v.version}</span>
                    <span className="sr-only">Compare</span>
                  </label>
                  {v.version === current && <Badge tone="ok">Current</Badge>}
                  <RevisionChannels channels={v.channels} />
                  {v.restored_from != null && <Badge tone="accent">{`Restored from v${v.restored_from}`}</Badge>}
                  {(v.pinned_by ?? []).length > 0 && <Badge tone="neutral">{`Pinned by ${(v.pinned_by ?? []).join(", ")}`}</Badge>}
                  <span className="text-xs text-ink-muted">
                    {v.created_by ?? "unknown author"} {v.created_at ? relative(v.created_at) : ""}
                  </span>
                </div>
                <p className={v.message ? "" : "text-ink-muted"}>
                  {v.message ?? "No message (published before revisions had one)."}
                </p>
                <div className="flex flex-wrap items-center gap-3 text-xs">
                  <Link href={`/agents/${agent.slug}?tab=definition&version=${v.version}`} className="underline">
                    Read<span className="sr-only"> v{v.version}</span>
                  </Link>
                  {before && (
                    <Link href={`${base}&compare=${before.version}..${v.version}`} className="underline">
                      What changed<span className="sr-only"> in v{v.version}</span>
                    </Link>
                  )}
                  {v.version !== current && (
                    <Button size="sm" disabled={!may} onClick={() => setRestoring(v.version)}>
                      {`Restore v${v.version}`}
                    </Button>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </Card>
      {!may && (
        <Alert tone="warn">
          <span className="font-medium">Restoring is not available to you. </span>
          {agent.cannot_reason ?? "You may not change this agent."}
        </Alert>
      )}
      {restoring !== null && (
        <RestoreDialog org={org} agent={agent} version={restoring} open onClose={() => setRestoring(null)} />
      )}
    </div>
  );
}
