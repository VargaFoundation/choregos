// SPDX-License-Identifier: Apache-2.0
"use client";

import { Alert, EngineSentence, ErrorNote } from "@/components/ui";
import { deploymentRefusal, retryIn, routeRefusal, type OutpostRefusal } from "@/lib/outposts";

/**
 * What the API said when it refused a gesture on an outpost, in its own words. A refusal that is
 * about the deployment (outposts switched off, a door with no signing key) is not the person's
 * mistake: it says which one it is above the API's sentence, and is not shown as an error of theirs.
 * A refusal of the route to an outpost (ADR 0149 §7: paused, offline, refused by the outpost's own
 * policy, not answered in time, or a quota's 429) says which, with its code, the outpost and the alias
 * it names, the outpost's own code, and when to ask again.
 */
export function RefusalNote({ refusal }: { refusal: OutpostRefusal }) {
  const deployment = deploymentRefusal(refusal);
  const route = routeRefusal(refusal);
  const words = (
    <>
      <EngineSentence text={refusal.detail} />
      {refusal.connectors.length > 0 && (
        <>
          {" "}
          Connectors: <span data-verbatim>{refusal.connectors.join(", ")}</span>.
        </>
      )}
    </>
  );
  if (deployment) {
    return (
      <div data-testid="outposts-deployment-refusal">
        <Alert tone="warn" title={deployment}>
          {words}
        </Alert>
      </div>
    );
  }
  if (route) {
    return (
      <div data-testid="outpost-route-refusal">
        <Alert tone={refusal.status === 429 ? "warn" : "danger"} title={route}>
          <p className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
            {refusal.code && (
              <span>
                Code <code data-testid="refusal-code">{refusal.code}</code>
              </span>
            )}
            {refusal.outpost && (
              <span>
                Outpost <code data-verbatim>{refusal.outpost}</code>
              </span>
            )}
            {refusal.endpoint && (
              <span>
                Alias <code data-verbatim>{refusal.endpoint}</code>
              </span>
            )}
            {refusal.refusal && (
              <span>
                The outpost&apos;s code <code data-testid="outpost-own-code">{refusal.refusal}</code>
              </span>
            )}
          </p>
          <p className="mt-1">{words}</p>
          {refusal.retryAfter !== undefined && (
            <p className="mt-1" data-testid="retry-after">
              {retryIn(refusal.retryAfter)}
            </p>
          )}
        </Alert>
      </div>
    );
  }
  return (
    <div data-testid="outposts-refusal">
      <ErrorNote>{words}</ErrorNote>
    </div>
  );
}
