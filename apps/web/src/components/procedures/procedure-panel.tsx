// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import { MarkdownView } from "@/components/markdown-view";
import { buttonClasses } from "@/components/ui";
import { api } from "@/lib/api";
import {
  allowedLinks,
  AUDIENCE,
  labelOfValue,
  markSeen,
  wasSeen,
  type Procedure,
} from "@/lib/procedures";

/**
 * One panel open at a time on a page: opening one closes another (a form with a connector and a
 * source procedure would otherwise stack two). A tiny store, because the panels do not share a parent.
 */
let activeKey: string | null = null;
const listeners = new Set<() => void>();
function setActive(key: string | null) {
  activeKey = key;
  for (const listener of listeners) listener();
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => void listeners.delete(listener);
}

/**
 * The setup procedure for what a form adds (ADR 0067): a "How to set this up" button, and a panel
 * beside the form with what to create at the vendor, which permissions to give, where to paste each
 * value, how to test it and what the usual failures mean — this platform's own URLs filled in by the
 * API. It opens by itself the first time a person meets a given procedure (the seen flag is per user
 * and per procedure, kept in the browser; nothing is stored on the server) and stays reachable from
 * the button. Where no procedure exists, or the API cannot be reached, it renders nothing: a missing
 * help never blocks the form.
 */
