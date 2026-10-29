// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { GesteConfirme } from "@/components/geste-confirme";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";

/**
 * Re-import the project memory (S47-17): the project's memory ingestion reads its sources again now,
 * instead of at its next cycle. The API answers 202 with no body: the import is scheduled, not done,
 * so what the gesture can say is when it was scheduled; the facts it finds reach the memory as the
 * ingestion runs. The page shows it only to those who may write the project's memory.
 */
export function MemoryReimport({ slug }: { slug: string }) {
  const [scheduledAt, setScheduledAt] = useState<string | null>(null);
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <GesteConfirme
        question="Re-import the project memory? Its ingestion reads the project's sources again now rather than at its next cycle; what it learns arrives in the memory as it runs."
        confirmer="Re-import"
        ton="accent"
        action={() => api.reimportMemory(slug)}
        onFait={() => setScheduledAt(new Date().toISOString())}
      >
        Re-import the project memory
      </GesteConfirme>
      {scheduledAt && (
        <p role="status" className="text-xs text-ink-muted">
          Re-import scheduled {shortDate(scheduledAt)}. New facts appear in the search as the ingestion reads them.
        </p>
      )}
    </div>
  );
}
