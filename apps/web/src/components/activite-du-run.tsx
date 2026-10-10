// SPDX-License-Identifier: Apache-2.0
"use client";

import {
  Bot,
  Brain,
  FileText,
  Globe,
  MoveRight,
  Pencil,
  Search,
  Terminal,
  ToggleRight,
  Trash2,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { Badge, Table, TBody, TD, TH, THead, TR, type Tone } from "@/components/ui";
import type { ActiviteDuRun, AppelDOutil, StatutDAppel } from "@/lib/activite-du-run";
import { duration } from "@/lib/format";

const ICONES: Record<string, LucideIcon> = {
  read: FileText,
  edit: Pencil,
  delete: Trash2,
  move: MoveRight,
  search: Search,
  execute: Terminal,
  think: Brain,
  fetch: Globe,
  switch_mode: ToggleRight,
};

const TONS: Record<StatutDAppel, Tone> = { pending: "neutral", in_progress: "running", completed: "succeeded", failed: "failed" };
const LIBELLES: Record<StatutDAppel, string> = { pending: "pending", in_progress: "running", completed: "done", failed: "failed" };

const HEURE = new Intl.DateTimeFormat("en-GB", { timeStyle: "medium" });

/** La durée d'un appel : à la seconde près, au dixième sous la seconde. */
function dureeDAppel(ms: number | undefined): string {
  if (ms === undefined) return "—";
  return ms < 1000 ? `${(ms / 1000).toFixed(1)} s` : duration(ms / 1000);
}

/**
 * L'activité d'un run (S25-02), au-dessus du journal brut : chaque appel d'outil une fois, son état,
 * sa durée, et le refus qui l'a bloqué ; puis les refus que rien ne relie à un appel.
 */
export function ActiviteDuRunVue({ activite }: { activite: ActiviteDuRun }) {
  const { appels, refus, termines, sousAgents } = activite;
  const orphelins = refus.filter((r) => !r.appel || !appels.some((a) => a.id === r.appel));
  return (
    <div className="space-y-3" data-testid="activite-du-run">
      <p className="font-mono text-xs text-ink-muted" data-testid="activite-resume">
        {appels.length} tool call{appels.length === 1 ? "" : "s"} · {termines} finished · {refus.length} refused
        {sousAgents > 0 ? ` · ${sousAgents} sub-agent${sousAgents === 1 ? "" : "s"}` : ""}
      </p>
      {appels.length > 0 && (
        <div className="max-h-96 overflow-auto">
          <Table aria-label="tool calls">
            <THead>
              <TR>
                <TH>started</TH>
                <TH>tool</TH>
                <TH>status</TH>
                <TH align="right">duration</TH>
              </TR>
            </THead>
            <TBody>
              {appels.map((appel) => (
                <Ligne key={appel.id} appel={appel} />
              ))}
            </TBody>
          </Table>
        </div>
      )}
      {orphelins.length > 0 && (
        <ul aria-label="refusals outside a tool call" className="space-y-1 text-xs">
          {orphelins.map((r, i) => (
            <li key={`${r.cible}-${i}`} className="flex flex-wrap items-baseline gap-2">
              <Badge tone="failed">denied</Badge>
              <span className="font-mono">{r.cible}</span>
              <span className="text-ink-muted">{r.raison}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Ligne({ appel }: { appel: AppelDOutil }) {
  const Icone = appel.sousAgent ? Bot : (ICONES[appel.genre] ?? Wrench);
  return (
    <TR data-appel={appel.id} data-statut={appel.statut}>
      <TD className="font-mono text-xs whitespace-nowrap text-ink-muted tabular-nums">{HEURE.format(new Date(appel.debut))}</TD>
      <TD className="min-w-0">
        <span className="flex min-w-0 items-center gap-2">
          <Icone aria-hidden className="size-4 shrink-0 text-ink-muted" strokeWidth={1.75} />
          <span className="truncate" title={appel.chemins.join(", ") || undefined}>
            {appel.titre}
          </span>
          <span className="sr-only">, {appel.sousAgent ? "sub-agent" : appel.genre} </span>
          {appel.sousAgent && <Badge tone="agent">sub-agent</Badge>}{" "}
          {appel.refus && (
            <span title={appel.refus}>
              <Badge tone="failed">denied</Badge>
            </span>
          )}
        </span>
      </TD>
      <TD>
        <Badge tone={TONS[appel.statut]}>{LIBELLES[appel.statut]}</Badge>
      </TD>
      <TD align="right" className="font-mono text-xs whitespace-nowrap tabular-nums">
        {dureeDAppel(appel.dureeMs)}
      </TD>
    </TR>
  );
}
