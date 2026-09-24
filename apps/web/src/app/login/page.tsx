"use client";

import { Heading, Lead, buttonClasses } from "@varga/design-system";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, IS_MOCK } from "@/lib/api";

/**
 * La page de connexion. Le front n'en avait pas : un visiteur non connecté voyait une
 * erreur brute sur chaque page. Ici : un bouton vers l'IdP (l'API fait la redirection,
 * PKCE et `state` compris), et, quand le déploiement l'autorise, la connexion de
 * développement par e-mail — jamais en production, l'API la refuse d'elle-même.
 */
export default function LoginPage() {
  return (
    <Suspense>
      <Connexion />
    </Suspense>
  );
}

function Connexion() {
  const params = useSearchParams();
  const next = params.get("next") ?? "/";
  const [email, setEmail] = useState("");
  const devLogin = process.env.NEXT_PUBLIC_DEV_LOGIN === "true";

  return (
    <div className="mx-auto max-w-md space-y-8 py-12">
      <div className="space-y-3">
        <Heading as="h1" size="xl">
          se connecter
        </Heading>
        <Lead>un ticket entre, une mise en production maîtrisée sort. mais d&apos;abord, qui êtes-vous ?</Lead>
      </div>
      {IS_MOCK ? (
        <p className="text-sm text-ink-muted">mode démo : la session est simulée, rien à faire ici.</p>
      ) : (
        <a href={api.loginUrl(next)} className={buttonClasses("primary", "md")}>
          continuer avec le fournisseur d&apos;identité
        </a>
      )}
      {devLogin && !IS_MOCK && (
        <form
          className="space-y-2 border-t border-line pt-6 text-sm"
          onSubmit={(event) => {
            event.preventDefault();
            if (email) window.location.assign(api.loginUrl(next, email));
          }}
        >
          <p className="text-xs text-ink-muted">connexion de développement — ce banc n&apos;a pas d&apos;IdP</p>
          <input
            aria-label="e-mail de développement"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="admin@varga.dev"
            className="w-full rounded border border-line bg-surface px-2 py-1.5"
          />
          <button type="submit" className={buttonClasses("secondary", "sm")}>
            entrer
          </button>
        </form>
      )}
    </div>
  );
}
