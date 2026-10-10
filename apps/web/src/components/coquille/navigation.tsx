// SPDX-License-Identifier: Apache-2.0
"use client";

import { BookOpen, Bot, FolderKanban, Inbox, Plug, Settings, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useId } from "react";
import { cn } from "@/lib/cn";

// Les skills ont leur entrée (revue du 07/10 : « il n'y a pas de lien direct dans le menu ») et
// « AI clients » plutôt qu'« integrations » : la page sert aux assistants qui entrent DANS Choregos,
// quand les connecteurs (administration) sont ce vers quoi Choregos sort.
export const NAV: { href: string; label: string; icone: LucideIcon }[] = [
  { href: "/", label: "projects", icone: FolderKanban },
  { href: "/agents", label: "agents", icone: Bot },
  { href: "/skills", label: "skills", icone: BookOpen },
  { href: "/inbox", label: "inbox", icone: Inbox },
  { href: "/integrations", label: "AI clients", icone: Plug },
  { href: "/admin", label: "admin", icone: Settings },
];

export function entreeActive(href: string, pathname: string): boolean {
  return href === "/" ? pathname === "/" || pathname.startsWith("/p/") : pathname.startsWith(href);
}

/** Les entrées, rangées : le travail, ce qu'on y branche, l'organisation. */
export const GROUPES: { titre: string; entrees: string[] }[] = [
  { titre: "work", entrees: ["/", "/inbox"] },
  { titre: "catalogue", entrees: ["/agents", "/skills", "/integrations"] },
  { titre: "organisation", entrees: ["/admin"] },
];

/** Le nombre de choses qui attendent quelqu'un : lu à voix haute comme tel, pas comme un chiffre nu. */
export function Compte({ n, className }: { n: number; className?: string }) {
  return (
    <span
      className={cn(
        "ml-1.5 inline-flex min-w-5 items-center justify-center rounded-full bg-accent px-1.5 font-mono text-[11px] tabular-nums text-accent-ink",
        className,
      )}
    >
      <span aria-hidden>{n}</span>
      <span className="sr-only">, {n} waiting</span>
    </span>
  );
}

/**
 * La navigation rangée en groupes, la même partout : dans la barre latérale (dense, repliable en
 * rail) et dans le panneau du menu sous 1280 px (cibles de 44 px).
 *
 * Les deux peuvent être dans la page en même temps — la barre n'est que cachée sous 1280 px — : les
 * identifiants des légendes viennent de `useId`, sans quoi `aria-labelledby` viserait un doublon.
 */
export function ListeDeNavigation({
  pathname,
  aDecider,
  variante,
  rail = false,
}: {
  pathname: string;
  /** Ce qui attend une personne, compté sur l'entrée « inbox » (S23-02). */
  aDecider: number;
  variante: "barre" | "panneau";
  /** La barre est repliée : les icônes portent leur libellé en infobulle. */
  rail?: boolean;
}) {
  const id = useId();
  const barre = variante === "barre";
  return GROUPES.map((groupe) => {
    const idTitre = `${id}-${groupe.titre}`;
    return (
      <div key={groupe.titre} className={barre ? "mb-3" : "mt-4"}>
        {/* Une légende, pas un titre : la page porte son seul h1 et ses sections. */}
        <p
          id={idTitre}
          className={cn(
            "px-2 pb-1 font-mono text-[11px] tracking-[0.06em] text-ink-muted uppercase",
            barre && "rail:sr-only",
          )}
        >
          {groupe.titre}
        </p>
        <ul aria-labelledby={idTitre}>
          {groupe.entrees.map((href) => {
            const entree = NAV.find((n) => n.href === href);
            if (!entree) return null;
            const Icone = entree.icone;
            const active = entreeActive(href, pathname);
            return (
              <li key={href}>
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  title={barre && rail ? entree.label : undefined}
                  className={cn(
                    "relative flex items-center border-l-2 font-mono no-underline",
                    barre
                      ? "h-7 gap-2.5 px-2 text-[13px] pointer-coarse:min-h-11 rail:justify-center rail:px-0"
                      : "min-h-11 gap-3 px-3 text-sm",
                    active
                      ? "border-accent bg-accent-soft text-ink"
                      : "border-transparent text-ink-muted hover:bg-surface-muted hover:text-ink",
                  )}
                >
                  <Icone aria-hidden className="size-4 shrink-0" strokeWidth={1.75} />
                  <span className={cn(barre && "rail:sr-only")}>{entree.label}</span>
                  {href === "/inbox" && aDecider > 0 && (
                    <Compte
                      n={aDecider}
                      className={cn(
                        "ml-auto",
                        barre && "rail:absolute rail:top-0 rail:right-0.5 rail:min-w-4 rail:px-1 rail:text-[10px]",
                      )}
                    />
                  )}
                </Link>
              </li>
            );
          })}
        </ul>
      </div>
    );
  });
}
