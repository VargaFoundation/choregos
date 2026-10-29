// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge, Card, EtatDeLecture, Label } from "@/components/ui";
import { api } from "@/lib/api";
import { percent, shortDate, toSentenceCase } from "@/lib/format";
import type { CrossBackendArm } from "@/lib/types";

/**
 * Does a review by another coding-agent backend than the implementer's catch more than a review by
 * the same one (S13-03)? The API counts, for each arm, the reviews that found something (a finding
 * filed, or an outcome other than `done`) and gives its verdict once each arm has five reviews. The
 * card says it next to DORA (S47-17), with the policy's `review.cross_backend` when it requires one.
 * The API words its verdict in lowercase; the card writes it in sentence case (ADR 0048).
 */
export function CrossBackendCard({ slug }: { slug: string }) {
  const report = useQuery({ queryKey: ["cross-backend", slug], queryFn: () => api.crossBackend(slug) });
  const data = report.data;
  return (
    <Card
      eyebrow="Review"
      title="Cross-backend review"
      action={data?.cross_backend_required ? <Badge tone="accent">required by the policy</Badge> : undefined}
    >
      {data === undefined ? (
        <EtatDeLecture lecture={report} quoi="the cross-backend review" />
      ) : (
        <div className="space-y-4" data-testid="cross-backend">
          <div>
            <p className="text-sm font-semibold text-ink">{toSentenceCase(data.verdict)}</p>
            {data.detail && <p className="mt-1 text-sm text-ink-muted">{toSentenceCase(data.detail)}</p>}
            <p className="mt-1 text-xs text-ink-muted">Since {shortDate(data.since)}</p>
          </div>
          <div className="grid border-t border-line sm:grid-cols-2">
            <Arm label="Reviewed by another backend" arm={data.other_backend} />
            <Arm label="Reviewed by the same backend" arm={data.same_backend} />
          </div>
        </div>
      )}
    </Card>
  );
}

function Arm({ label, arm }: { label: string; arm?: CrossBackendArm }) {
  const reviews = arm?.reviews ?? 0;
  const caught = arm?.caught ?? 0;
  const backends = arm?.backends ?? [];
  return (
    <div role="group" aria-label={label} className="border-line p-5 sm:[&:nth-child(2)]:border-l">
      <Label>{label}</Label>
      <p className="mt-2 font-display text-2xl leading-none font-bold tracking-tight tabular-nums">
        {percent(arm?.catch_rate)}
      </p>
      <p className="mt-3 text-xs text-ink-muted">
        {caught} caught out of {reviews} review{reviews === 1 ? "" : "s"}
      </p>
      <p className="mt-1 text-xs text-ink-muted">
        {backends.length ? (
          <>
            Backends: <span className="font-mono">{backends.join(", ")}</span>
          </>
        ) : (
          "No review yet"
        )}
      </p>
    </div>
  );
}
