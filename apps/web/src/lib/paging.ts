// SPDX-License-Identifier: Apache-2.0
import type { PageMeta } from "@choregos/contracts/api";

/** `getNextPageParam` of a keyset-paged list (ADR 0206): the cursor of the next page, or none. */
export function nextCursor(last: { meta: PageMeta }): string | undefined {
  return last.meta.has_more ? (last.meta.next_cursor ?? undefined) : undefined;
}
