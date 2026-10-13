// SPDX-License-Identifier: Apache-2.0
"use client";

import { Provenance } from "@/components/provenance";
import { Badge, Card } from "@/components/ui";
import type { ContextReduction } from "@/lib/context-reduction";
import { tokens } from "@/lib/format";

/**
 * What the run did to its context (ADR 0055): which reduction it was given, what actually ran, and
 * what headroom says it saved. What ran is observed; the savings are headroom's own count, inferred,
 * and the cost card stays the gateway's.
 */
export function ContextReductionCard({ reduction }: { reduction: ContextReduction | null }) {
  if (!reduction) return null;
  const { shellFilter, compression, savings } = reduction;
  return (
    <Card title="context reduction" action={<Provenance de="observed" />}>
      <ul className="space-y-2 text-sm" data-testid="context-reduction">
        {shellFilter && (
          <li data-testid="shell-filter">
            <span className="font-mono">{shellFilter.filter}</span> on shell output ·{" "}
            {shellFilter.applied === true ? (
              <span>applied by {shellFilter.backend}</span>
            ) : shellFilter.applied === false ? (
              <Badge tone="warn">not applied: {shellFilter.backend} has no hook for it</Badge>
            ) : (
              <span className="text-ink-muted">the run ended before its agent started</span>
            )}
          </li>
        )}
        {compression && (
          <li data-testid="compression">
            <span className="font-mono">headroom</span> compression ·{" "}
            {compression.state === "ran" ? (
              <span>
                ran{compression.version ? ` (${compression.version})` : ""}
                {compression.profile ? `, profile ${compression.profile}` : ""}
              </span>
            ) : compression.state === "unavailable" ? (
              <>
                <Badge tone={compression.failed ? "danger" : "warn"}>
                  {compression.failed ? "unavailable, stage failed" : "unavailable, went to the gateway directly"}
                </Badge>
                <span className="block font-mono text-xs text-ink-muted">{compression.reason}</span>
              </>
            ) : (
              <span className="text-ink-muted">the run ended before it started</span>
            )}
          </li>
        )}
      </ul>
      {savings && (
        <div className="mt-3 border-t border-line pt-3" data-testid="context-savings">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm">
              {tokens(savings.tokensRemoved)} tokens removed of {tokens(savings.tokensBefore)}
              {savings.tokensBefore > 0 ? ` (${Math.round((savings.tokensRemoved / savings.tokensBefore) * 100)} %)` : ""}
            </p>
            <Provenance de="inferred" />
          </div>
          <p className="text-xs text-ink-muted">
            {savings.requestsCompressed} of {savings.requests} model requests compressed · {savings.retrievals}{" "}
            original(s) asked back · headroom&apos;s own count, before the gateway: the cost is the gateway&apos;s
          </p>
        </div>
      )}
    </Card>
  );
}
