// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, ErrorNote, Input } from "@/components/ui";
import { api } from "@/lib/api";
import type { Action } from "@/lib/types";

/**
 * Deciding on an action (ADR 0035). Approving requires a recent authentication: the API answers
 * `step_up_required` beyond that, and the console goes through the IdP again then comes back here. Rejecting says
 * why: the button stays closed until there is a reason.
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
          Approve
        </Button>
        <label className="flex-1 space-y-1">
          <span className="text-xs text-ink-muted">Reason to reject</span>
          <Input
            className="w-full"
            value={raison}
            onChange={(event) => setRaison(event.target.value)}
          />
        </label>
        <Button tone="danger" onClick={() => void decider("reject")} disabled={occupe || !raison.trim()}>
          Reject
        </Button>
      </div>
      <p className="text-xs text-ink-muted" data-testid="regles-de-decision">
        {/* Without `step_up_minutes`, the rule does not require a recent authentication (S20-08): do not
                promise a detour through the IdP that will not happen. */}
        {action.approval?.step_up_minutes != null
          ? `Approving needs a recent sign-in (within ${String(action.approval.step_up_minutes)} min). `
          : ""}
        Whoever proposed it, or owns the agent that did, cannot decide.
      </p>
      {erreur && <ErrorNote>{erreur}</ErrorNote>}
    </div>
  );
}
