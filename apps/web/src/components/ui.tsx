// SPDX-License-Identifier: Apache-2.0
/**
 * The console's primitives (ADR 0048): the console owns them, on plain accessible HTML and its own
 * tokens; it no longer depends on the Varga Foundation design system, and an eslint rule refuses an
 * import from it. The look is an operations console in the manner of Temporal's UI: sentence case,
 * sans for what is read and mono only for what is compared, 4–6 px radii, round pills.
 *
 * Ce qui est propre à Choregos — l'état d'un ticket, le coût d'un run, l'acteur d'une étape — se
 * dit en pastilles teintées, la prune pour ce qui agit seul, et toujours en toutes lettres.
 */
"use client";

import Link from "next/link";
import {
  createContext,
  forwardRef,
  useContext,
  useEffect,
  useId,
  useRef,
  type ElementType,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TdHTMLAttributes,
  type TextareaHTMLAttributes,
  type ThHTMLAttributes,
} from "react";
import { cn } from "@/lib/cn";
import { useServedTheme } from "@/components/served-theme";
import { toSentenceCase, libelleDeCode, segmentsDeCode, tokens, usd } from "@/lib/format";

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

/**
 * The classes of a caption: small, sans, medium weight, muted — in sentence case, never uppercased
 * (ADR 0048). For what is not an `Eyebrow`: a group's name, a panel's `h4`, a column's header.
 */
export const LEGENDE = "text-xs font-medium text-ink-muted";

/** A caption above a title: small, muted, no frame. */
export function Eyebrow({ children, tone, className }: { children: ReactNode; tone?: Tone; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5", LEGENDE, className)}>
      {tone && <Dot tone={tone} size={4} />}
      {children}
    </span>
  );
}

// ───────────────────────────── titres ─────────────────────────────

const TITRE = {
  hero: "font-(family-name:--choregos-font-title) text-3xl md:text-4xl leading-tight font-(--choregos-font-title-weight)",
  // The title role (family and weight) is the theme's (ADR 0064): Geist at 600 unless it says otherwise;
  // `lg` stays regular.
  // A page's title: 24 px, semibold, sans — it was 36 to 48 px of bold Space Mono.
  xl: "font-(family-name:--choregos-font-title) text-2xl leading-tight font-(--choregos-font-title-weight)",
  lg: "font-(family-name:--choregos-font-title) text-xl leading-snug font-normal",
  md: "font-(family-name:--choregos-font-title) text-base leading-snug font-(--choregos-font-title-weight)",
  sm: "font-(family-name:--choregos-font-title) text-sm leading-snug font-(--choregos-font-title-weight)",
} as const;

export function Heading({
  as: Tag = "h2",
  size = "lg",
  className,
  ...props
}: HTMLAttributes<HTMLHeadingElement> & { as?: ElementType; size?: keyof typeof TITRE }) {
  return <Tag className={cn("tracking-tight text-ink", TITRE[size], className)} {...props} />;
}

/** Ce qui suit un titre de page : une phrase, discrète. */
export function Lead({ className, ...props }: HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("max-w-3xl text-sm text-ink-muted", className)} {...props} />;
}

// ───────────────────────────── tableaux ─────────────────────────────

