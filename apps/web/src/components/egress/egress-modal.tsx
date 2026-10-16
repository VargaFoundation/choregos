// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { Alert, Badge, Button, Code, Drawer } from "@/components/ui";
import {
  FAMILY_LABEL,
  approversOf,
  ceilingLine,
  hostLabel,
  needsPlatformChange,
  orgLine,
} from "@/lib/egress";
import type { EgressVerdict } from "@/lib/types";

/** What Choregos can and cannot do about the cluster's network, said plainly (ADR 0066 §7). */
export const HONEST_SENTENCE =
  "Choregos cannot change the cluster's network policy: it holds no credential to the platform's repository and must not hold one.";

export interface EgressModalProps {
  open: boolean;
  verdicts: EgressVerdict[];
  /** What the person was doing: "Install the Notion MCP server". Names the primary action. */
  gesture?: string;
  busy?: boolean;
  error?: string | null;
  /** An administrator: open the hosts and carry on, in one call. */
  onAllow: () => void;
  /** Anyone else: file the request. */
  onRequest: () => void;
  onClose: () => void;
}

/**
 * The network access modal: each host the add needs with its two states (this organisation, the
 * platform's network), who can allow it, the change the platform team must make when the cluster's
 * network also needs one, and the one gesture that fits the person looking.
 */
export function EgressModal({ open, verdicts, gesture, busy, error, onAllow, onRequest, onClose }: EgressModalProps) {
  const canAllow = verdicts.length > 0 && verdicts.every((v) => v.can_allow);
  const approvers = approversOf(verdicts);
  const changes = verdicts.filter((v) => v.platform_change.needed && v.platform_change.snippet);
  const platform = needsPlatformChange(verdicts);
  const hosts = verdicts.map(hostLabel).join(", ");
  return (
    <Drawer open={open} onClose={onClose} title="Network access needed">
      <div className="space-y-4 text-sm" data-testid="egress-modal">
        <p className="text-ink-muted">
          {gesture ? <>To {gesture.charAt(0).toLowerCase() + gesture.slice(1)}, </> : <>To do this, </>}
          Choregos must call the {verdicts.length === 1 ? "host" : "hosts"} below from its own pods. An
          organisation allows each one first.
        </p>
        <ul className="space-y-3" data-testid="egress-hosts">
          {verdicts.map((v) => (
            <li key={`${v.host}:${v.port}:${v.used_by}`} className="space-y-1 rounded-md border border-line p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Code>{hostLabel(v)}</Code>
                <Badge tone="neutral">called by {FAMILY_LABEL[v.used_by]}</Badge>
              </div>
              <dl className="grid gap-x-3 gap-y-1 text-xs sm:grid-cols-[auto_1fr]">
                <dt className="text-ink-muted">This organisation:</dt>
                <dd data-testid={`org-${v.host}`}>{orgLine(v)}</dd>
                <dt className="text-ink-muted">The platform&apos;s network:</dt>
                <dd data-testid={`ceiling-${v.host}`}>{ceilingLine(v)}</dd>
              </dl>
            </li>
          ))}
        </ul>

        {!canAllow && (
          <div className="space-y-1" data-testid="egress-approvers">
            <p className="font-medium">Only an administrator can allow this.</p>
            {approvers.length > 0 ? (
              <ul className="list-disc pl-5 text-ink-muted">
                {approvers.map((a) => (
                  <li key={a.email}>
                    {a.display_name} · {a.email}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-ink-muted">No administrator is listed: ask the person who runs this organisation.</p>
            )}
            <p className="text-ink-muted">
              Ask them below: the request waits in their inbox, and you retry once it is approved.
            </p>
          </div>
        )}

        {platform && (
          <section className="space-y-2" data-testid="egress-platform">
            <h3 className="font-medium">Platform change</h3>
            <Alert tone="warn">{HONEST_SENTENCE} Until the platform team applies this change, calls to {hosts} will be refused by the network.</Alert>
            {changes.map((v) => (
              <PlatformChangeBlock key={`${v.host}:${v.used_by}`} verdict={v} />
            ))}
            {changes.length === 0 && (
              <p className="text-ink-muted">This deployment gives no template for the change: ask the platform team to open {hosts}.</p>
            )}
          </section>
        )}
        {!platform && <p className="text-xs text-ink-muted">{HONEST_SENTENCE}</p>}

        {error && <Alert tone="danger">{error}</Alert>}
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          {canAllow ? (
            <Button type="button" tone="primary" disabled={busy} onClick={onAllow}>
              Allow and continue
            </Button>
          ) : (
            <Button type="button" tone="primary" disabled={busy} onClick={onRequest}>
              Ask an administrator
            </Button>
          )}
        </div>
      </div>
    </Drawer>
  );
}

export function PlatformChangeBlock({ verdict }: { verdict: EgressVerdict }) {
  const [copied, setCopied] = useState(false);
  const change = verdict.platform_change;
  return (
    <div className="space-y-1">
      {change.owner && (
        <p className="text-xs text-ink-muted">
          Applied by {change.owner} — for <Code>{hostLabel(verdict)}</Code> ({FAMILY_LABEL[verdict.used_by]}):
        </p>
      )}
      <pre className="overflow-x-auto rounded-md border border-line bg-surface-sunken p-2 text-xs" data-testid="egress-snippet">
        {change.snippet}
      </pre>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          className="text-xs underline"
          onClick={() => {
            void navigator.clipboard?.writeText(change.snippet ?? "").then(() => setCopied(true));
          }}
        >
          {copied ? "Copied" : "Copy the change"}
        </button>
        {change.issue_url && (
          <a className="text-xs underline" href={change.issue_url} target="_blank" rel="noreferrer">
            Open a prefilled issue
          </a>
        )}
      </div>
    </div>
  );
}
