// SPDX-License-Identifier: Apache-2.0
/**
 * Composants transverses de Choregos, posés sur le design system de la Varga Foundation.
 *
 * Les noms et les signatures restent ceux que les pages utilisent déjà ; seul le rendu passe
 * par `@varga/design-system`. Ce qui est propre à Choregos — l'état d'un ticket, le coût d'un
 * run, l'acteur d'une étape — se traduit dans la grammaire de la fondation : une puce carrée
 * et un libellé, le turquoise pour ce qui agit seul.
 */
"use client";

import type { ComponentProps, ReactNode } from "react";
import {
  Alert,
  Badge,
  Button as VargaButton,
  Card as VargaCard,
  Dot,
  Empty as VargaEmpty,
  TabList,
  type Tone,
} from "@varga/design-system";
import { cn } from "@/lib/cn";
import { eur, tokens } from "@/lib/format";

/** Le genre d'un état du workflow, tel que le DSL le déclare. */
const STATE_TONES: Record<string, Tone> = {
  // Un agent travaille : la seule couleur qui dise « ça tourne sans vous ».
  work: "accent",
  // On attend quelqu'un : le noir de l'humain.
  wait: "ink",
  terminal: "ok",
  blocked: "danger",
};

/**
 * Le genre d'un état quand l'appelant ne le connaît pas — un ticket porte le *nom* de son état,
 * pas son genre, qui vit dans le workflow. Les templates nomment leurs états de façon régulière
 * (`awaiting_*`, `deployed_*`, `*_blocked`) : c'est cette régularité qu'on lit. Un appelant qui
 * a le workflow sous la main (le board) passe `kind` et court-circuite la déduction.
 */
function inferKind(state: string): string {
  const s = state.toLowerCase();
  if (/(blocked|failed|rejected|error)/.test(s)) return "blocked";
  if (/(deployed|merged|done|closed|released|resolved)/.test(s)) return "terminal";
  if (/(awaiting|needs_human|approval|review|pending|draft|open)/.test(s)) return "wait";
  return "work";
}

export function StateBadge({
  state,
  display,
  kind,
}: {
  state: string;
  display?: string | null;
  kind?: string;
}) {
  const resolved = kind ?? inferKind(state);
  const tone = state.includes("needs_human") || state.includes("blocked") ? "danger" : (STATE_TONES[resolved] ?? "accent");
  return (
    <Badge tone={tone} title={state}>
      {display ?? state}
    </Badge>
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
    tokensIn || tokensOut ? `${tokens(tokensIn)} tokens in / ${tokens(tokensOut)} out` : undefined;
  return (
    <span
      title={title}
      className={cn("inline-flex items-baseline gap-1 text-xs tabular-nums", over ? "text-danger" : "text-ink")}
    >
      {eur(costEur)}
      {budgetEur != null && <span className="text-ink-muted">/ {eur(budgetEur)}</span>}
    </span>
  );
}

const ACTORS: Record<string, { tone: Tone; label: string }> = {
  agent: { tone: "accent", label: "agent" },
  user: { tone: "ink", label: "human" },
  human: { tone: "ink", label: "human" },
  system: { tone: "neutral", label: "system" },
  train: { tone: "neutral", label: "release train" },
};

/**
 * L'acteur d'une étape : une puce carrée dans sa couleur, et son nom. La couleur ne suffit pas
 * — un lecteur d'écran ne la voit pas, et un daltonien confond le turquoise et le gris : le
 * type d'acteur est toujours énoncé en toutes lettres.
 */
export function ActorIcon({ kind, name }: { kind: string; name?: string | null }) {
  const actor = ACTORS[kind] ?? { tone: "neutral" as Tone, label: "system" };
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-ink-muted" title={actor.label}>
      <Dot tone={actor.tone} size={6} />
      {name ? (
        <>
          <span className="sr-only">{actor.label}</span>
          <span>{name}</span>
        </>
      ) : (
        <span className="text-ink">{actor.label}</span>
      )}
    </span>
  );
}