/** A dense table: 13 px, rows of about 32 px, headers in a small sans caption. */
export function Table({ className, ...props }: HTMLAttributes<HTMLTableElement>) {
  return (
    // Focusable, so that a keyboard can scroll a table that is wider than its screen (axe: scrollable-region-focusable).
    // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- a scroller must be reachable by keyboard
    <div className="w-full overflow-x-auto" tabIndex={0}>
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
        "h-8 px-3 text-xs font-medium whitespace-nowrap text-ink-muted",
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

/** A number put forward: in Geist Mono (a number is compared), aligned; its label below. */
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
 * A button's classes, to dress something other than a `<button>` — a Next `Link`, typically. In
 * sans, with 4 px corners (ADR 0048). For a finger, 44 px high at least (WCAG 2.5.8, S23-11).
 */
export function buttonClasses(variant: ButtonVariant = "secondary", size: ButtonSize = "md", className?: string) {
  return cn(
    "inline-flex shrink-0 items-center justify-center rounded border font-medium no-underline whitespace-nowrap",
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

/** A field's look: 32 px, a control border (3:1), 4 px corners, the accent on focus. */
export const CHAMP =
  "w-full rounded border border-line-strong bg-surface px-2.5 text-[13px] text-ink transition-colors duration-200 ease-standard " +
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

/** Le ton d'un état du workflow : celui de sa pastille, de son point sur le board. */
export function tonDEtat(state: string, kind?: string): Tone {
  const resolved = kind ?? inferKind(state);
  return state.includes("needs_human") || state.includes("blocked") ? "failed" : (STATE_TONES[resolved] ?? "running");
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
  const tone = tonDEtat(state, kind);
  return (
    <Badge tone={tone} title={state}>
      {display && display !== state ? display : toSentenceCase(libelleDeCode(state))}
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

// Les couleurs des cartes (S24-04) : prune l'agent, bleu la personne, ardoise la plateforme, vert le
// train. La personne était un point d'encre — blanc pur en sombre.
const ACTORS: Record<string, { tone: Tone; label: string }> = {
  agent: { tone: "agent", label: "Agent" },
  user: { tone: "running", label: "Human" },
  human: { tone: "running", label: "Human" },
  system: { tone: "neutral", label: "System" },
  // A job is deterministic code the platform runs (ADR 0056): it reads as the platform, not as a model.
  job: { tone: "neutral", label: "Job" },
  train: { tone: "succeeded", label: "Release train" },
};

/** Le ton d'un acteur : celui de son point, dans la chronologie d'un ticket comme à côté de son nom. */
export function tonDActeur(kind: string): Tone {
  return ACTORS[kind]?.tone ?? "neutral";
}

/**
 * L'acteur d'une étape : un point dans sa couleur, et son nom. La couleur ne suffit pas — un
 * lecteur d'écran ne la voit pas, et un daltonien confond la prune et le gris : le type d'acteur
 * est toujours énoncé en toutes lettres.
 */
export function ActorIcon({ kind, name, point = true }: { kind: string; name?: string | null; point?: boolean }) {
  const actor = ACTORS[kind] ?? { tone: "neutral" as Tone, label: "System" };
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-ink-muted" title={actor.label}>
      {/* Sans point quand un autre le porte déjà : le rail de la chronologie d'un ticket. */}
      {point && <Dot tone={actor.tone} size={6} />}
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

export interface CardProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  title?: ReactNode;
  /** The title is data (a project's name): it keeps its case (ADR 0048 §3). */
  titreDonnee?: boolean;
  /** A short line above the title. */
  eyebrow?: ReactNode;
  /** A button or a link, on the right of the header. */
  action?: ReactNode;
  /** A card that is a link: its border strengthens on hover. */
  interactive?: boolean;
  padding?: "none" | "sm" | "md" | "lg";
}

const CARD_PADDINGS = { none: "", sm: "p-4", md: "p-5", lg: "p-8" } as const;

/**
 * A card: a raised surface, a 1 px hairline, 6 px corners (ADR 0048). Its title is a level-2 heading
 * (S23-05): under a page's `h1`, an `h3` was a skipped level for axe (`heading-order`) on 23 pages
 * out of 36. It is raised above the page (ADR 0043): its surface, and whatever "takes the colour of
 * the background" inside it, is a card's.
 */
export function Card({
  title,
  titreDonnee,
  eyebrow,
  action,
  interactive,
  padding = "md",
  className,
  children,
  ...props
}: CardProps) {
  const hasHeader = title != null || eyebrow != null || action != null;
  return (
    <section
      className={cn(
        "raised rounded-md border border-line",
        interactive && "transition-colors duration-200 ease-standard hover:border-line-strong",
        CARD_PADDINGS[padding],
        className,
      )}
      {...props}
    >
      {hasHeader && (
        <header className={cn("mb-4 flex items-start justify-between gap-4", padding === "none" && "px-5 pt-5")}>
          <div className="min-w-0 space-y-1">
            {eyebrow != null && <Label>{eyebrow}</Label>}
            {typeof title === "string" ? (
              <h2 className="text-sm font-semibold text-ink" data-donnee={titreDonnee || undefined}>
                {title}
              </h2>
            ) : (
              title
            )}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

/** The width of a page: centred, 1400 px at most (`wide`), with its side gutter. */
const PAGE_WIDTHS = { text: "max-w-3xl", page: "max-w-6xl", wide: "max-w-[1400px]" } as const;

export function Container({
  size = "page",
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & { size?: keyof typeof PAGE_WIDTHS }) {
  return <div className={cn("mx-auto w-full px-4 sm:px-6", PAGE_WIDTHS[size], className)} {...props} />;
}

/** Inline code: an identifier, a path, a command — in mono, on a sunken chip. */
export function Code({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return <code className={cn("rounded-sm bg-surface-sunken px-1 py-0.5 font-mono text-[0.85em] text-ink", className)} {...props} />;
}

/**
 * A message in the page: a tinted panel and its point; the tone is said by the title, never by the
 * colour alone. `danger` is an `alert` (announced at once), anything else a `status`.
 */
export function Alert({
  tone = "neutral",
  title,
  children,
  className,
}: {
  tone?: Tone;
  title?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div
      role={tone === "danger" || tone === "failed" ? "alert" : "status"}
      className={cn("rounded-md border px-3 py-2.5 text-sm", PASTILLE[tone], className)}
    >
      {title && (
        <p className="mb-0.5 flex items-center gap-2 font-medium">
          <Dot tone={tone} size={6} />
          {title}
        </p>
      )}
      {children && <div className={title ? "text-ink-muted" : undefined}>{children}</div>}
    </div>
  );
}

/**
 * A section that folds (S39-05): a native `<details>`, so the keyboard and assistive technology
 * work without a script. The `summary` says the current value in one line, so a closed section
 * still tells what it holds. Closed unless `defaultOpen`.
 */
export function Disclosure({
  title,
  summary,
  defaultOpen = false,
  children,
  id,
  className,
}: {
  title: ReactNode;
  summary?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
  id?: string;
  className?: string;
}) {
  return (
    <details id={id} open={defaultOpen} className={cn("group rounded-md border border-line", className)}>
      <summary className="flex cursor-pointer list-none flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded-md px-4 py-3 text-sm marker:hidden [&::-webkit-details-marker]:hidden pointer-coarse:min-h-11">
        <span aria-hidden className="text-ink-muted transition-transform duration-200 group-open:rotate-90">
          ›
        </span>
        <span className="font-semibold text-ink">{title}</span>
        {summary != null && <span className="min-w-0 text-xs text-ink-muted">{summary}</span>}
      </summary>
      <div className="space-y-3 border-t border-line p-4">{children}</div>
    </details>
  );
}

/**
 * A box that is ticked or not — a step done, a criterion met. It shows a state; it is not a control
 * (a form's checkbox is a native `<input type="checkbox">`).
 */
export function Checkbox({ checked = true, className }: { checked?: boolean; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-flex size-4 shrink-0 items-center justify-center rounded-sm border",
        checked ? "border-accent bg-accent text-accent-ink" : "border-line-strong bg-surface",
        className,
      )}
    >
      {checked && (
        <svg viewBox="0 0 10 10" className="size-2.5" fill="none" stroke="currentColor" strokeWidth="1.6">
          <path d="M1.5 5.2L4 7.5L8.5 2.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
    </span>
  );
}

/** The number of a step in a sequence (1, 2, 3): a round chip, mono digits. */
export function StepNumber({ value, className }: { value: number | string; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex size-6 shrink-0 items-center justify-center rounded-full border border-line-strong font-mono text-xs tabular-nums text-ink",
        className,
      )}
    >
      {value}
    </span>
  );
}

/**
 * The console's wordmark: the product's name, in its own letters — not the foundation's mark (ADR
 * 0048). The glyph is a plain geometric figure (a stage and its next step), drawn here.
 */
export function Wordmark({ className, compact = false }: { className?: string; compact?: boolean }) {
  const theme = useServedTheme();
  // `logo` is drawn on the light mode, `logo_dark` on the dark one; one alone serves both.
  const light = theme.logo ?? theme.logo_dark;
  const dark = theme.logo_dark ?? theme.logo;
  return (
    <span className={cn("inline-flex items-center gap-2 text-ink", className)}>
      {light && dark ? (
        <>
          {/* A served file: `next/image` would add an optimiser in front of a same-origin asset. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            data-testid="wordmark-logo-dark"
            src={dark}
            alt=""
            className="block h-5 w-auto max-w-32 shrink-0 [:root[data-theme=light]_&]:hidden"
          />
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            data-testid="wordmark-logo-light"
            src={light}
            alt=""
            className="hidden h-5 w-auto max-w-32 shrink-0 [:root[data-theme=light]_&]:block"
          />
        </>
      ) : (
        <svg aria-hidden viewBox="0 0 20 20" className="size-5 shrink-0" fill="none">
          <rect x="1.5" y="1.5" width="17" height="17" rx="4" stroke="currentColor" strokeOpacity="0.35" />
          <circle cx="7" cy="10" r="2.5" fill="var(--choregos-accent)" />
          <path d="M10.5 10h5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      )}
      <span className={cn("text-[15px] font-semibold tracking-tight", compact && "sr-only")}>
        {theme.product_name}
      </span>
    </span>
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
  "aria-label": etiquette,
}: {
  children: ReactNode;
  onClick?: () => void;
  /** A name for a button whose text alone is not enough (an arrow, a bare "Remove" in a list). */
  "aria-label"?: string;
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
      aria-label={etiquette}
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

export interface BreadcrumbStep {
  href?: string;
  label: ReactNode;
}

/**
 * The steps above the current page, set by a layout (the project, the administration): a page's
 * breadcrumbs start with them, so the trail is one line from the organisation down (S30-03).
 */
export const BreadcrumbBase = createContext<BreadcrumbStep[]>([]);

/**
 * Where one is, and the way back up (S23-12, S30-03): the tab rows are gone, the trail
 * carries the hierarchy. The steps of the enclosing layout (`BreadcrumbBase`) come first.
 */
export function Breadcrumbs({ steps }: { steps: BreadcrumbStep[] }) {
  const base = useContext(BreadcrumbBase);
  const etapes = [...base, ...steps];
  return (
    <nav aria-label="Breadcrumb" className="text-sm">
      <ol className="flex flex-wrap items-center gap-x-2 gap-y-1 text-ink-muted">
        {etapes.map((etape, index) => (
          <li key={index} className="flex min-w-0 items-center gap-2">
            {index > 0 && <span aria-hidden>/</span>}
            {etape.href && index < etapes.length - 1 ? (
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

/**
 * A drawer: a modal panel on the right edge (S30-04), for what one opens from a list — a new agent,
 * the catalogue. A native `<dialog>` shown modal: the focus stays inside, Escape closes it, and the
 * page behind is inert.
 */
export function Drawer({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      // jsdom has no `showModal`: the attribute is enough to show it there.
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
    if (!open && dialog.open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }
  }, [open]);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      onCancel={onClose}
      className="raised fixed inset-y-0 right-0 left-auto m-0 h-dvh max-h-dvh w-full max-w-2xl overflow-y-auto border-l border-line p-0 text-ink backdrop:bg-black/40"
    >
      {open && (
        <div className="space-y-4 p-5">
          <div className="flex items-center justify-between gap-4">
            <h2 id={titleId} className="text-base font-semibold text-ink">
              {title}
            </h2>
            <button type="button" onClick={onClose} className={buttonClasses("ghost", "sm")}>
              Close
            </button>
          </div>
          {children}
        </div>
      )}
    </dialog>
  );
}

/** Une zone vide qui dit quoi faire : un filet pointillé discret, un titre, une phrase, une action. */
export function Empty({ children, title, action }: { children: ReactNode; title?: ReactNode; action?: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-line px-6 py-10 text-center">
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
        Could not {lecture.data === undefined ? "read" : "refresh"} {quoi}: {message}.{" "}
        <button type="button" className="underline" onClick={() => void lecture.refetch()} disabled={lecture.isRefetching}>
          {lecture.isRefetching ? "Retrying…" : "Retry"}
        </button>
      </ErrorNote>
    );
  }
  if (lecture.data === undefined && lecture.fetchStatus !== "idle") {
    return (
      <p className="text-sm text-ink-muted" role="status">
        Reading {quoi}…
      </p>
    );
  }
  return null;
}

/**
 * A row of tabs: links to the sections of a page, the current one with `aria-current="page"` and
 * the accent's underline (`tabClasses`). It is navigation, not an ARIA `tablist`: each tab is a page.
 *
 * The active tab drops one pixel (`-mb-px`) to lay its underline on the bar's rule; in a bar that
 * scrolls sideways (`overflow-x-auto`), that extra pixel was a vertical overflow, and Windows drew
 * its ▲ ● ▼ arrows at the end of every tab bar (seen on 06/10). One pixel of bottom padding
 * (`pb-px`) takes it.
 */
export function Tabs({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return <nav className={cn("flex gap-6 overflow-x-auto border-b border-line pb-px", className)} {...props} />;
}
