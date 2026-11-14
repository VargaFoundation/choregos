// SPDX-License-Identifier: Apache-2.0
import { Badge } from "@/components/ui";
import type { OrgGroupSource } from "@/lib/types";

/** Where a group comes from (ADR 0160): written here, or pushed by the identity provider. */
export const SOURCE_LABELS: Record<OrgGroupSource, string> = {
  internal: "Internal",
  scim: "Identity provider (SCIM)",
};

export function SourceBadge({ source }: { source: OrgGroupSource }) {
  return (
    <Badge tone={source === "scim" ? "info" : "neutral"} variant="plain" data-testid="group-source">
      {SOURCE_LABELS[source]}
    </Badge>
  );
}
