// SPDX-License-Identifier: Apache-2.0
/**
 * Composants transverses de Choregos, posés sur le design system de la Varga Foundation.
 *
 * Les composants du design system restent la bibliothèque ; ceux dont l'allure est codée en dur
 * — un titre en Space Mono gras de 48 px, une puce carrée, un aplat inversé, un trait d'encre sous
 * un en-tête de tableau — sont redonnés ICI, sous les MÊMES noms et les MÊMES props, à l'allure
 * d'une console d'opérations (ADR 0043) : une page change sa ligne d'import, pas son code. Une
 * règle eslint interdit de les importer directement du design system.
 *
 * Ce qui est propre à Choregos — l'état d'un ticket, le coût d'un run, l'acteur d'une étape — se
 * dit en pastilles teintées, la prune pour ce qui agit seul, et toujours en toutes lettres.
 */
"use client";

import Link from "next/link";
import {
  forwardRef,
  useId,
  type ComponentProps,
  type ElementType,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TdHTMLAttributes,
  type TextareaHTMLAttributes,
  type ThHTMLAttributes,
} from "react";
import { Alert, Card as VargaCard, TabList } from "@varga/design-system";
import { cn } from "@/lib/cn";
import { libelleDeCode, segmentsDeCode, tokens, usd } from "@/lib/format";

// ───────────────────────────── tons ─────────────────────────────

/**
 * Les tons du design system, et ceux des états d'une console d'opérations. Les premiers gardent
 * leurs noms pour que les pages n'aient rien à changer ; ils se disent désormais en teintes.
 */
export type Tone =
  | "neutral"
  | "ink"
  | "accent"
  | "ok"
  | "warn"
  | "danger"
  | "running"
  | "waiting"
  | "succeeded"
  | "failed"
  | "retrying"
  | "agent";

/** Une pastille : un fond teinté, un filet de sa teinte, son texte — 4,5:1 au moins (theme-contraste). */
const PASTILLE: Record<Tone, string> = {
  neutral: "border-neutral-line bg-neutral-soft text-neutral-ink",
  ink: "border-line-strong bg-surface-muted text-ink",
  accent: "border-accent-line bg-accent-soft text-accent-strong",
  ok: "border-succeeded-line bg-succeeded-soft text-succeeded-ink",
  succeeded: "border-succeeded-line bg-succeeded-soft text-succeeded-ink",
  warn: "border-waiting-line bg-waiting-soft text-waiting-ink",
  waiting: "border-waiting-line bg-waiting-soft text-waiting-ink",
  danger: "border-failed-line bg-failed-soft text-failed-ink",
  failed: "border-failed-line bg-failed-soft text-failed-ink",
  running: "border-running-line bg-running-soft text-running-ink",
  retrying: "border-retrying-line bg-retrying-soft text-retrying-ink",
  agent: "border-agent-line bg-agent-soft text-agent-ink",
};

/** Le point d'un ton : rond, plein. */
const POINT: Record<Tone, string> = {
  neutral: "bg-neutral",
  ink: "bg-ink",
  accent: "bg-accent",
  ok: "bg-succeeded",
  succeeded: "bg-succeeded",
  warn: "bg-waiting",
  waiting: "bg-waiting",
  danger: "bg-failed",
  failed: "bg-failed",
  running: "bg-running",
  retrying: "bg-retrying",
  agent: "bg-agent",
};

/** Le texte d'un ton, sans fond. */
const TEXTE: Record<Tone, string> = {
  neutral: "text-ink-muted",
  ink: "text-ink",
  accent: "text-accent-strong",
  ok: "text-succeeded-ink",
  succeeded: "text-succeeded-ink",
  warn: "text-waiting-ink",
  waiting: "text-waiting-ink",
  danger: "text-failed-ink",
  failed: "text-failed-ink",
  running: "text-running-ink",
  retrying: "text-retrying-ink",
  agent: "text-agent-ink",
};

// ───────────────────────────── points, pastilles, légendes ─────────────────────────────

/** Le point d'un état : rond (la puce carrée était la signature éditoriale de la fondation). */
export function Dot({ tone = "ink", size = 6, className }: { tone?: Tone; size?: 4 | 6 | 8; className?: string }) {
  const dimension = { 4: "size-1", 6: "size-1.5", 8: "size-2" }[size];
  return <span aria-hidden className={cn("inline-block shrink-0 rounded-full", dimension, POINT[tone], className)} />;
}

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
  /** `outline` et `soft` : la pastille teintée ; `plain` : le point et le texte, sans fond. */
  variant?: "outline" | "soft" | "plain";
  dot?: boolean;
}

