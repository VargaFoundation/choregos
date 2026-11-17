// SPDX-License-Identifier: Apache-2.0
"use client";

import { Download } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui";

/**
 * Download a memory as an Open Knowledge Format bundle (S72-04, ADR 0174 point 7): one markdown file per
 * active fact, `verified` where a person accepted it. The API refuses (409) a memory its backend cannot
 * list apart from the others (Ecphoria): the refusal is shown as it is said.
 */
export function MemoryExport({ bundle, filename }: { bundle: () => Promise<Blob>; filename: string }) {
  const [error, setError] = useState<string | null>(null);
  async function download() {
    setError(null);
    try {
      const blob = await bundle();
      const link = window.document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = filename;
      link.click();
      URL.revokeObjectURL(link.href);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    }
  }
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <Button onClick={() => void download()} title="The memory's active facts as an Open Knowledge Format bundle: one markdown file per fact">
        <Download aria-hidden className="size-3.5" strokeWidth={2} />
        Download (OKF)
      </Button>
      {error && (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
