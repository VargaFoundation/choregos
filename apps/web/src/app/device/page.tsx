// SPDX-License-Identifier: Apache-2.0
"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import {
  Button,
  ErrorNote,
  Heading,
  Input,
  Lead,
  ReadState,
} from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { shortDate } from "@/lib/format";

/**
 * Where a developer surface's sign-in is approved (ADR 0154 §6, S67-05). `choregos login` (or the editor)
 * shows a code and opens this page; the person, signed in to the console as themselves, sees what is asking
 * and approves or refuses it. The surface then holds a session of its own, the person's, listed under
 * My account and ended there.
 *
 * `fresh=1` (from `choregos login --fresh`) sends the person through their identity provider once more
 * first, so that the sign-in this approves is a recent one.
 */
export default function DevicePage() {
  return (
    <Suspense>
      <Device />
    </Suspense>
  );
}

const KINDS = { cli: "the command line", editor: "an editor" } as const;

function Device() {
  const params = useSearchParams();
  const fromLink = params.get("user_code") ?? "";
  const fresh = params.get("fresh") === "1" && params.get("reauthed") !== "1";
  const [typed, setTyped] = useState(fromLink);
  const [code, setCode] = useState(fromLink);

  // A surface that asked for a recent sign-in: through the identity provider again, then back here.
  useEffect(() => {
    if (!fresh || !fromLink) return;
    const back = `/device?user_code=${encodeURIComponent(fromLink)}&reauthed=1`;
    window.location.assign(api.loginUrl(back, undefined, true));
  }, [fresh, fromLink]);

  const request = useQuery({
    queryKey: ["device-request", code],
    queryFn: () => api.deviceRequest(code),
    enabled: code !== "" && !fresh,
    retry: false,
  });
  const decide = useMutation({
    mutationFn: (decision: "approve" | "deny") =>
      api.decideDeviceRequest(code, decision),
  });
  const done = decide.isSuccess ? decide.variables : undefined;
  const missing =
    request.error instanceof ApiError && request.error.status === 404;

  return (
    <div className="mx-auto max-w-md space-y-6 py-12">
      <div className="space-y-3">
        <Heading as="h1" size="xl">
          Sign in a developer tool
        </Heading>
        <Lead>
          Approve only a code that a tool of yours is showing you right now.
        </Lead>
      </div>
      {code === "" || missing ? (
        <form
          className="space-y-2 text-sm"
          onSubmit={(event) => {
            event.preventDefault();
            setCode(typed.trim());
          }}
        >
          {missing && (
            <ErrorNote>
              This code is unknown, expired or already used. Ask the tool for a
              new one.
            </ErrorNote>
          )}
          <Input
            aria-label="Code"
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            placeholder="ABCD-EFGH"
            className="w-full font-mono"
          />
          <Button type="submit" tone="primary">
            Continue
          </Button>
        </form>
      ) : done ? (
        <p role="status" className="text-sm text-ink">
          {done === "approve"
            ? "Approved. You can go back to the tool: it is signed in as you. End it any time from My account."
            : "Refused. The tool is not signed in."}
        </p>
      ) : (
        <div className="space-y-4">
          <ReadState lecture={request} quoi="this sign-in request" />
          {request.data && (
            <div className="space-y-4 text-sm">
              <dl className="space-y-1">
                <div className="flex gap-2">
                  <dt className="text-ink-muted">Code</dt>
                  <dd className="font-mono">{request.data.user_code}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="text-ink-muted">Asking</dt>
                  <dd>
                    {request.data.client_name ?? "A tool"} (
                    {KINDS[request.data.client_kind]})
                  </dd>
                </div>
                {request.data.org && (
                  <div className="flex gap-2">
                    <dt className="text-ink-muted">Organisation</dt>
                    <dd>{request.data.org}</dd>
                  </div>
                )}
                <div className="flex gap-2">
                  <dt className="text-ink-muted">Code valid until</dt>
                  <dd>{shortDate(request.data.expires_at)}</dd>
                </div>
              </dl>
              {decide.error && <ErrorNote>{decide.error.message}</ErrorNote>}
              <div className="flex gap-2">
                <Button
                  tone="primary"
                  disabled={decide.isPending}
                  onClick={() => decide.mutate("approve")}
                >
                  Approve
                </Button>
                <Button
                  disabled={decide.isPending}
                  onClick={() => decide.mutate("deny")}
                >
                  Refuse
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