/** Une pastille d'état : arrondie, teintée, son point — l'état se lit au mot, jamais à la couleur seule. */
export function Badge({ tone = "neutral", variant = "outline", dot = true, className, children, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 text-xs font-medium whitespace-nowrap",
        variant === "plain" ? TEXTE[tone] : cn("rounded-full border px-2 py-0.5 leading-4", PASTILLE[tone]),
        className,
      )}
      {...props}
    >
      {dot && <Dot tone={tone} size={6} />}
      {children}
    </span>
  );
}

/** Une légende au-dessus d'un titre : petite, en capitales d'affichage, sans cadre. */
export function Eyebrow({ children, tone, className }: { children: ReactNode; tone?: Tone; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 font-mono text-[11px] tracking-[0.06em] text-ink-muted uppercase",
        className,
      )}
    >
      {tone && <Dot tone={tone} size={4} />}
      {children}
    </span>
  );
}

// ───────────────────────────── titres ─────────────────────────────

const TITRE = {
  hero: "text-3xl md:text-4xl leading-tight font-light",
  // Le titre d'une page : 24 px, léger — il était en Space Mono gras de 36 à 48 px.
  xl: "text-2xl leading-tight font-light",
  lg: "text-xl leading-snug font-normal",
  md: "text-base leading-snug font-semibold",
  sm: "text-sm leading-snug font-semibold",
} as const;

export function Heading({
  as: Tag = "h2",
  size = "lg",
  className,
  ...props
}: HTMLAttributes<HTMLHeadingElement> & { as?: ElementType; size?: keyof typeof TITRE }) {
  return <Tag className={cn("font-display tracking-tight text-ink", TITRE[size], className)} {...props} />;
}

/** Ce qui suit un titre de page : une phrase, discrète. */
export function Lead({ className, ...props }: HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("max-w-3xl text-sm text-ink-muted", className)} {...props} />;
}

// ───────────────────────────── tableaux ─────────────────────────────

/** Un tableau dense : 13 px, des lignes de 32 px environ, des en-têtes en petites capitales. */
export function Table({ className, ...props }: HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="w-full overflow-x-auto">
      <table className={cn("w-full border-collapse text-left text-[13px] tabular-nums", className)} {...props} />
    </div>
  );
}

export function THead({ className, ...props }: HTMLAttributes<HTMLTableSectionElement>) {
  return <thead className={cn("border-b border-line", className)} {...props} />;
}

export function TBody({ className, ...props }: HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className={cn("divide-y divide-line", className)} {...props} />;
}

export function TR({ className, interactive, ...props }: HTMLAttributes<HTMLTableRowElement> & { interactive?: boolean }) {
  return <tr className={cn(interactive && "transition-colors hover:bg-surface-muted", className)} {...props} />;
}

export function TH({ className, align = "left", ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn(
        "h-8 px-3 font-mono text-[11px] font-medium tracking-[0.06em] whitespace-nowrap text-ink-muted uppercase",
        align === "right" && "text-right",
        className,
      )}
      {...props}
    />
  );
}

export function TD({ className, align = "left", ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("px-3 py-1.5 align-top", align === "right" && "text-right", className)} {...props} />;
}

// ───────────────────────────── chiffres ─────────────────────────────

