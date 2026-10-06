// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import type { Action } from "@/lib/types";

/**
 * Décider d'une action (ADR 0035). Approuver demande une authentification récente : l'API répond
 * `step_up_required` au-delà, et la console repasse par l'IdP puis revient ici. Rejeter dit
 * pourquoi — le bouton reste fermé tant qu'il n'y a pas de raison.
 */
export function DecisionDAction({ projet, action }: { projet: string; action: Action }) {
  const client = useQueryClient();
  const [raison, setRaison] = useState("");
  const [erreur, setErreur] = useState<string | null>(null);
  const [occupe, setOccupe] = useState(false);
  if (action.status !== "pending_approval") return null;
  async function decider(decision: "approve" | "reject") {
    setErreur(null);
    setOccupe(true);
    try {
      await api.decideAction(projet, action.id, { decision, ...(decision === "reject" ? { reason: raison } : {}) });
      await client.invalidateQueries({ queryKey: ["action", projet, action.id] });
      await client.invalidateQueries({ queryKey: ["actions", projet] });
      await client.invalidateQueries({ queryKey: ["org-actions"] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "refused");
    } finally {
      setOccupe(false);
    }
  }
  return (
    <div className="space-y-2" data-testid="decision">
      <div className="flex flex-wrap items-end gap-2">
        <Button tone="primary" onClick={() => void decider("approve")} disabled={occupe}>
          approve
        </Button>
        <label className="flex-1 space-y-1">
          <span className="text-xs text-ink-muted">reason to reject</span>
          <input
            className="w-full rounded border border-line bg-surface px-2 py-1.5 text-sm"
            value={raison}
            onChange={(event) => setRaison(event.target.value)}
          />
        </label>
        <Button tone="danger" onClick={() => void decider("reject")} disabled={occupe || !raison.trim()}>
          reject
        </Button>
      </div>
      <p className="text-xs text-ink-muted" data-testid="regles-de-decision">
        {/* Sans `step_up_minutes`, la règle n'exige pas d'authentification récente (S20-08) : ne pas
            promettre un détour par l'IdP qui n'aura pas lieu. */}
        {action.approval?.step_up_minutes != null
          ? `Approving needs a recent sign-in (within ${String(action.approval.step_up_minutes)} min). `
          : ""}
        Whoever proposed it, or owns the agent that did, cannot decide.
      </p>
      {erreur && <ErrorNote>{erreur}</ErrorNote>}
    </div>
  );
}
