// SPDX-License-Identifier: Apache-2.0
"use client";

import { ChevronDown, KeyRound, LogOut, UserRound } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { ThemePicker } from "@/components/theme-picker";
import type { MeDto } from "@/lib/types";

/** The name a person is shown under: their name, else their address. */
export function personName(me: Pick<MeDto, "display_name" | "email">): string {
  return me.display_name?.trim() || me.email;
}

const ITEM =
  "flex min-h-9 w-full items-center gap-2 rounded px-2 text-left text-[13px] text-ink no-underline hover:bg-surface-muted pointer-coarse:min-h-11";

/**
 * What concerns the signed-in person, and only them: their account, their access tokens, their
 * preferences and signing out — under their name, in the header. The administration keeps what one
 * administers for the organisation.
 *
 * A disclosure button and a panel of links (not an ARIA `menu`: the panel also holds the theme's
 * radio group). It closes on Escape (the focus goes back to the button), on a click outside, and
 * when the page changes.
 */
export function AccountMenu({ me, onSignOut }: { me: MeDto; onSignOut: () => void }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  // Changing page closes the panel, adjusted during the render like the main menu's.
  const [seenPath, setSeenPath] = useState(pathname);
  if (pathname !== seenPath) {
    setSeenPath(pathname);
    setOpen(false);
  }

  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setOpen(false);
      button.current?.focus();
    }
    function onPointer(event: PointerEvent) {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  const name = personName(me);
  return (
    <div ref={root} className="relative">
      <button
        ref={button}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={`Account of ${name}`}
        title={me.email}
        onClick={() => setOpen((value) => !value)}
        className="inline-flex h-7 items-center gap-1 rounded px-2 text-ink hover:bg-surface-muted aria-expanded:bg-surface-muted pointer-coarse:min-h-11"
      >
        <span data-verbatim>{name}</span>
        <ChevronDown aria-hidden className="size-3.5 text-ink-muted" strokeWidth={1.75} />
      </button>
      {open && (
        <div
          id={panelId}
          className="raised absolute right-0 top-full z-30 mt-1 w-64 whitespace-normal rounded-md border border-line p-1.5 text-left"
        >
          <div className="border-b border-line px-2 pb-2 pt-1">
            <p className="truncate text-[13px] font-medium text-ink" data-verbatim>
              {name}
            </p>
            {name !== me.email && (
              <p className="truncate text-xs text-ink-muted" data-verbatim>
                {me.email}
              </p>
            )}
          </div>
          <ul className="space-y-0.5 py-1.5">
            <li>
              <Link href="/account" className={ITEM} onClick={() => setOpen(false)}>
                <UserRound aria-hidden className="size-4 text-ink-muted" strokeWidth={1.75} />
                My account
              </Link>
            </li>
            <li>
              <Link href="/account#tokens" className={ITEM} onClick={() => setOpen(false)}>
                <KeyRound aria-hidden className="size-4 text-ink-muted" strokeWidth={1.75} />
                My access tokens
              </Link>
            </li>
          </ul>
          <div className="border-t border-line px-2 py-2">
            <p className="mb-1 text-xs text-ink-muted">Theme</p>
            <ThemePicker />
          </div>
          <div className="border-t border-line pt-1.5">
            <button type="button" onClick={onSignOut} className={ITEM}>
              <LogOut aria-hidden className="size-4 text-ink-muted" strokeWidth={1.75} />
              Sign out
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
