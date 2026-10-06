// SPDX-License-Identifier: Apache-2.0
/** Barre de décision : approuver, renvoyer, répondre, faire une tâche — depuis n'importe quel écran. */
"use client";

import { useId, useState } from "react";
import { api } from "@/lib/api";
import type { HumanRequest } from "@/lib/types";
import { champsManquants, SchemaForm, type JsonSchema } from "./schema-form";
import { Button, ErrorNote } from "./ui";

export function DecisionBar({
  itemId,
  kind = "approval",
  request,
  onDone,
}: {
  itemId: string;
  kind?: string;
  /** La demande en attente : une tâche y porte son formulaire et ce qu'elle fait attester. */
  request?: Pick<HumanRequest, "payload">;
  onDone?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState("");

  async function send(decision: "approve" | "reject" | "answer") {
    setBusy(true);
    setError(null);
    try {
      await api.decide(itemId, {
        kind: decision,
        ...(decision === "answer" ? { answer } : {}),
        ...(decision === "reject" ? { reason: answer || "sent back" } : {}),
      });
      onDone?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "decision refused");
    } finally {
      setBusy(false);
    }
  }

  if (kind === "task") {
    return <TaskForm itemId={itemId} payload={request?.payload ?? {}} onDone={onDone} />;
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {kind === "question" ? (
        <>
          <input
            aria-label="answer"
            value={answer}
            onChange={(event) => setAnswer(event.target.value)}
            placeholder="your answer…"
            className="min-w-64 flex-1 rounded border border-line bg-surface px-2 py-1.5 text-sm"
          />
          <Button tone="primary" disabled={busy || !answer} onClick={() => send("answer")}>
            answer
          </Button>
        </>
      ) : (
        <>
          <Button tone="primary" disabled={busy} onClick={() => send("approve")}>
            approve
          </Button>
          <input
            aria-label="reason for sending back"
            value={answer}
            onChange={(event) => setAnswer(event.target.value)}
            placeholder="reason (if sent back)"
            className="min-w-48 rounded border border-line bg-surface px-2 py-1.5 text-sm"
          />
          <Button tone="danger" disabled={busy} onClick={() => send("reject")}>
            send back
          </Button>
        </>
      )}
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}

/**
 * Une tâche (S20-06) : ce qu'il faut faire, le formulaire dont les valeurs deviennent des champs du
 * ticket, et la phrase à attester, mot pour mot. « done » reste fermé tant qu'un champ requis manque
 * ou que l'attestation n'est pas cochée ; l'API reste juge, et le dit si elle refuse.
 */
function TaskForm({
  itemId,
  payload,
  onDone,
}: {
  itemId: string;
  payload: Record<string, unknown>;
  onDone?: () => void;
}) {
  const base = useId();
  const formulaire = (payload.form ?? {}) as JsonSchema;
  const attest = typeof payload.attest === "string" ? payload.attest : null;
  const instructions = typeof payload.instructions === "string" ? payload.instructions : null;
  const [valeurs, setValeurs] = useState<Record<string, unknown>>({});
  const [atteste, setAtteste] = useState(false);
  const [motif, setMotif] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const manquants = champsManquants(formulaire, valeurs);

  async function send(body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      await api.decide(itemId, body);
      onDone?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "task refused");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      {instructions && <p className="text-sm">{instructions}</p>}
      <SchemaForm schema={formulaire} value={valeurs} onChange={setValeurs} disabled={busy} />
      {attest && (
        <label htmlFor={`${base}-atteste`} className="flex items-start gap-2 text-sm">
          <input
            id={`${base}-atteste`}
            type="checkbox"
            checked={atteste}
            onChange={(event) => setAtteste(event.target.checked)}
            disabled={busy}
          />
          <span>I attest: {attest}</span>
        </label>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          tone="primary"
          disabled={busy || manquants.length > 0 || (attest !== null && !atteste)}
          onClick={() => send({ kind: "complete", values: valeurs, attested: atteste })}
        >
          done
        </Button>
        <input
          aria-label="why it cannot be done"
          value={motif}
          onChange={(event) => setMotif(event.target.value)}
          placeholder="why it cannot be done"
          className="min-w-48 rounded border border-line bg-surface px-2 py-1.5 text-sm"
        />
        <Button tone="danger" disabled={busy || !motif} onClick={() => send({ kind: "reject", reason: motif })}>
          cannot do it
        </Button>
      </div>
      {manquants.length > 0 && <p className="text-xs text-ink-muted">to fill: {manquants.join(", ")}</p>}
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}
