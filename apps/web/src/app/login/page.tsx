// SPDX-License-Identifier: Apache-2.0
"use client";

import { buttonClasses, Heading, Lead } from "@/components/ui";
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
          sign in
        </Heading>
        <Lead>A ticket goes in, a controlled production release comes out. But first, who are you?</Lead>
      </div>
      {IS_MOCK ? (
        <p className="text-sm text-ink-muted">Demo mode: the session is simulated, nothing to do here.</p>
      ) : (
        <a href={api.loginUrl(next)} className={buttonClasses("primary", "md")}>
          continue with the identity provider
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
          <p className="text-xs text-ink-muted">development login — this bench has no IdP</p>
          <input
            aria-label="development e-mail"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="admin@varga.dev"
            className="w-full rounded border border-line bg-surface px-2 py-1.5"
          />
          <button type="submit" className={buttonClasses("secondary", "sm")}>
            enter
          </button>
        </form>
      )}
    </div>
  );
}
