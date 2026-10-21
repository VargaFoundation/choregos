// SPDX-License-Identifier: Apache-2.0
"use client";

import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useSyncExternalStore } from "react";
import { AboutEntry } from "@/components/edition";
import { Wordmark } from "@/components/ui";
import { useInboxCount } from "@/lib/a-decider";
import { appliquerLaBarre, choisirLaBarre, CLE_DE_LA_BARRE, lireLaBarre, type Barre } from "@/lib/preferences";
import { useSession } from "@/lib/session";
import { ListeDeNavigation } from "./navigation";

/** Un repli fait dans CET onglet : `storage` ne prévient que les autres. */
const EVENEMENT_LOCAL = "choregos:sidebar";

function abonner(rappel: () => void): () => void {
  const surStockage = (evenement: StorageEvent) => {
    if (evenement.key !== CLE_DE_LA_BARRE) return;
    appliquerLaBarre(lireLaBarre());
    rappel();
  };
  window.addEventListener("storage", surStockage);
  window.addEventListener(EVENEMENT_LOCAL, rappel);
  return () => {
    window.removeEventListener("storage", surStockage);
    window.removeEventListener(EVENEMENT_LOCAL, rappel);
  };
}

/** Ce qui s'affiche : l'attribut que le script d'en-tête a posé avant la peinture. */
function barreAffichee(): Barre {
  return document.documentElement.dataset.sidebar === "rail" ? "rail" : "full";
}

/** La barre pleine ou repliée, et de quoi basculer ; le choix se garde (`choregos.sidebar`). */
export function useBarre(): [Barre, () => void] {
  const barre = useSyncExternalStore(abonner, barreAffichee, (): Barre => "full");
  const basculer = useCallback(() => {
    choisirLaBarre(barreAffichee() === "rail" ? "full" : "rail");
    window.dispatchEvent(new Event(EVENEMENT_LOCAL));
  }, []);
  return [barre, basculer];
}

/**
 * La barre latérale d'une console d'opérations (ADR 0043) : à partir de 1280 px, la navigation
 * quitte l'en-tête pour une colonne à gauche, plus sombre que la page, rangée en groupes. Elle se
 * replie en rail d'icônes, et le repli se garde. En dessous de 1280 px, l'en-tête garde son bouton
 * « menu » (S23-01) et cette barre n'est pas affichée.
 *
 * Le rail est dessiné par CSS (`rail:`), sur l'attribut que pose le script d'en-tête : une barre
 * repliée l'est dès la première peinture, JavaScript bloqué compris. React ne s'en sert que pour
 * les infobulles des icônes et pour le bouton.
 */
export function BarreLaterale() {
  const pathname = usePathname();
  const { org } = useSession();
  const aDecider = useInboxCount(org, pathname);
  const [barre, basculer] = useBarre();
  return (
    <aside
      aria-label="Sidebar"
      className="sidebar-surface hidden border-r border-line xl:sticky xl:top-0 xl:flex xl:h-dvh xl:flex-col"
    >
      <div className="flex h-12 shrink-0 items-center border-b border-line px-4 rail:justify-center rail:px-0">
        <Link href="/" className="no-underline">
          {/* Folded, the glyph alone: the name does not fit in 52 px (it stays for a screen reader). */}
          <Wordmark className="rail:[&>span]:sr-only" />
        </Link>
      </div>
      <nav aria-label="Main navigation" className="flex-1 overflow-y-auto px-2 py-3">
        <ListeDeNavigation pathname={pathname} aDecider={aDecider} variante="barre" rail={barre === "rail"} />
      </nav>
      <div className="shrink-0 space-y-0.5 border-t border-line p-2">
        {/* Version, licence, edition, publisher: what is deployed (S42-12). The rail keeps the icon. */}
        <AboutEntry rail className="flex h-7 w-full items-center gap-2.5 rounded px-2 text-[13px] text-ink-muted hover:bg-surface-muted hover:text-ink pointer-coarse:min-h-11 rail:justify-center rail:px-0" />
        {/* Les deux états sont rendus, le CSS montre le bon : juste dès la première peinture. */}
        <button
          type="button"
          onClick={basculer}
          title={barre === "rail" ? "Expand sidebar" : undefined}
          className="flex h-7 w-full items-center gap-2.5 rounded px-2 text-[13px] text-ink-muted hover:bg-surface-muted hover:text-ink pointer-coarse:min-h-11 rail:justify-center rail:px-0"
        >
          <PanelLeftClose aria-hidden className="size-4 shrink-0 rail:hidden" strokeWidth={1.75} />
          <PanelLeftOpen aria-hidden className="hidden size-4 shrink-0 rail:block" strokeWidth={1.75} />
          <span className="rail:hidden">Collapse sidebar</span>
          <span className="sr-only hidden rail:inline">Expand sidebar</span>
        </button>
      </div>
    </aside>
  );
}
