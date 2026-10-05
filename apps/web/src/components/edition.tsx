// SPDX-License-Identifier: Apache-2.0
"use client";

import { Card } from "@/components/ui";
import type { Edition } from "@/lib/types";

/**
 * Ce que l'édition entreprise ajoute, dit en clair (ADR 0024).
 *
 * Les clés sont celles que l'édition entreprise annonce dans `GET /edition` ; une clé inconnue
 * s'affiche telle quelle plutôt que de disparaître. En communautaire, la même liste dit ce
 * qu'apporterait l'édition entreprise — sans faux écran ni bouton qui mènerait nulle part.
 */
export const FONCTIONS_ENTREPRISE: ReadonlyArray<readonly [string, string]> = [
  ["multi_org", "several organisations on one installation"],
  ["saml", "SAML sign-in, per organisation"],
  ["scim", "SCIM provisioning of users and groups"],
  ["revocation_de_session", "server-side session revocation"],
  ["authentification_fraiche", "a fresh-authentication policy per organisation"],
  ["plafonds", "monthly spend and concurrency caps per organisation"],
  ["quota", "a Kubernetes quota per project namespace"],
  ["suspension", "suspending an organisation"],
  ["export", "exporting an organisation"],
  ["suppression", "deleting an organisation"],
  ["admins_plateforme", "platform administrators"],
];

const LIBELLES = new Map(FONCTIONS_ENTREPRISE);

export function libelleFonction(cle: string): string {
  return LIBELLES.get(cle) ?? cle;
}

/** Le badge de la barre du haut : sur quelle édition tourne la plateforme. */
export function EditionBadge({ edition }: { edition: Edition["edition"] }) {
  return (
    <span
      className="rounded border border-line px-2 py-0.5 text-ink-muted"
      title="ADR 0024 — two editions"
      data-testid="edition-badge"
    >
      {edition === "enterprise" ? "enterprise edition" : "community edition"}
    </span>
  );
}

/** La carte de l'administration : l'édition, sa version, et ce qu'elle s'autorise. */
export function EditionCard({ edition }: { edition: Edition | undefined }) {
  if (!edition) return null;
  const entreprise = edition.edition === "enterprise";
  return (
    <Card title="edition">
      <p className="text-sm">
        {entreprise ? "enterprise edition" : "community edition"}{" "}
        <span className="text-xs text-ink-muted">version {edition.version}</span>
      </p>
      {entreprise ? (
        <ul className="mt-2 space-y-1 text-xs" aria-label="enterprise features">
          {edition.features.map((cle) => (
            <li key={cle}>{libelleFonction(cle)}</li>
          ))}
        </ul>
      ) : (
        <>
          <p className="mt-2 text-xs text-ink-muted">
            this installation runs the community core: one organisation. The enterprise edition adds:
          </p>
          <ul className="mt-1 space-y-1 text-xs" aria-label="what the enterprise edition adds">
            {FONCTIONS_ENTREPRISE.map(([cle, libelle]) => (
              <li key={cle}>{libelle}</li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}
