// SPDX-License-Identifier: Apache-2.0
"use client";

import { buttonClasses, Heading, Input, Lead } from "@/components/ui";
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
  const devLogin = process.env.NEXT_PUBLIC_DEV_LOGIN === "true";
  // The local install (`compose.yaml`) bakes the demo admin and the demo project in: one click on
  // "enter" and the first sign-in lands on a project with a work item, not on an empty page.
  const next = params.get("next") || (devLogin && process.env.NEXT_PUBLIC_DEV_LOGIN_NEXT) || "/";
  const [email, setEmail] = useState(devLogin ? (process.env.NEXT_PUBLIC_DEV_LOGIN_EMAIL ?? "") : "");

  return (
    <div className="mx-auto max-w-md space-y-8 py-12">
      <div className="space-y-3">
        <Heading as="h1" size="xl">
          Sign in
        </Heading>
        <Lead>A ticket goes in, a controlled production release comes out. But first, who are you?</Lead>
      </div>
      {IS_MOCK ? (
        <p className="text-sm text-ink-muted">Demo mode: the session is simulated, nothing to do here.</p>
      ) : (
        <a href={api.loginUrl(next)} className={buttonClasses("primary", "md")}>
          Continue with the identity provider
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
          <p className="text-xs text-ink-muted">Development login — this bench has no IdP</p>
          <Input
            aria-label="Development e-mail"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="admin@acme.dev"
            className="w-full"
          />
          <button type="submit" className={buttonClasses("secondary", "sm")}>
            Enter
          </button>
        </form>
      )}
    </div>
  );
}
