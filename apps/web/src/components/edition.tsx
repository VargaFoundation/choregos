// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { Card } from "@/components/ui";
import { api } from "@/lib/api";
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
  ["theme", "a custom look per instance and organisation"],
];

const LIBELLES = new Map(FONCTIONS_ENTREPRISE);

export function libelleFonction(cle: string): string {
  return LIBELLES.get(cle) ?? cle;
}

/** Le badge de la barre du haut : sur quelle édition tourne la plateforme. */
export function EditionBadge({
  edition,
  publisher,
}: {
  edition: Edition["edition"];
  publisher?: string | null;
}) {
  return (
    <span
      className="rounded-full border border-line px-2 py-0.5 text-[11px] text-ink-muted"
      title={
        edition === "enterprise" && publisher
          ? `Enterprise edition, published by ${publisher}`
          : "ADR 0024 — two editions"
      }
      data-testid="edition-badge"
    >
      {edition === "enterprise" ? "Enterprise edition" : "Community edition"}
    </span>
  );
}

/** La carte de l'administration : l'édition, sa version, et ce qu'elle s'autorise. */
export function EditionCard({ edition }: { edition: Edition | undefined }) {
  if (!edition) return null;
  const entreprise = edition.edition === "enterprise";
  return (
    <Card title="Edition">
      <p className="text-sm">
        {entreprise ? "Enterprise edition" : "Community edition"}{" "}
        <span className="text-xs text-ink-muted">
          {entreprise && edition.edition_version ? (
            <>
              <span className="font-mono">{edition.edition_version}</span>
              {edition.publisher ? ` · published by ${edition.publisher}` : null}
              {" · core "}
            </>
          ) : (
            "version "
          )}
          <span className="font-mono">{edition.version}</span>
        </span>
      </p>
      {entreprise ? (
        <ul className="mt-2 space-y-1 text-xs" aria-label="Enterprise features">
          {edition.features.map((cle) => (
            <li key={cle}>{libelleFonction(cle)}</li>
          ))}
        </ul>
      ) : (
        <>
          <p className="mt-2 text-xs text-ink-muted">
            This installation runs the community core: one organisation. The enterprise edition adds:
          </p>
          <ul className="mt-1 space-y-1 text-xs" aria-label="What the enterprise edition adds">
            {FONCTIONS_ENTREPRISE.map(([cle, libelle]) => (
              <li key={cle}>{libelle}</li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

/**
 * The footer's attribution line. The community text is the foundation's (ADR 0048) and never
 * changes; an enterprise edition that announces a publisher is named next to it, and the line says
 * it is built on the community core.
 */
export function EditionFooterLine({ productName = "Choregos" }: { productName?: string }) {
  const edition = useQuery({ queryKey: ["edition"], queryFn: () => api.edition(), staleTime: Infinity, retry: false });
  const publisher = edition.data?.edition === "enterprise" ? edition.data.publisher : null;
  // The foundation is named here, and only here (ADR 0048). A theme cannot remove this line; a renamed
  // instance says what it is built on (ADR 0064), an enterprise edition who publishes it (ADR 0024).
  const product = productName === "Choregos" ? "" : `${productName} · `;
  return (
    <span data-testid="footer-foundation">
      {publisher
        ? `${product}Choregos Enterprise edition by ${publisher} · built on Choregos by Varga Foundation (Apache 2.0)`
        : `${productName === "Choregos" ? "Choregos" : `${productName}, built on Choregos`} by Varga Foundation · Apache 2.0`}
    </span>
  );
}
