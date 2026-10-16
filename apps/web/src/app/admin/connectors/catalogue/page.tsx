// SPDX-License-Identifier: Apache-2.0
import { redirect } from "next/navigation";

/**
 * The MCP catalogue left the administration for Catalogue → MCP servers (ADR 0074): links and
 * documents that point here land on the new page.
 */
export default function AncienneCatalogueMcp() {
  redirect("/catalogue/mcp-servers");
}
