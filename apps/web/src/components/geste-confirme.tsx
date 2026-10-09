// SPDX-License-Identifier: Apache-2.0
"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Button, ErrorNote } from "@/components/ui";

type Ton = "default" | "primary" | "accent" | "danger";

export interface ProprietesDeConfirmation {
  /** Ce que le geste va faire, dit avant qu'il se fasse : ce qui part, ce qui ne revient pas. */
  question: ReactNode;
  /** Le libellé du bouton qui exécute : le verbe, pas « OK ». */
  confirmer: string;
  ton?: Ton;
  /** Une raison à saisir (abandonner un train) ; `requise` interdit d'exécuter sans elle. */
  raison?: { label: string; requise?: boolean };
  /** Le geste lui-même. Une erreur reste affichée ici, et la confirmation reste ouverte. */
  action: (raison: string) => Promise<unknown>;
  onFait?: () => void;
  onAnnuler: () => void;
}

/**
 * La confirmation d'un geste qu'on ne défait pas, dans la page, à côté du bouton (S23-07). Avant,
 * révoquer un jeton, arrêter un ticket ou approuver une mise en production partaient au premier clic,
 * sans un mot si l'API refusait ; les trains passaient par `confirm()` et `prompt()`, qui bloquent
 * l'onglet et ne se stylent pas. Le focus va au premier champ, Échap annule et rend la main.
 */
export function Confirmation({ question, confirmer, ton = "danger", raison, action, onFait, onAnnuler }: ProprietesDeConfirmation) {
  const id = useId();
  const zone = useRef<HTMLDivElement>(null);
  const [texte, setTexte] = useState("");
  const [occupe, setOccupe] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  useEffect(() => {
    zone.current?.querySelector<HTMLElement>("input, button")?.focus();
  }, []);

  // Échap annule, tant que le focus est dans la confirmation (et que rien n'est en vol).
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

  async function executer() {
    setOccupe(true);
    setErreur(null);
    try {
      await action(texte.trim());
      onFait?.();
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "refused");
      setOccupe(false);
    }
  }

  const bloque = occupe || Boolean(raison?.requise && !texte.trim());
  return (
    <div
      ref={zone}
      role="group"
      aria-labelledby={`${id}-question`}
      className="space-y-2 rounded border border-line border-l-2 border-l-warn bg-surface p-2 text-sm"
      data-testid="confirmation"
    >
      <p id={`${id}-question`}>{question}</p>
      {raison && (
        <label className="block space-y-1">
          <span className="text-xs text-ink-muted">
            {raison.label}
            {raison.requise ? " (required)" : ""}
          </span>
          <input
            value={texte}
            onChange={(event) => setTexte(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !bloque) void executer();
            }}
            className="w-full min-w-0 rounded border border-line bg-surface px-2 py-1"
          />
        </label>
      )}
      <span className="flex flex-wrap gap-2">
        <Button size="sm" tone={ton} onClick={() => void executer()} disabled={bloque}>
          {occupe ? `${confirmer}…` : confirmer}
        </Button>
        <Button size="sm" onClick={onAnnuler} disabled={occupe}>
          cancel
        </Button>
      </span>
      {erreur && <ErrorNote>{erreur}</ErrorNote>}
    </div>
  );
}

/** Un bouton dont le geste se confirme : il s'ouvre en confirmation, et s'y referme. */
export function GesteConfirme({
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
  // Annuler rend le focus au bouton : sans lui, il retombe en haut de la page.
  const rendreLeFocus = useRef(false);

  useEffect(() => {
    if (ouvert || !rendreLeFocus.current) return;
    rendreLeFocus.current = false;
    declencheur.current?.querySelector("button")?.focus();
  }, [ouvert]);

  if (ouvert) {
    return (
      <Confirmation
        {...confirmation}
        onFait={() => {
          setOuvert(false);
          confirmation.onFait?.();
        }}
        onAnnuler={() => {
          rendreLeFocus.current = true;
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
