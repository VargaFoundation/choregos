// SPDX-License-Identifier: Apache-2.0
import { Badge } from "@/components/ui";
import type { MemberGroup } from "@/lib/types";

/** Where a group comes from: written here, pushed by the identity provider (ADR 0160), or its sign-in claim. */
export type GroupSource = MemberGroup["source"];

export const SOURCE_LABELS: Record<GroupSource, string> = {
  internal: "Internal",
  scim: "Identity provider (SCIM)",
  identity_provider: "Identity provider (sign-in)",
};

export function SourceBadge({ source }: { source: GroupSource }) {
  return (
    <Badge tone={source === "internal" ? "neutral" : "info"} variant="plain" data-testid="group-source">
      {SOURCE_LABELS[source]}
    </Badge>
  );
}