/**
 * La carte du design system, titrée au niveau 2 (S23-05). Elle titrait en `h3` : sous le `h1` d'une
 * page, axe relevait un saut de niveau (`heading-order`) sur 23 pages sur 36, et un lecteur d'écran
 * qui parcourt la page par titres cherchait des sections qui n'existaient pas.
 */
export function Card({ title, ...props }: ComponentProps<typeof VargaCard>) {
  return (
    <VargaCard
      title={
        typeof title === "string" ? (
          <h2 className="font-display text-base font-bold tracking-tight text-ink">{title}</h2>
        ) : (
          title
        )
      }
      {...props}
    />
  );
}

const TONE_TO_VARIANT = {
  default: "secondary",
  primary: "primary",
  accent: "accent",
  danger: "danger",
} as const;

export function Button({
  children,
  onClick,
  tone = "default",
  disabled,
  type = "button",
  title,
  size = "md",
  "aria-describedby": decritPar,
}: {
  children: ReactNode;
  onClick?: () => void;
  /** Ce qui explique le bouton — pourquoi il attend, ce qu'il va faire. */
  "aria-describedby"?: string;
  /**
   * `primary` : la décision d'un humain (approuver) — noir, une par vue.
   * `accent` : ce qui déclenche une machine (lancer, provisionner) — turquoise.
   */
  tone?: keyof typeof TONE_TO_VARIANT;
  disabled?: boolean;
  type?: "button" | "submit";
  title?: string;
  size?: "sm" | "md";
}) {
  return (
    <VargaButton
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-describedby={decritPar}
      variant={TONE_TO_VARIANT[tone]}
      size={size}
    >
      {children}
    </VargaButton>
  );
}

export function Empty({ children, title, action }: { children: ReactNode; title?: ReactNode; action?: ReactNode }) {
  return (
    <VargaEmpty title={title} action={action}>
      {children}
    </VargaEmpty>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return <Alert tone="danger">{children}</Alert>;
}

/**
 * Ce que montre une lecture tant qu'elle n'a rien de bon à montrer (S23-08) : son échec, avec un
 * moyen de relire, ou son chargement — jamais « rien » ni « vide ». Avant, une API en panne
 * affichait « no spend recorded. », « no batch », « measurements being computed. » ou un
 * « loading… » sans fin : une panne passait pour un projet calme. Une lecture désactivée (une
 * recherche pas encore lancée) ne dit rien.
 */
export function EtatDeLecture({
  lecture,
  quoi,
}: {
  lecture: { data: unknown; error: unknown; fetchStatus: string; refetch: () => unknown; isRefetching?: boolean };
  /** Ce qui se lit, dans une phrase : « the costs », « the batches ». */
  quoi: string;
}) {
  if (lecture.error) {
    const message = lecture.error instanceof Error ? lecture.error.message : String(lecture.error);
    return (
      <ErrorNote>
        could not {lecture.data === undefined ? "read" : "refresh"} {quoi}: {message}.{" "}
        <button type="button" className="underline" onClick={() => void lecture.refetch()} disabled={lecture.isRefetching}>
          {lecture.isRefetching ? "retrying…" : "retry"}
        </button>
      </ErrorNote>
    );
  }
  if (lecture.data === undefined && lecture.fetchStatus !== "idle") {
    return (
      <p className="text-sm text-ink-muted" role="status">
        reading {quoi}…
      </p>
    );
  }
  return null;
}

/**
 * La barre d'onglets du design system, sans la barre de défilement VERTICALE qu'elle portait.
 *
 * L'onglet actif descend d'un pixel (`-mb-px`) pour poser son trait sur le filet de la barre ; dans
 * une barre qui défile en largeur (`overflow-x-auto`), ce pixel de trop est un débordement vertical,
 * et Windows dessinait ses flèches ▲ ● ▼ au bout de chaque barre d'onglets de la console (relevé le
 * 06/10 sur la page d'un workflow). Un pixel de marge basse le reçoit : rien ne déborde, et le trait
 * de l'onglet actif garde ses deux pixels.
 */
export function Onglets({ className, ...props }: ComponentProps<typeof TabList>) {
  return <TabList className={cn("pb-px", className)} {...props} />;
}