/** Le libellé d'un champ ou d'un chiffre : petit, en `ink-muted`. */
export function Label({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return <span className={cn("block text-xs text-ink-muted", className)} {...props} />;
}

/** Un chiffre mis en avant : en Geist Mono léger, aligné ; le libellé dessous. */
export function Stat({
  value,
  label,
  hint,
  tone = "ink",
  className,
}: {
  value: ReactNode;
  label: ReactNode;
  hint?: ReactNode;
  tone?: "ink" | "accent" | "ok" | "warn" | "danger";
  className?: string;
}) {
  return (
    <div className={cn("space-y-1", className)}>
      <div className={cn("font-mono text-2xl leading-none font-light tabular-nums", tone === "ink" ? "text-ink" : TEXTE[tone])}>
        {value}
      </div>
      <Label>{label}</Label>
      {hint != null && <p className="text-xs text-ink-muted">{hint}</p>}
    </div>
  );
}

// ───────────────────────────── boutons et onglets ─────────────────────────────

export type ButtonVariant = "primary" | "secondary" | "ghost" | "accent" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

const VARIANTES: Record<ButtonVariant, string> = {
  // L'action : l'iris, une par vue — l'aplat inversé (blanc en sombre) n'existe plus.
  primary: "border-inverse bg-inverse text-inverse-ink hover:border-inverse-hover hover:bg-inverse-hover",
  accent: "border-accent bg-accent text-accent-ink hover:border-accent-hover hover:bg-accent-hover",
  secondary: "border-line-strong bg-raised text-ink hover:bg-surface-muted",
  ghost: "border-transparent bg-transparent text-ink-muted hover:bg-surface-muted hover:text-ink",
  // Détruire se dit en rouge, sans aplat : un fond rouge au survol rendait le texte illisible.
  danger: "border-failed-line bg-transparent text-failed-ink hover:bg-failed-soft",
};

const TAILLES: Record<ButtonSize, string> = {
  sm: "h-7 gap-1.5 px-2.5 text-xs",
  md: "h-8 gap-2 px-3 text-[13px]",
  lg: "h-10 gap-2 px-4 text-sm",
};

/**
 * Les classes d'un bouton, pour habiller autre chose qu'un `<button>` — un `Link` de Next,
 * typiquement. En Geist Mono : un bouton se commande, il se lit comme une commande. Au doigt,
 * 44 px de haut au moins (WCAG 2.5.8, S23-11).
 */
export function buttonClasses(variant: ButtonVariant = "secondary", size: ButtonSize = "md", className?: string) {
  return cn(
    "inline-flex shrink-0 items-center justify-center border font-mono font-medium no-underline whitespace-nowrap",
    "transition-colors duration-200 ease-standard pointer-coarse:min-h-11",
    "disabled:cursor-not-allowed disabled:opacity-40 aria-disabled:pointer-events-none aria-disabled:opacity-40",
    VARIANTES[variant],
    TAILLES[size],
    className,
  );
}

/** Les classes d'un onglet : l'actif porte le trait de l'accent, plus celui de l'encre. */
export function tabClasses(active: boolean, className?: string) {
  return cn(
    "-mb-px inline-flex items-center gap-2 border-b-2 py-2.5 text-sm whitespace-nowrap no-underline transition-colors duration-200",
    active ? "border-accent font-medium text-ink" : "border-transparent text-ink-muted hover:text-ink",
    className,
  );
}

// ───────────────────────────── champs ─────────────────────────────

/** L'allure d'un champ : 32 px, une bordure de contrôle (3:1), l'accent au focus. */
export const CHAMP =
  "w-full border border-line-strong bg-surface px-2.5 text-[13px] text-ink transition-colors duration-200 ease-standard " +
  "hover:border-ink-subtle focus:border-accent focus:outline-none pointer-coarse:min-h-11 " +
  "disabled:cursor-not-allowed disabled:bg-surface-sunken disabled:text-ink-subtle aria-invalid:border-danger";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...props },
  ref,
) {
  return <input ref={ref} className={cn(CHAMP, "h-8", className)} {...props} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, ...props },
  ref,
) {
  return <textarea ref={ref} className={cn(CHAMP, "min-h-24 py-2 leading-relaxed", className)} {...props} />;
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, ...props },
  ref,
) {
  return <select ref={ref} className={cn(CHAMP, "h-8 pr-7", className)} {...props} />;
});

/**
 * Un champ complet : libellé, contrôle, aide ou erreur — reliés au contrôle (`htmlFor`,
 * `aria-describedby`) : un lecteur d'écran les annonce ensemble.
 */
export function Field({
  label,
  hint,
  error,
  children,
  className,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  children: (props: { id: string; "aria-describedby"?: string; "aria-invalid"?: boolean }) => ReactNode;
  className?: string;
}) {
  const id = useId();
  const messageId = `${id}-message`;
  const message = error ?? hint;
  return (
    <div className={cn("space-y-1.5", className)}>
      <label htmlFor={id}>
        <Label>{label}</Label>
      </label>
      {children({
        id,
        "aria-describedby": message ? messageId : undefined,
        "aria-invalid": error ? true : undefined,
      })}
      {message && (
        <p id={messageId} className={cn("text-xs", error ? "text-danger" : "text-ink-muted")}>
          {message}
        </p>
      )}
    </div>
  );
}

/** Le genre d'un état du workflow, tel que le DSL le déclare, dit dans les tons d'une console d'opérations. */
const STATE_TONES: Record<string, Tone> = {
  // Quelque chose tourne : le bleu.
  work: "running",
  // On attend quelqu'un : l'ambre — c'était un carré blanc en sombre.
  wait: "waiting",
  terminal: "succeeded",
  blocked: "failed",
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
  const tone = state.includes("needs_human") || state.includes("blocked") ? "failed" : (STATE_TONES[resolved] ?? "running");
  return (
    <Badge tone={tone} title={state}>
      {display && display !== state ? display : libelleDeCode(state)}
    </Badge>
  );
}

/**
 * Le coût d'un ticket ou d'un run, en dollars : c'est la monnaie de tout ce qu'un agent dépense
 * (prix des modèles, budgets, estimations), et l'écran d'un ticket mêlait € et US$ (S23-10). La
 * console ne convertit rien : l'API garde chaque écriture avec son taux du jour.
 */
