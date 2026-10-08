// SPDX-License-Identifier: Apache-2.0
import Link from "next/link";

/**
 * Comment agents, skills, connecteurs et clients tiennent ensemble — la question de la revue du 07/10 :
 * « ça mélange des agents, des MCPs et des connecteurs API, c'est flou ». Quatre notions, une phrase
 * chacune, et le chemin d'une demande de bout en bout. Posé sur chacune des quatre pages ; la notion de
 * la page où l'on est ne mène nulle part.
 */
export type NotionDuGlossaire = "agents" | "skills" | "connectors" | "clients";

export const NOTIONS: { cle: NotionDuGlossaire; terme: string; href: string; definition: string }[] = [
  {
    cle: "agents",
    terme: "Agent",
    href: "/agents",
    definition:
      "does the work of a workflow step: instructions, a model, the skills it carries and the tools it may call. Choregos runs it, under a budget.",
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
      "a system Choregos reaches OUT to: a directory, a device fleet, an MCP server. Each operation is allowed, needs an approval, or is forbidden. A project's delivery tools — tracker, repository, CI, deployment — are set in the project's settings.",
  },
  {
    cle: "clients",
    terme: "Client",
    href: "/integrations",
    definition:
      "an AI assistant that reaches IN to Choregos through its MCP gate — Claude Code, Cursor, ChatGPT — with its human's rights, never more. It never decides.",
  },
];

export function Glossaire({ ici, ouvert = false }: { ici: NotionDuGlossaire; ouvert?: boolean }) {
  return (
    <details
      open={ouvert}
      data-testid="glossaire"
      className="max-w-3xl rounded border border-line bg-surface px-3 py-2 text-sm"
    >
      <summary className="cursor-pointer text-ink-muted">how agents, skills, connectors and clients fit together</summary>
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
      <ol aria-label="how a request flows" className="mt-3 flex flex-wrap items-center gap-1 text-xs text-ink-muted">
        <li>a client or a tracker opens a work item</li>
        <li aria-hidden="true">→</li>
        <li>its workflow hands each step to an agent</li>
        <li aria-hidden="true">→</li>
        <li>the agent calls connectors under the policy</li>
        <li aria-hidden="true">→</li>
        <li>a person approves what the policy asks</li>
      </ol>
      <p className="mt-2 text-xs text-ink-muted">
        A supplier&apos;s agent that you call over MCP is a connector (an MCP server), not an agent of your registry.
      </p>
    </details>
  );
}
