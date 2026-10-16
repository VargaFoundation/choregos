// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { ErrorNote, Heading, Lead } from "@/components/ui";

/** One section of the settings page: an anchor, a heading, one sentence of what it is for. */
export function SettingsSection({
  id,
  title,
  lead,
  children,
}: {
  id: string;
  title: string;
  lead: ReactNode;
  children: ReactNode;
}) {
  return (
    <section id={id} aria-labelledby={`${id}-titre`} className="scroll-mt-4 space-y-3" data-testid={`section-${id}`}>
      <div className="space-y-1">
        <Heading as="h2" size="md" id={`${id}-titre`}>
          {title}
        </Heading>
        <Lead>{lead}</Lead>
      </div>
      {children}
    </section>
  );
}

/**
 * A write with its own message and error, per section: a refusal in the guardrails does not print
 * above the connectors (the page used to share one message for everything).
 */
export function useSectionWrites(slug: string) {
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function faire(action: () => Promise<unknown>, succes: string, cles: string[]) {
    setError(null);
    setMessage(null);
    try {
      await action();
      setMessage(succes);
      for (const cle of cles) void queryClient.invalidateQueries({ queryKey: [cle, slug] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    }
  }
  const notice = (
    <>
      {message && (
        <p className="text-sm text-ok" role="status">
          {message}
        </p>
      )}
      {error && <ErrorNote>{error}</ErrorNote>}
    </>
  );
  return { faire, notice };
}
