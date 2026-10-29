// SPDX-License-Identifier: Apache-2.0
import type { AdminSection } from "@/lib/types";

/** The core route the instance sign-in card reads: a plugin block reading it again would show the same rows twice. */
const CORE_IDENTITY_ROUTE = "/platform/identity";

/** A plugin's identity section, without the blocks that only repeat the core's sign-in card. */
export function withoutCoreRows(section: AdminSection): AdminSection {
  const blocks = section.blocks.filter((block) => {
    const read =
      block.kind === "form"
        ? block.read
        : block.kind === "table"
          ? block.list
          : null;
    return read?.split("?")[0] !== CORE_IDENTITY_ROUTE;
  });
  return { ...section, blocks };
}
