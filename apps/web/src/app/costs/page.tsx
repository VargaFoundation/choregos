// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { GrapheDesCouts } from "@/components/graphe-des-couts";
import { BreakdownCard, csvUrl, fetchReport, reportKey, type Scope } from "@/components/spend/breakdown";
import { MonthPanel } from "@/components/spend/month";
import { Card, Empty, ErrorNote, EtatDeLecture, Eyebrow, Heading, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { type Currency, money, monthStart } from "@/lib/spend";

const PERIODES = [
  { id: "month", label: "This month" },
  { id: "30", label: "Last 30 days" },
  { id: "90", label: "Last 90 days" },
] as const;
type Periode = (typeof PERIODES)[number]["id"];

/** The first day of the period, `YYYY-MM-DD` (UTC, like the ledger's days). */
function depuis(periode: Periode, now: Date): string {
  if (periode === "month") return monthStart(now);
  return new Date(now.getTime() - Number(periode) * 86_400_000).toISOString().slice(0, 10);
}

/**
 * Where the money goes (S29-03): spend over time, this month against its budget with a forecast,
 * the agents, workflows and work items that cost the most, models and tools apart, and the CSV of
 * each breakdown. The organisation by default; `?project=<slug>` narrows it to one project.
 */
function Spend() {
  const { org, orgs } = useSession();
  const router = useRouter();
  const params = useSearchParams();
  const projet = params.get("project");
  const scope: Scope = { org, project: projet };
  const currency: Currency = orgs.find((o) => o.slug === org)?.currency === "USD" ? "USD" : "EUR";
  const [periode, setPeriode] = useState<Periode>("30");
  // Fixed once per mount: the forecast must not move under the reader's eyes.
  const [now] = useState(() => new Date());
  const filtre = { since: depuis(periode, now) };
  const projets = useQuery({ queryKey: ["projects", org], queryFn: () => api.projects(org) });
  const parJour = useQuery({
    queryKey: reportKey(scope, "day", filtre),
    queryFn: () => fetchReport(scope, "day", filtre),
  });

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Eyebrow>Where the money goes</Eyebrow>
        <Heading as="h1" size="xl">
          Spend
        </Heading>
        <p className="max-w-3xl text-sm text-ink-muted">
          Read from the cost ledger, which the gateway&apos;s spend feeds. Shown in {currency}, the organisation&apos;s
          currency; the CSV of each breakdown carries both.
        </p>
      </div>
      <div className="flex flex-wrap items-end gap-4">
        <label className="space-y-1 text-xs text-ink-muted">
          <span>Scope</span>
          <Select
            aria-label="Scope"
            className="w-auto"
            value={projet ?? ""}
            onChange={(e) => router.replace(e.target.value ? `/costs?project=${e.target.value}` : "/costs")}
          >
            <option value="">Whole organisation</option>
            {(projets.data?.items ?? []).map((p) => (
              <option key={p.slug} value={p.slug}>
                {p.slug}
              </option>
            ))}
          </Select>
        </label>
        <label className="space-y-1 text-xs text-ink-muted">
          <span>Period</span>
          <Select aria-label="Period" className="w-auto" value={periode} onChange={(e) => setPeriode(e.target.value as Periode)}>
            {PERIODES.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </Select>
        </label>
      </div>

      <MonthPanel scope={scope} currency={currency} now={now} />

      <Card
        title="Spend over time"
        action={
          <a href={csvUrl(scope, "day", filtre)} className="text-xs text-ink-muted hover:text-ink" download>
            CSV
          </a>
        }
      >
        {parJour.error ? (
          <ErrorNote>{String(parJour.error)}</ErrorNote>
        ) : !parJour.data ? (
          <EtatDeLecture lecture={parJour} quoi="the spend" />
        ) : parJour.data.rows.length === 0 ? (
          <Empty>No spend recorded in this period.</Empty>
        ) : (
          <>
            <GrapheDesCouts rows={parJour.data.rows} currency={currency} />
            <p className="mt-3 text-sm" data-testid="total-periode">
              Total {money(parJour.data.rows.reduce((s, r) => s + (currency === "USD" ? r.cost_usd : r.cost_eur), 0), currency)}
            </p>
          </>
        )}
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <BreakdownCard scope={scope} currency={currency} title="Top agents" group="agent" filtre={filtre} />
        <BreakdownCard scope={scope} currency={currency} title="Top workflows" group="workflow" filtre={filtre} />
        <BreakdownCard scope={scope} currency={currency} title="Top work items" group="work_item" filtre={filtre} />
        {!projet && <BreakdownCard scope={scope} currency={currency} title="Projects" group="project" filtre={filtre} />}
        <BreakdownCard scope={scope} currency={currency} title="By model" group="model" filtre={{ ...filtre, kind: "model" }} />
        <BreakdownCard scope={scope} currency={currency} title="By tool" group="model" filtre={{ ...filtre, kind: "tool" }} />
      </div>
    </div>
  );
}

export default function SpendPage() {
  return (
    <Suspense fallback={<Empty>Reading the spend…</Empty>}>
      <Spend />
    </Suspense>
  );
}
