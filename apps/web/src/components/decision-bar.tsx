/** Barre de décision : approuver, renvoyer, répondre — depuis n'importe quel écran. */
"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Button, ErrorNote } from "./ui";

export function DecisionBar({
  itemId,
  kind = "approval",
  onDone,
}: {
  itemId: string;
  kind?: string;
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
        ...(decision === "reject" ? { reason: answer || "renvoyé" } : {}),
      });
      onDone?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "décision refusée");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {kind === "question" ? (
        <>
          <input
            aria-label="réponse"
            value={answer}
            onChange={(event) => setAnswer(event.target.value)}
            placeholder="votre réponse…"
            className="min-w-64 flex-1 rounded border border-line bg-surface px-2 py-1.5 text-sm"
          />
          <Button tone="primary" disabled={busy || !answer} onClick={() => send("answer")}>
            répondre
          </Button>
        </>
      ) : (
        <>
          <Button tone="primary" disabled={busy} onClick={() => send("approve")}>
            approuver
          </Button>
          <input
            aria-label="motif du renvoi"
            value={answer}
            onChange={(event) => setAnswer(event.target.value)}
            placeholder="motif (si renvoi)"
            className="min-w-48 rounded border border-line bg-surface px-2 py-1.5 text-sm"
          />
          <Button tone="danger" disabled={busy} onClick={() => send("reject")}>
            renvoyer
          </Button>
        </>
      )}
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}