export function CostChip({
  costUsd,
  tokensIn,
  tokensOut,
  budgetUsd,
}: {
  costUsd: number | null | undefined;
  tokensIn?: number;
  tokensOut?: number;
  budgetUsd?: number | null;
}) {
  const over = budgetUsd != null && costUsd != null && costUsd > budgetUsd;
  const title =
    tokensIn || tokensOut ? `${tokens(tokensIn)} tokens in / ${tokens(tokensOut)} out` : undefined;
  return (
    <span
      title={title}
      className={cn("inline-flex items-baseline gap-1 text-xs tabular-nums", over ? "text-danger" : "text-ink")}
    >
      {usd(costUsd)}
      {budgetUsd != null && <span className="text-ink-muted">/ {usd(budgetUsd)}</span>}
    </span>
  );
}

const ACTORS: Record<string, { tone: Tone; label: string }> = {
  agent: { tone: "agent", label: "agent" },
  user: { tone: "ink", label: "human" },
  human: { tone: "ink", label: "human" },
  system: { tone: "neutral", label: "system" },
  train: { tone: "neutral", label: "release train" },
};

/**
 * L'acteur d'une étape : un point dans sa couleur, et son nom. La couleur ne suffit pas — un
 * lecteur d'écran ne la voit pas, et un daltonien confond la prune et le gris : le type d'acteur
 * est toujours énoncé en toutes lettres.
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
export function Card({
  title,
  titreDonnee,
  ...props
}: ComponentProps<typeof VargaCard> & {
  /** Le titre est une donnée (le nom d'un projet) : il garde sa casse (ADR 0042). */
  titreDonnee?: boolean;
}) {
  return (
    <VargaCard
      title={
        typeof title === "string" ? (
          <h2 className="font-display text-sm font-semibold tracking-tight text-ink" data-donnee={titreDonnee || undefined}>
            {title}
          </h2>
        ) : (
          title
        )
      }
      {...props}
      // Une carte s'élève au-dessus de la page (ADR 0043) : sa surface, et tout ce qui « prend la
      // couleur du fond » dedans, est celle d'une carte.
      className={cn("raised", props.className)}
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
   * `primary` : la décision d'un humain (approuver) — l'iris, une par vue.
   * `accent` : ce qui déclenche une machine (lancer, provisionner) — l'iris aussi.
   */
  tone?: keyof typeof TONE_TO_VARIANT;
  disabled?: boolean;
  type?: "button" | "submit";
  title?: string;
  size?: "sm" | "md";
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-describedby={decritPar}
      // Au doigt, 44 px de haut au moins (WCAG 2.5.8, S23-11) : `buttonClasses` les porte.
      className={buttonClasses(TONE_TO_VARIANT[tone], size)}
    >
      {children}
    </button>
  );
}

/**
 * Une phrase du moteur, dont les noms (un agent, un champ produit) sont entre backticks : ils se
 * lisent comme du code, sans les backticks (S23-11).
 */
export function PhraseDuMoteur({ texte }: { texte: string }) {
  return (
    <>
      {segmentsDeCode(texte).map((segment, index) =>
        segment.code ? (
          <code key={index} className="font-mono text-[0.92em]">
            {segment.texte}
          </code>
        ) : (
          <span key={index}>{segment.texte}</span>
        ),
      )}
    </>
  );
}

/**
 * Où l'on est, et le chemin pour remonter (S23-12) : la page d'un ticket et celle d'un run
 * n'avaient ni onglet actif ni fil — on n'y savait pas d'où l'on venait.
 */
export function FilDAriane({ etapes }: { etapes: { href?: string; label: ReactNode }[] }) {
  return (
    <nav aria-label="breadcrumb" className="text-sm">
      <ol className="flex flex-wrap items-center gap-x-2 gap-y-1 text-ink-muted">
        {etapes.map((etape, index) => (
          <li key={index} className="flex min-w-0 items-center gap-2">
            {index > 0 && <span aria-hidden>/</span>}
            {etape.href ? (
              <Link href={etape.href} className="hover:underline">
                {etape.label}
              </Link>
            ) : (
              <span aria-current="page" className="truncate text-ink">
                {etape.label}
              </span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}

/** Une zone vide qui dit quoi faire : un filet pointillé discret, un titre, une phrase, une action. */
export function Empty({ children, title, action }: { children: ReactNode; title?: ReactNode; action?: ReactNode }) {
  return (
    <div className="border border-dashed border-line px-6 py-10 text-center">
      {title && <p className="text-sm font-medium text-ink">{title}</p>}
      {children && <p className="mx-auto mt-1 max-w-md text-sm text-ink-muted">{children}</p>}
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
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
