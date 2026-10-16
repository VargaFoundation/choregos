// SPDX-License-Identifier: Apache-2.0
"use client";

import { useEffect, useRef, type ReactNode } from "react";

/**
 * The side panel an install happens in (ADR 0065): the same shell for an MCP server (a name and a
 * secret reference) and for a skill (what it contains, and who must approve it). It is a modal
 * dialog: it takes the focus, Escape closes it, and the page behind stays where it was.
 */
export function InstallDrawer({
  title,
  onClose,
  children,
}: {
  /** Names what is being installed; data keeps its case. */
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  // The latest `onClose`, read at the key press: the effect runs once, so typing in a field of the
  // panel never moves the focus back to the panel.
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") close.current();
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      before?.focus?.();
    };
  }, []);
  return (
    <div className="fixed inset-0 z-50 flex justify-end" data-testid="install-drawer">
      <button
        type="button"
        aria-label="Close the panel"
        className="absolute inset-0 bg-ink/30"
        tabIndex={-1}
        onClick={onClose}
      />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby="install-drawer-title"
        tabIndex={-1}
        className="raised relative flex h-full w-full max-w-md flex-col gap-4 overflow-y-auto border-l border-line p-6 shadow-lg focus:outline-none"
      >
        <header className="flex items-start justify-between gap-4">
          <h2 id="install-drawer-title" className="text-base font-semibold text-ink">
            Install <span data-donnee>{title}</span>
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded px-2 py-1 text-sm text-ink-muted hover:text-ink pointer-coarse:min-h-11"
          >
            Close
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
