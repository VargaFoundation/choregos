// SPDX-License-Identifier: Apache-2.0
import Link from "next/link";
import { Tabs, tabClasses } from "@/components/ui";

/** The two views of the connectors of an organisation: what it declared, and what it can add (ADR 0065). */
export function ConnectorTabs({ active }: { active: "connectors" | "catalogue" }) {
  return (
    <Tabs aria-label="Connector views">
      <Link
        href="/admin/connectors"
        className={tabClasses(active === "connectors")}
        aria-current={active === "connectors" ? "page" : undefined}
      >
        Connectors
      </Link>
      <Link
        href="/admin/connectors/catalogue"
        className={tabClasses(active === "catalogue")}
        aria-current={active === "catalogue" ? "page" : undefined}
      >
        MCP catalogue
      </Link>
    </Tabs>
  );
}
