// SPDX-License-Identifier: Apache-2.0
"use client";

import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useSyncExternalStore } from "react";
import { AboutEntry } from "@/components/edition";
import { Wordmark } from "@/components/ui";
import { useInboxCount } from "@/lib/a-decider";
import { appliquerLaBarre, chooseSidebar, SIDEBAR_KEY, readSidebar, type Barre } from "@/lib/preferences";
import { useSession } from "@/lib/session";
import { NavigationList } from "./navigation";

/** A fold made in THIS tab: `storage` only warns the others. */
const LOCAL_EVENT = "choregos:sidebar";

function abonner(rappel: () => void): () => void {
  const surStockage = (event: StorageEvent) => {
    if (event.key !== SIDEBAR_KEY) return;
    appliquerLaBarre(readSidebar());
    rappel();
  };
  window.addEventListener("storage", surStockage);
  window.addEventListener(LOCAL_EVENT, rappel);
  return () => {
    window.removeEventListener("storage", surStockage);
    window.removeEventListener(LOCAL_EVENT, rappel);
  };
}

/** What is displayed: the attribute the header script set before the paint. */
function barreAffichee(): Barre {
  return document.documentElement.dataset.sidebar === "rail" ? "rail" : "full";
}

/** The bar, full or folded, and what toggles it; the choice is kept (`choregos.sidebar`). */
export function useBarre(): [Barre, () => void] {
  const barre = useSyncExternalStore(abonner, barreAffichee, (): Barre => "full");
  const basculer = useCallback(() => {
    chooseSidebar(barreAffichee() === "rail" ? "full" : "rail");
    window.dispatchEvent(new Event(LOCAL_EVENT));
  }, []);
  return [barre, basculer];
}

/**
 * The sidebar of an operations console (ADR 0043): from 1280 px, the navigation
 * leaves the header for a column on the left, darker than the page, arranged in groups. It folds
 * into an icon rail, and the fold is kept. Below 1280 px, the header keeps its "menu" button
 * (S23-01) and this bar is not displayed.
 *
 * The rail is drawn by CSS (`rail:`), on the attribute the header script sets: a folded
 * bar is folded from the first paint, JavaScript blocked included. React uses it only for
 * the icons' tooltips and for the button.
 */
export function Sidebar() {
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
        <NavigationList pathname={pathname} aDecider={aDecider} variante="barre" rail={barre === "rail"} />
      </nav>
      <div className="shrink-0 space-y-0.5 border-t border-line p-2">
        {/* Version, licence, edition, publisher: what is deployed (S42-12). The rail keeps the icon. */}
        <AboutEntry rail className="flex h-7 w-full items-center gap-2.5 rounded px-2 text-[13px] text-ink-muted hover:bg-surface-muted hover:text-ink pointer-coarse:min-h-11 rail:justify-center rail:px-0" />
        {/* Both states are rendered, the CSS shows the right one: correct from the first paint. */}
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
