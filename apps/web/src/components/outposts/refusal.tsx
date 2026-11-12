// SPDX-License-Identifier: Apache-2.0
"use client";

import { Alert, EngineSentence, ErrorNote } from "@/components/ui";
import { deploymentRefusal, type OutpostRefusal } from "@/lib/outposts";

/**
 * What the API said when it refused a gesture on an outpost, in its own words. A refusal that is
 * about the deployment (outposts switched off, a door with no signing key) is not the person's
 * mistake: it says which one it is above the API's sentence, and is not shown as an error of theirs.
 */
export function RefusalNote({ refusal }: { refusal: OutpostRefusal }) {
  const deployment = deploymentRefusal(refusal);
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
  return (
    <div data-testid="outposts-refusal">
      <ErrorNote>{words}</ErrorNote>
    </div>
  );
}
