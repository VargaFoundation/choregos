// SPDX-License-Identifier: Apache-2.0
import Link from "next/link";

/**
 * How agents, skills, connectors and clients fit together: the question of the review of 7 Oct:
 * "it mixes agents, MCPs and API connectors, it is blurry". Four notions, one sentence
 * each, and the path of a request from end to end. Placed on each of the four pages; the notion of
 * the page one is on leads nowhere.
 */
export type NotionDuGlossaire = "agents" | "skills" | "connectors" | "clients";

export const NOTIONS: { cle: NotionDuGlossaire; terme: string; href: string; definition: string }[] = [
  {
    cle: "agents",
    terme: "Agent",
    href: "/agents",
    definition:
      "does the work of a workflow step: instructions, a model, the skills it carries and the tools it may call. An internal agent runs in Choregos, under a budget; an external agent is an AI client registered here, whose calls carry its name and limits.",
  },
  {
    cle: "skills",
    terme: "Skill",
    href: "/skills",
    definition: "know-how an agent carries — a SKILL.md and its files. A skill grants no right.",
  },
  {
    cle: "connectors",
    terme: "Connector",
    href: "/admin/connectors",
    definition:
      "an installed instance of a system Choregos reaches OUT to: a directory, a device fleet, an MCP server. Each operation is allowed, needs an approval, or is forbidden. A catalogue lists what the platform offers (MCP servers, connector types); installing one from it makes a connector. A project's delivery tools — tracker, repository, CI, deployment — are set in the project's settings.",
  },
  {
    cle: "clients",
    terme: "Client",
    href: "/integrations",
    definition:
      "an AI assistant that reaches IN to Choregos through its MCP door — Claude Code, Cursor, ChatGPT — with its human's access token: their rights, never more, fewer once registered as an external agent. It never decides.",
  },
];

export function Glossary({ ici, ouvert = false }: { ici: NotionDuGlossaire; ouvert?: boolean }) {
  return (
    <details
      open={ouvert}
      data-testid="glossary"
      className="max-w-3xl rounded border border-line bg-surface px-3 py-2 text-sm"
    >
      <summary className="cursor-pointer text-ink-muted">How agents, skills, connectors and clients fit together</summary>
      <dl className="mt-2 space-y-1.5">
        {NOTIONS.map((notion) => (
          <div key={notion.cle} className="flex gap-2">
            <dt className="w-24 shrink-0 font-medium">
              {notion.cle === ici ? (
                <span aria-current="page">{notion.terme}</span>
              ) : (
                <Link href={notion.href} className="underline">
                  {notion.terme}
                </Link>
              )}
            </dt>
            <dd className="text-ink-muted">{notion.definition}</dd>
          </div>
        ))}
      </dl>
      <ol aria-label="How a request flows" className="mt-3 flex flex-wrap items-center gap-1 text-xs text-ink-muted">
        <li>A client or a tracker opens a work item</li>
        <li aria-hidden="true">→</li>
        <li>Its workflow hands each step to an agent</li>
        <li aria-hidden="true">→</li>
        <li>The agent calls connectors under the policy</li>
        <li aria-hidden="true">→</li>
        <li>A person approves what the policy asks</li>
      </ol>
      <p className="mt-2 text-xs text-ink-muted">
        A supplier&apos;s agent that you call over MCP is a connector (an MCP server), not an agent of your registry.
      </p>
    </details>
  );
}