export function ProcedurePanel({
  target,
  cardHost,
  preset,
  procedure,
  label = "How to set this up",
  autoOpen = true,
  inline = false,
}: {
  /** What the form adds: `connector:documents/sharepoint`, `source:web`, `agent:remote.a2a`. */
  target: string;
  /** The host of an agent card: selects a preset (`a2a.atlassian.com`) before the generic procedure. */
  cardHost?: string;
  /** A catalogue preset (`atlassian-rovo`). */
  preset?: string;
  /** A procedure the caller already knows by id (a catalogue entry names its own): skips the lookup. */
  procedure?: string;
  label?: string;
  autoOpen?: boolean;
  /** Draw the procedure in the page's flow under the button, not as a panel beside it: for a modal drawer. */
  inline?: boolean;
}) {
  const key = useId();
  const found = useQuery({
    queryKey: ["procedure-for", target, cardHost ?? "", preset ?? ""],
    queryFn: () => api.listProcedures({ for: target, card_host: cardHost, preset }),
    enabled: !procedure && Boolean(target),
    staleTime: Infinity,
    retry: false,
  });
  const id = procedure ?? found.data?.[0]?.id;
  const detail = useQuery({
    queryKey: ["procedure", id],
    queryFn: () => api.getProcedure(id!),
    enabled: Boolean(id),
    staleTime: Infinity,
    retry: false,
  });
  const open = useSyncExternalStore(
    subscribe,
    () => activeKey === key,
    () => false,
  );
  const loadedId = detail.data?.id;
  useEffect(() => {
    if (!autoOpen || !loadedId || wasSeen(loadedId)) return;
    markSeen(loadedId);
    setActive(key);
  }, [autoOpen, loadedId, key]);
  // The panel lies over the page and never reflows it: opening help must not move the form being
  // filled in. Escape closes it, as it would a drawer.
  useEffect(() => {
    if (!open || inline) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setActive(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, inline]);
  useEffect(() => () => setActiveIfMine(key), [key]);
  if (!detail.data) return null;
  return (
    <>
      <button
        type="button"
        aria-expanded={open}
        data-testid="procedure-open"
        onClick={() => setActive(open ? null : key)}
        className={buttonClasses("secondary", "sm")}
      >
        {label}
      </button>
      {open && <Panel procedure={detail.data} onClose={() => setActive(null)} inline={inline} />}
    </>
  );
}

function setActiveIfMine(key: string) {
  if (activeKey === key) setActive(null);
}

function Panel({ procedure, onClose, inline }: { procedure: Procedure; onClose: () => void; inline: boolean }) {
  const ref = useRef<HTMLElement>(null);
  const titleId = useId();
  useEffect(() => {
    ref.current?.scrollTo?.({ top: 0 });
  }, [procedure.id]);
  const links = allowedLinks(procedure);
  const values = Object.entries(procedure.values);
  const fields = Object.entries(procedure.fields);
  return (
    <aside
      ref={ref}
      aria-labelledby={titleId}
      data-testid="procedure-panel"
      className={
        inline
          ? "mt-3 basis-full border border-line p-3 text-sm"
          : "raised fixed inset-x-0 bottom-0 z-40 max-h-[75dvh] overflow-y-auto border-t border-line p-4 text-sm shadow-raised xl:inset-y-0 xl:left-auto xl:right-0 xl:max-h-none xl:w-[26rem] xl:border-l xl:border-t-0"
      }
    >
      <div className="space-y-4 break-words">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <h2 id={titleId} className="text-base font-semibold text-ink">
              {procedure.title}
            </h2>
            <p className="text-xs text-ink-muted">
              {AUDIENCE[procedure.audience]} · version {procedure.version} · reviewed {procedure.reviewed}
            </p>
          </div>
          <button type="button" onClick={onClose} data-testid="procedure-close" className={buttonClasses("ghost", "sm")}>
            Close
          </button>
        </div>
        {values.length > 0 && (
          <div className="space-y-2" data-testid="procedure-values">
            <h3 className="font-semibold text-ink">Values for this platform</h3>
            {values.map(([name, value]) => (
              <CopyBox key={name} name={name} value={value} />
            ))}
          </div>
        )}
        {procedure.sections.map((section) => (
          <section key={section.key} data-testid={`procedure-section-${section.key}`} className="space-y-1">
            <h3 className="font-semibold text-ink">{section.title}</h3>
            <MarkdownView text={section.markdown} links={links} />
          </section>
        ))}
        {fields.length > 0 && (
          <section data-testid="procedure-fields" className="space-y-1">
            <h3 className="font-semibold text-ink">What goes in each field</h3>
            <dl className="space-y-1">
              {fields.map(([name, text]) => (
                <div key={name}>
                  <dt>
                    <code className="font-mono text-[0.92em]">{name}</code>
                  </dt>
                  <dd className="text-ink-muted">{text}</dd>
                </div>
              ))}
            </dl>
          </section>
        )}
        {procedure.egress.length > 0 && (
          <section data-testid="procedure-egress" className="space-y-1">
            <h3 className="font-semibold text-ink">Network access</h3>
            <p className="text-ink-muted">The platform must be allowed to reach:</p>
            <ul className="list-disc pl-5">
              {procedure.egress.map((host) => (
                <li key={host}>
                  <code className="font-mono text-[0.92em]">{host}</code>
                </li>
              ))}
            </ul>
          </section>
        )}
        {procedure.link && (
          <p>
            <Link href={procedure.link} className="underline">
              Open the full page
            </Link>
          </p>
        )}
        {procedure.vendor_docs.length > 0 && (
          <section data-testid="procedure-vendor-docs" className="space-y-1">
            <h3 className="font-semibold text-ink">Vendor documentation</h3>
            <ul className="list-disc pl-5">
              {procedure.vendor_docs.map((doc) => (
                <li key={doc.url}>
                  <a href={doc.url} target="_blank" rel="noopener noreferrer" className="underline">
                    {doc.title}
                  </a>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </aside>
  );
}

/** A value to copy exactly: this platform's callback or webhook address, not a placeholder. */
function CopyBox({ name, value }: { name: string; value: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }
  return (
    <div className="space-y-1" data-testid={`procedure-value-${name}`}>
      <p className="text-xs text-ink-muted">{labelOfValue(name)}</p>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 overflow-x-auto border border-line px-2 py-1 font-mono text-xs">{value}</code>
        <button
          type="button"
          onClick={() => void copy()}
          aria-label={`Copy ${labelOfValue(name)}`}
          className={buttonClasses("secondary", "sm")}
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}
