// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { Button, ErrorNote } from "@/components/ui";
import { useBrouillon } from "@/components/workflows/brouillon";

/**
 * Ce qui attend d'être publié, quelle que soit la vue où on l'a fait (S21-06) : le nombre de gestes,
 * l'état de la validation, le dernier diff, annuler, abandonner, publier — un seul bouton pour les
 * trois vues. Si une autre version est passée pendant l'édition, la barre le dit et laisse choisir.
 */
export function BarreDuBrouillon() {
  const brouillon = useBrouillon();
  const [message, setMessage] = useState<string | null>(null);
  if (brouillon.enAttente === 0 && !brouillon.erreur && !message) return null;
  const { conflit, validation } = brouillon;
  return (
    <div className="space-y-2 rounded border border-line border-l-2 border-l-accent bg-surface p-3" data-testid="brouillon">
      {brouillon.enAttente > 0 && (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-sm">
            {brouillon.enAttente} change{brouillon.enAttente > 1 ? "s" : ""} not published yet
            {validation.etat === "en_cours" && <span className="text-ink-muted"> — validating…</span>}
            {validation.etat === "a_jour" && !brouillon.valide && (
              <span className="text-danger"> — the workflow would be invalid</span>
            )}
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
              disabled={!brouillon.publiable || Boolean(conflit)}
            >
              publish v{(brouillon.definition.data?.version ?? 0) + 1}
            </Button>
          </span>
        </div>
      )}
      {validation.etat === "injoignable" && (
        <ErrorNote>
          could not validate the text: {validation.message} — publishing waits for a verdict.{" "}
          <button type="button" className="underline" onClick={brouillon.revalider}>
            retry
          </button>
        </ErrorNote>
      )}
      {conflit && (
        <div className="space-y-2 rounded border border-warn p-2 text-sm" role="alert" data-testid="conflit">
          <p>
            someone published a newer version meanwhile: you started from v{conflit.versionLue}, the workflow is now
            v{conflit.versionPubliee}.
          </p>
          <span className="inline-flex gap-2">
            <Button size="sm" onClick={() => void brouillon.recharger()} disabled={brouillon.occupe}>
              reload v{conflit.versionPubliee} (discard my changes)
            </Button>
            <Button
              size="sm"
              tone="danger"
              onClick={() => void brouillon.ecraser().then(setMessage)}
              disabled={brouillon.occupe || !brouillon.valide}
            >
              publish over v{conflit.versionPubliee}
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
