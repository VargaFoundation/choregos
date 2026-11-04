// SPDX-License-Identifier: Apache-2.0
"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Button, ErrorNote, Input, EngineSentence } from "@/components/ui";

type Ton = "default" | "primary" | "accent" | "danger";

export interface ProprietesDeConfirmation {
  /** What the gesture will do, said before it happens: what goes out, what does not come back. */
  question: ReactNode;
  /** The label of the button that runs it: the verb, not "OK". */
  confirmer: string;
  ton?: Ton;
  /** A reason to type (abandoning a train); `requise` forbids running without it. */
  reason?: { label: string; required?: boolean };
  /** The gesture itself. An error stays displayed here, and the confirmation stays open. */
  action: (reason: string) => Promise<unknown>;
  onDone?: () => void;
  onAnnuler: () => void;
}

/**
 * The confirmation of a gesture that cannot be undone, in the page, next to the button (S23-07). Before,
 * revoking a token, stopping a ticket or approving a production release left at the first click,
 * without a word if the API refused; the trains went through `confirm()` and `prompt()`, which block
 * the tab and cannot be styled. The focus goes to the first field, Escape cancels and hands back control.
 */
export function Confirmation({ question, confirmer, ton = "danger", reason, action, onDone, onAnnuler }: ProprietesDeConfirmation) {
  const id = useId();
  const zone = useRef<HTMLDivElement>(null);
  const [texte, setTexte] = useState("");
  const [occupe, setOccupe] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  useEffect(() => {
    zone.current?.querySelector<HTMLElement>("input, button")?.focus();
  }, []);

  // Escape cancels, as long as the focus is in the confirmation (and nothing is in flight).
  const annuler = useRef(onAnnuler);
  useEffect(() => {
    annuler.current = onAnnuler;
  });
  useEffect(() => {
    const element = zone.current;
    if (!element || occupe) return;
    function surTouche(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      annuler.current();
    }
    element.addEventListener("keydown", surTouche);
    return () => element.removeEventListener("keydown", surTouche);
  }, [occupe]);

  async function run() {
    setOccupe(true);
    setErreur(null);
    try {
      await action(texte.trim());
      onDone?.();
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "refused");
      setOccupe(false);
    }
  }

  const blocked = occupe || Boolean(reason?.required && !texte.trim());
  return (
    <div
      ref={zone}
      role="group"
      aria-labelledby={`${id}-question`}
      className="space-y-2 rounded border border-line border-l-2 border-l-warn bg-surface p-2 text-sm"
      data-testid="confirmation"
    >
      <p id={`${id}-question`}>{question}</p>
      {reason && (
        <label className="block space-y-1">
          <span className="text-xs text-ink-muted">
            {reason.label}
            {reason.required ? " (required)" : ""}
          </span>
          <Input
            value={texte}
            onChange={(event) => setTexte(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !blocked) void run();
            }}
            className="w-full min-w-0"
          />
        </label>
      )}
      <span className="flex flex-wrap gap-2">
        <Button size="sm" tone={ton} onClick={() => void run()} disabled={blocked}>
          {occupe ? `${confirmer}…` : confirmer}
        </Button>
        <Button size="sm" onClick={onAnnuler} disabled={occupe}>
          Cancel
        </Button>
      </span>
      {erreur && (
        <ErrorNote>
          <EngineSentence text={erreur} />
        </ErrorNote>
      )}
    </div>
  );
}

/** A button whose gesture is confirmed: it opens as a confirmation, and closes back into it. */
export function ConfirmedGesture({
  children,
  tonDuBouton = "default",
  disabled,
  ...confirmation
}: Omit<ProprietesDeConfirmation, "onAnnuler"> & {
  children: ReactNode;
  tonDuBouton?: Ton;
  disabled?: boolean;
}) {
  const [ouvert, setOuvert] = useState(false);
  const declencheur = useRef<HTMLSpanElement>(null);
  // Cancelling gives the focus back to the button: without it, it falls back to the top of the page.
  const restoreFocus = useRef(false);

  useEffect(() => {
    if (ouvert || !restoreFocus.current) return;
    restoreFocus.current = false;
    declencheur.current?.querySelector("button")?.focus();
  }, [ouvert]);

  if (ouvert) {
    return (
      <Confirmation
        {...confirmation}
        onDone={() => {
          setOuvert(false);
          confirmation.onDone?.();
        }}
        onAnnuler={() => {
          restoreFocus.current = true;
          setOuvert(false);
        }}
      />
    );
  }
  return (
    <span ref={declencheur} className="inline-flex">
      <Button size="sm" tone={tonDuBouton} onClick={() => setOuvert(true)} disabled={disabled}>
        {children}
      </Button>
    </span>
  );
}
