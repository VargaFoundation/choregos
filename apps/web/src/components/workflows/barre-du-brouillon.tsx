// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { Button, ErrorNote } from "@/components/ui";
import { useBrouillon } from "@/components/workflows/brouillon";

/** Ce qui attend d'être publié : le nombre de gestes, le dernier diff, annuler, abandonner, publier. */
export function BarreDuBrouillon() {
  const brouillon = useBrouillon();
  const [message, setMessage] = useState<string | null>(null);
  if (brouillon.enAttente === 0 && !brouillon.erreur && !message) return null;
  return (
    <div className="space-y-2 rounded border border-line border-l-2 border-l-accent bg-surface p-3" data-testid="brouillon">
      {brouillon.enAttente > 0 && (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-sm">
            {brouillon.enAttente} change{brouillon.enAttente > 1 ? "s" : ""} not published yet
            {!brouillon.valide && <span className="text-danger"> — the workflow would be invalid</span>}
          </p>
          <span className="ml-auto inline-flex gap-2">
            <Button size="sm" onClick={() => void brouillon.annuler()} disabled={brouillon.occupe}>
              undo
            </Button>
            <Button size="sm" onClick={brouillon.abandonner} disabled={brouillon.occupe}>
              discard
            </Button>
            <Button
              size="sm"
              tone="primary"
              onClick={() => void brouillon.publier().then(setMessage)}
              disabled={brouillon.occupe || !brouillon.valide}
            >
              publish v{(brouillon.definition.data?.version ?? 0) + 1}
            </Button>
          </span>
        </div>
      )}
      {brouillon.avis.map((avis) => (
        <p key={avis} className="text-sm text-warn">
          {avis}
        </p>
      ))}
      {brouillon.erreurs.map((issue, index) => (
        <ErrorNote key={index}>{issue.message}</ErrorNote>
      ))}
      {brouillon.dernierDiff && (
        <details>
          <summary className="cursor-pointer text-xs text-ink-muted">last change</summary>
          <pre className="mt-1 whitespace-pre-wrap break-all text-xs" data-testid="dernier-diff">
            {brouillon.dernierDiff}
          </pre>
        </details>
      )}
      {brouillon.erreur && <ErrorNote>{brouillon.erreur}</ErrorNote>}
      {message && (
        <p className="text-sm" role="status">
          {message}
        </p>
      )}
    </div>
  );
}
