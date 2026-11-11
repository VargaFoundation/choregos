// SPDX-License-Identifier: Apache-2.0
"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { SIGNED_IN_MESSAGE } from "@/lib/embed-token";

/**
 * The popup of the embedded component's sign-in lands here (S63-12): the person is signed in with the console's
 * own session (first party). It mints the frame token for the installation and hands it to the frame that opened
 * it (same origin, never `*`), then closes: a frame under another site does not carry this session cookie, the
 * token is what it will hold instead.
 */
function Done() {
  const params = useSearchParams();
  const org = params.get("org");
  const installation = params.get("installation");
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    void (async () => {
      try {
        const minted = org && installation ? await api.mintEmbedToken(org, installation) : null;
        window.opener?.postMessage(
          { type: SIGNED_IN_MESSAGE, token: minted?.token ?? null, expires_in: minted?.expires_in ?? 0 },
          window.location.origin,
        );
        window.close();
      } catch (error) {
        setFailed(error instanceof Error ? error.message : "The assistant could not give this frame a session.");
      }
    })();
  }, [org, installation]);
  if (failed) return <p className="p-4 text-sm text-danger">{failed}</p>;
  return <p className="p-4 text-sm text-ink-muted">Signed in. You can close this window.</p>;
}

export default function SignedIn() {
  return (
    <Suspense>
      <Done />
    </Suspense>
  );
}
