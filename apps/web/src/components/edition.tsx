// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { Info } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useServedTheme } from "@/components/served-theme";
import { buttonClasses, Card, Drawer, EtatDeLecture } from "@/components/ui";
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
          : "Community edition: the open-source platform"
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
export function EditionFooterLine({ productName }: { productName?: string }) {
  // The name follows the theme the client holds, the organisation's once it is swapped in (S42-13).
  const served = useServedTheme().product_name;
  productName = productName ?? served;
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

/** The footer's own sentence: the theme's, which follows the organisation once it is swapped in (S42-13). */
export const DEFAULT_FOOTER_TEXT = "A ticket goes in, a controlled production release comes out.";

export function FooterText() {
  return <span data-testid="footer-text">{useServedTheme().footer?.text ?? DEFAULT_FOOTER_TEXT}</span>;
}

/** Where the core's source and documentation live: the foundation's repository (ADR 0048). */
export const SOURCE_REPOSITORY = "https://github.com/VargaFoundation/choregos";
export const DOCUMENTATION = `${SOURCE_REPOSITORY}/tree/main/docs`;

/** The plain text "Copy for support" puts on the clipboard: what a support request needs, nothing more. */
export function aboutSupportText(productName: string, edition: Edition): string {
  const enterprise = edition.edition === "enterprise";
  return [
    `Product: ${productName}`,
    `Core version: ${edition.version}`,
    `Edition: ${enterprise ? "enterprise" : "community"}`,
    ...(enterprise && edition.edition_version ? [`Edition version: ${edition.edition_version}`] : []),
    `Revision: ${edition.build?.revision ?? "unknown"}`,
  ].join("\n");
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[9rem_1fr] gap-3 py-2 text-sm">
      <dt className="text-ink-muted">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

/**
 * The About panel: what is deployed (product, core version and licence, edition, who publishes it,
 * the build revision) and where to read more. Everything comes from `GET /edition` and the served
 * theme; nothing here names an edition or a publisher itself, the plugin announces them.
 */
export function AboutPanel() {
  const productName = useServedTheme().product_name;
  const edition = useQuery({ queryKey: ["edition"], queryFn: () => api.edition(), staleTime: Infinity, retry: false });
  const [copied, setCopied] = useState<"yes" | "no" | null>(null);
  const data = edition.data;
  if (!data) return <EtatDeLecture lecture={edition} quoi="the version information" />;
  const enterprise = data.edition === "enterprise";

  async function copy(texte: string) {
    try {
      await navigator.clipboard.writeText(texte);
      setCopied("yes");
    } catch {
      setCopied("no");
    }
  }

  return (
    <div className="space-y-4">
      <dl className="divide-y divide-line" aria-label="Deployed version">
        <Row label="Product">{productName}</Row>
        <Row label="Core">
          Choregos <span className="font-mono">{data.version}</span>
          {" · "}
          <span data-testid="about-core-licence">{data.licence ?? "Apache-2.0"}</span> licence
        </Row>
        <Row label="Edition">{enterprise ? "Enterprise edition" : "Community edition"}</Row>
        {enterprise && data.edition_version ? (
          <Row label="Edition version">
            <span className="font-mono">{data.edition_version}</span>
          </Row>
        ) : null}
        {enterprise && data.publisher ? <Row label="Publisher">{data.publisher}</Row> : null}
        {enterprise && data.edition_licence ? <Row label="Edition licence">{data.edition_licence}</Row> : null}
        <Row label="Build revision">
          {data.build?.revision ? (
            <span className="font-mono">{data.build.revision}</span>
          ) : (
            <span className="text-ink-muted">not recorded</span>
          )}
        </Row>
      </dl>
      <ul className="space-y-1 text-sm" aria-label="Links">
        <li>
          <a href={SOURCE_REPOSITORY} target="_blank" rel="noreferrer" className="underline">
            Source repository
          </a>
        </li>
        <li>
          <a href={DOCUMENTATION} target="_blank" rel="noreferrer" className="underline">
            Documentation
          </a>
        </li>
      </ul>
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => void copy(aboutSupportText(productName, data))}
          className={buttonClasses("secondary", "sm")}
        >
          Copy for support
        </button>
        <span role="status" className="text-xs text-ink-muted">
          {copied === "yes" ? "Copied." : copied === "no" ? "Could not copy: select the values above instead." : ""}
        </span>
      </div>
    </div>
  );
}

/**
 * The "About" entry of the sidebar and of the mobile menu: a button that opens the panel in a drawer.
 * `rail` folds the sidebar variant to its icon (the label stays for a screen reader and a tooltip).
 */
export function AboutEntry({ className = "", rail = false }: { className?: string; rail?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        aria-label="About"
        aria-haspopup="dialog"
        title="About"
        onClick={() => {
          setOpen(true);
        }}
        className={className}
      >
        <Info aria-hidden className="size-4 shrink-0" strokeWidth={1.75} />
        <span className={rail ? "rail:hidden" : undefined}>About</span>
      </button>
      <Drawer open={open} onClose={() => setOpen(false)} title="About">
        <AboutPanel />
      </Drawer>
    </>
  );
}
