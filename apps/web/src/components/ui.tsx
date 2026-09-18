/** Composants transverses : badges d'état, coût, acteur, barre de décision, journal. */
"use client";

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { eur, tokens } from "@/lib/format";

const WORK_TONE = "bg-agent/15 text-agent border-agent/30";

const STATE_TONES: Record<string, string> = {
  wait: "bg-warn/15 text-warn border-warn/30",
  work: WORK_TONE,
  terminal: "bg-ok/15 text-ok border-ok/30",
  blocked: "bg-danger/15 text-danger border-danger/30",
};

export function StateBadge({
  state,
  display,
  kind = "work",
}: {
  state: string;
  display?: string | null;
  kind?: string;
}) {
  const tone = state.includes("needs_human") || state.includes("blocked") ? "blocked" : kind;
  return (
    <span
      title={state}
      className={cn(
        "inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium",
        STATE_TONES[tone] ?? WORK_TONE,
      )}
    >
      {display ?? state}
    </span>
  );
}

export function CostChip({
  costEur,
  tokensIn,
  tokensOut,
  budgetEur,
}: {
  costEur: number | null | undefined;
  tokensIn?: number;
  tokensOut?: number;
  budgetEur?: number | null;
}) {
  const over = budgetEur != null && costEur != null && costEur > budgetEur;
  const title =
    tokensIn || tokensOut ? `${tokens(tokensIn)} tokens entrants / ${tokens(tokensOut)} sortants` : undefined;
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1 rounded bg-surface-muted px-2 py-0.5 font-mono text-xs",
        over ? "text-danger" : "text-ink-muted",
      )}
    >
      {eur(costEur)}
      {budgetEur != null && <span className="text-ink-muted/60">/ {eur(budgetEur)}</span>}
    </span>
  );
}

interface ActorGlyph {
  glyph: string;
  label: string;
  className: string;
}

const SYSTEM_GLYPH: ActorGlyph = { glyph: "▣", label: "système", className: "text-system" };

const ACTOR_GLYPH: Record<string, ActorGlyph> = {
  agent: { glyph: "◆", label: "agent", className: "text-agent" },
  user: { glyph: "●", label: "humain", className: "text-human" },
  human: { glyph: "●", label: "humain", className: "text-human" },
  system: SYSTEM_GLYPH,
  train: { glyph: "▶", label: "release train", className: "text-system" },
};

export function ActorIcon({ kind, name }: { kind: string; name?: string | null }) {
  const actor = ACTOR_GLYPH[kind] ?? SYSTEM_GLYPH;
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs", actor.className)} title={actor.label}>
      <span aria-hidden>{actor.glyph}</span>
      <span className="sr-only">{actor.label}</span>
      {name && <span className="text-ink-muted">{name}</span>}
    </span>
  );
}

export function Card({
  title,
  action,
  children,
  className,
}: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("rounded-lg border border-line bg-surface p-4", className)}>
      {(title || action) && (
        <header className="mb-3 flex items-center justify-between gap-2">
          {typeof title === "string" ? <h2 className="text-sm font-semibold">{title}</h2> : title}
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function Button({
  children,
  onClick,
  tone = "default",
  disabled,
  type = "button",
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  tone?: "default" | "primary" | "danger";
  disabled?: boolean;
  type?: "button" | "submit";
  title?: string;
}) {
  const tones = {
    default: "border-line bg-surface hover:bg-surface-muted",
    primary: "border-agent/40 bg-agent/10 text-agent hover:bg-agent/20",
    danger: "border-danger/40 bg-danger/10 text-danger hover:bg-danger/20",
  } as const;
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(
        "rounded border px-3 py-1.5 text-sm transition disabled:cursor-not-allowed disabled:opacity-40",
        tones[tone],
      )}
    >
      {children}
    </button>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded border border-dashed border-line px-4 py-8 text-center text-sm text-ink-muted">{children}</p>;
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="rounded border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
      {children}
    </p>
  );
}
