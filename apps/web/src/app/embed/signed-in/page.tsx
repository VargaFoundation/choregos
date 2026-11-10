// SPDX-License-Identifier: Apache-2.0
"use client";

import { useEffect } from "react";
import { SIGNED_IN_MESSAGE } from "@/components/embed/embedded-assistant";

/** The popup of the embedded component's sign-in lands here: it tells the frame that opened it, then closes. */
export default function SignedIn() {
  useEffect(() => {
    window.opener?.postMessage(SIGNED_IN_MESSAGE, window.location.origin);
    window.close();
  }, []);
  return <p className="p-4 text-sm text-ink-muted">Signed in. You can close this window.</p>;
}
