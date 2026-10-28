// SPDX-License-Identifier: Apache-2.0
import Link from "next/link";

/**
 * "What this catalogue is for", on the two catalogues that look alike and are not (owner review of
 * 2026-10-28: "is the ticket moved by API or by MCP, and with whose account?"). Connector types are the
 * platform's own integrations, called through each system's API; MCP servers are tools an agent calls
 * during its step. Each block says who calls, with which account, and points to the other catalogue.
 */
export type CatalogueKind = "connectors" | "mcp-servers";

const PURPOSE: Record<
  CatalogueKind,
  { title: string; who: string; account: string; not: string; other: { href: string; label: string } }
> = {
  connectors: {
    title: "What it is for: the platform's own integrations",
    who:
      "Choregos calls these systems through their APIs to run your workflows: it reads and moves tickets, " +
      "comments on them, opens pull requests, runs pipelines, deploys, and syncs knowledge sources. Their " +
      "events (a ticket labelled agent-ready, a pipeline result) reach the workflow by webhook.",
    account:
      "The account configured on the connector, usually a service or bot account: a comment or a transition " +
      "appears under its name, never under the person who started the workflow.",
    not:
      "An agent does not call these itself. It hands its results to the workflow, which writes them " +
      "(the triage's size and risk, a finding, a question).",
    other: { href: "/catalogue/mcp-servers", label: "Tools for agents: MCP servers" },
  },
  "mcp-servers": {
    title: "What it is for: tools your agents call",
    who:
      "During its step, an agent calls an MCP server's tools: search Confluence, read an issue, create a page. " +
      "Installing a server makes an MCP connector of the organisation; its tools are discovered closed, and an " +
      "administrator opens each one (Administration → Installed connectors). A write is a governed action.",
    account:
      "The key configured on the connector, usually a service account's: the platform makes the call with it and " +
      "never hands it to the agent. It is not the account of the person who started the workflow.",
    not:
      "A workflow does not move its ticket through MCP: the platform does that through the connector types, " +
      "with their APIs.",
    other: { href: "/catalogue/connectors", label: "The platform's own integrations: connector types" },
  },
};

export function CataloguePurpose({ kind }: { kind: CatalogueKind }) {
  const purpose = PURPOSE[kind];
  const id = `catalogue-purpose-${kind}`;
  return (
    <aside
      aria-labelledby={id}
      data-testid="catalogue-purpose"
      className="max-w-3xl space-y-1.5 rounded-md border border-accent-line bg-accent-soft px-4 py-3 text-sm"
    >
      <p id={id} className="font-semibold text-accent-strong">
        {purpose.title}
      </p>
      <p>{purpose.who}</p>
      <p>
        <span className="font-medium">With which account: </span>
        {purpose.account}
      </p>
      <p>
        <span className="font-medium">What it is not: </span>
        {purpose.not}{" "}
        <Link href={purpose.other.href} className="underline">
          {purpose.other.label}
        </Link>
        .
      </p>
    </aside>
  );
}
