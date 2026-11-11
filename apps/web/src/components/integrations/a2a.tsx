// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState, type ReactNode } from "react";
import { DocsLink } from "@/components/docs-link";
import { MachineAccounts } from "@/components/project-settings/machine-accounts";
import {
  Alert,
  Button,
  Card,
  Code,
  ErrorNote,
  Select,
  tabClasses,
} from "@/components/ui";
import { api } from "@/lib/api";
import {
  a2aEndpoint,
  a2aBlock,
  a2aSnippets,
  exposureOf,
  machineCommands,
  personalTokenCommand,
  revokeCommand,
  type Exposure,
  type SnippetId,
} from "@/lib/a2a-snippets";
import type { DocSlug } from "@/lib/docs";
import { a2aCardUrl, a2aFileLimit, formatMiB } from "@/lib/integrations";
import { canManageMembers } from "@/lib/memberships";
import type { ApiTokenCreated, MachineAccount, ProjectDto } from "@/lib/types";
import { Extrait } from "./panel";

type Served = Extract<Exposure, { exposed: true }>;

/**
 * An application or another agent starts a workflow over the A2A door (ADR 0078, S62-11): the steps of
 * the guide, with this instance's values. Which workflows are served, who calls (an application's own
 * account, ADR 0131, or a token of one's own to try it), the calls to paste, and where the work shows.
 *
 * At the organisation's scale (`projet` absent) every project is listed and one is chosen for the
 * account; in a project, that project only.
 */
export function A2aIntegration({
  org,
  projet,
}: {
  org: string;
  projet?: string;
}) {
  const infos = useQuery({
    queryKey: ["integrations"],
    queryFn: () => api.integrations(),
    staleTime: Infinity,
  });
  const listed = useQuery({
    queryKey: ["projects", org],
    queryFn: () => api.projects(org),
  });
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api.me(),
    retry: false,
  });
  const projects = (listed.data?.items ?? []).filter(
    (project) => !projet || project.slug === projet,
  );

  const summaries = useQueries({
    queries: projects.map((project) => ({
      queryKey: ["workflows", project.slug],
      queryFn: () => api.workflows(project.slug),
    })),
  });
  const pairs = projects.flatMap((project, index) =>
    (summaries[index]?.data ?? []).map((summary) => ({
      project: project.slug,
      name: summary.name,
    })),
  );
  // The same keys as a workflow's own pages: a definition read here is not read again there.
  const definitions = useQueries({
    queries: pairs.map((pair) => ({
      queryKey: ["workflow", pair.project, pair.name],
      queryFn: () => api.workflowNamed(pair.project, pair.name),
    })),
  });
  const exposures = new Map<string, Exposure[]>();
  pairs.forEach((pair, index) => {
    const definition = definitions[index]?.data;
    if (!definition) return;
    exposures.set(pair.project, [
      ...(exposures.get(pair.project) ?? []),
      exposureOf(pair.project, definition),
    ]);
  });
  // Everything read: before that, "nothing is served" would be said of what is still loading.
  const loaded =
    Boolean(listed.data) &&
    summaries.every((query) => query.data) &&
    definitions.every((query) => query.data);
  const servedIn = (slug: string): Served[] =>
    (exposures.get(slug) ?? []).filter(
      (exposure): exposure is Served => exposure.exposed,
    );

  const [chosenProject, setChosenProject] = useState<string | null>(null);
  const project =
    projects.find((p) => p.slug === chosenProject) ??
    projects.find((p) => servedIn(p.slug).length > 0) ??
    projects[0] ??
    null;
  const [chosenSkill, setChosenSkill] = useState<string | null>(null);
  const skills = project ? servedIn(project.slug) : [];
  const skill =
    skills.find((s) => s.skillId === chosenSkill) ?? skills[0] ?? null;

  const [token, setToken] = useState<{ value: string; who: string } | null>(
    null,
  );
  const [application, setApplication] = useState<MachineAccount | null>(null);

  const origin = infos.data?.mcp_url ?? "";
  const card = infos.data ? a2aCardUrl(infos.data.mcp_url, org) : "…";
  const endpoint = infos.data ? a2aEndpoint(infos.data.mcp_url, org) : "…";
  const qualified = project ? `${project.org}:${project.slug}` : null;
  const memberships = me.data?.memberships ?? [];
  const mayManage = project
    ? canManageMembers(memberships, org, project.slug)
    : false;

  return (
    <div className="space-y-6" data-testid="a2a-integration">
      <Card title="An application or another agent (A2A)">
        <div className="space-y-2 text-sm">
          <p className="text-ink-muted">
            An application, a script or another agent starts a workflow and
            hands it its data, without anyone opening the console, then follows
            it to its result. The door speaks A2A 0.3.0 and 1.0, JSON-RPC over
            HTTP, at one URL. It never decides: when a step waits for a person,
            that person decides here.{" "}
            <DocsLink slug="integrate/a2a" testId="a2a-guide">
              The guide, step by step
            </DocsLink>
          </p>
          <p className="break-all">
            Agent card <Code data-testid="a2a-card-url">{card}</Code>
          </p>
          <p className="break-all">
            JSON-RPC endpoint <Code data-testid="a2a-endpoint">{endpoint}</Code>
          </p>
        </div>
      </Card>

      <Step n={1} title="Open a workflow to the door" docs={["integrate/a2a"]}>
        <p className="text-sm text-ink-muted">
          Nothing is served by default: a workflow is a skill once its
          definition carries <Code>a2a:</Code>. A caller sees the skills of the
          projects it may read.
        </p>
        {listed.error && <ErrorNote>{String(listed.error)}</ErrorNote>}
        {listed.data && projects.length === 0 && (
          <p className="text-sm">No project to serve yet.</p>
        )}
        {projects.map((p) => (
          <ProjectWorkflows
            key={p.slug}
            project={p}
            exposures={exposures.get(p.slug)}
            showName={!projet}
          />
        ))}
      </Step>

      <Step n={2} title="Decide who calls" docs={["integrate/a2a"]}>
        {!projet && projects.length > 1 && (
          <label className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-ink-muted">The project it calls</span>
            <Select
              aria-label="The project it calls"
              value={project?.slug ?? ""}
              onChange={(event) => {
                setChosenProject(event.target.value);
                setChosenSkill(null);
                setApplication(null);
              }}
              className="w-auto"
            >
              {projects.map((p) => (
                <option key={p.slug} value={p.slug}>
                  {p.name}
                </option>
              ))}
            </Select>
          </label>
        )}
        <p className="text-sm text-ink-muted">
          <strong className="text-ink">An application</strong> gets an account
          of its own, on one project: it acts under its own name, keeps working
          when you leave, never decides, and has its own daily budget. Its token
          is shown once.
        </p>
        {project &&
          (me.data && !mayManage ? (
            <Alert
              tone="warn"
              title="You cannot create an application here"
              className="text-sm"
            >
              <span data-testid="a2a-cannot-manage">
                Creating an application and issuing its token takes the right to
                manage the members of <Code>{qualified}</Code> (
                <Code>member:manage</Code>: a project owner or an organisation
                administrator). Ask one of them, or try the door yourself below.
              </span>
            </Alert>
          ) : (
            me.data && (
              <MachineAccounts
                key={project.slug}
                slug={project.slug}
                onIssued={(account, issued) => {
                  setApplication(account);
                  setToken({ value: issued.token, who: account.display_name });
                }}
              />
            )
          ))}
        {qualified && (
          <details className="text-sm">
            <summary className="cursor-pointer text-ink-muted">
              The same from a terminal
            </summary>
            <Extrait
              code={machineCommands(qualified)}
              testId="a2a-machine-commands"
            />
          </details>
        )}
        {listed.data && (
          <PersonalToken
            project={qualified}
            onIssued={(issued) => {
              setApplication(null);
              setToken({ value: issued.token, who: "you" });
            }}
          />
        )}
      </Step>

      <Step n={3} title="Call it" docs={["integrate/a2a", "cli/install"]}>
        {skills.length > 1 && (
          <label className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-ink-muted">Skill</span>
            <Select
              aria-label="Skill"
              value={skill?.skillId ?? ""}
              onChange={(event) => setChosenSkill(event.target.value)}
              className="w-auto font-mono"
            >
              {skills.map((s) => (
                <option key={s.skillId} value={s.skillId}>
                  {s.skillId}
                </option>
              ))}
            </Select>
          </label>
        )}
        {loaded && !skill && (
          <p className="text-sm text-ink-muted" data-testid="a2a-no-skill">
            No workflow of {project ? <Code>{qualified}</Code> : "this project"}{" "}
            is served yet: the examples name a placeholder skill until one is
            (step 1).
          </p>
        )}
        <p className="text-sm text-ink-muted">
          {token ? (
            <span data-testid="a2a-token-in-snippets">
              The token just issued for {token.who} is in the examples: copy
              them now, it is not shown again.
            </span>
          ) : (
            <>
              The token is a placeholder: issue one at step 2, or paste yours
              where <Code>{"<token>"}</Code> stands.
            </>
          )}
        </p>
        {infos.data && (
          <SnippetTabs
            origin={origin}
            org={org}
            skill={skill}
            token={token?.value ?? null}
          />
        )}
      </Step>

      <Step
        n={4}
        title="Follow the work, and revoke"
        docs={["integrate/a2a", "use/work-items"]}
      >
        <ul
          className="ml-4 list-disc space-y-2 text-sm"
          data-testid="a2a-follow-up"
        >
          <li>
            Each task is a work item of the project, on its{" "}
            {project ? (
              <Link href={`/p/${project.slug}/board`}>board</Link>
            ) : (
              "board"
            )}
            , opened through the same path as the console: the workflow&apos;s
            gates, budget and journal apply. Its page says who started it:{" "}
            {application ? (
              <>
                <Code>machine:{application.address}</Code> for{" "}
                {application.display_name}
              </>
            ) : (
              <>
                <Code>machine:&lt;address&gt;</Code> for an application, the
                person for a personal token
              </>
            )}
            .
          </li>
          <li>
            When a step waits for a person, the task says{" "}
            <Code>input-required</Code> and gives its <Code>decision_url</Code>:
            the decision is taken in the{" "}
            {project ? (
              <Link href={`/p/${project.slug}/inbox`}>inbox</Link>
            ) : (
              "inbox"
            )}
            , never over A2A.
          </li>
          <li>
            Revoke an application&apos;s token in{" "}
            {project ? (
              <Link href={`/p/${project.slug}/settings/people`}>
                Settings › People › Applications
              </Link>
            ) : (
              "Settings › People › Applications"
            )}{" "}
            (or{" "}
            <Code>
              {revokeCommand(
                qualified ?? "<org>:<project>",
                application?.slug ?? "<application>",
              )}
            </Code>
            ); removing the application stops all its tokens at once. A personal
            token is revoked in{" "}
            <Link href="/account#tokens">My account › Access tokens</Link>.
          </li>
        </ul>
      </Step>
    </div>
  );
}

/** What a step's documentation link says, when a step has more than one: two "Learn more" would be ambiguous. */
const STEP_DOCS_LABELS: Partial<Record<DocSlug, string>> = {
  "cli/install": "Install the CLI",
  "use/work-items": "Work items",
};

/** One step: its number, its title, and the documentation pages that say more. */
function Step({
  n,
  title,
  docs,
  children,
}: {
  n: number;
  title: string;
  docs: DocSlug[];
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={`a2a-step-${n}`} data-testid={`a2a-step-${n}`}>
      <Card>
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <h2 id={`a2a-step-${n}`} className="text-base font-semibold">
            <span className="mr-2 text-ink-muted">{n}.</span>
            {title}
          </h2>
          <span className="flex flex-wrap gap-3">
            {docs.map((slug) => (
              <DocsLink key={slug} slug={slug} testId={`a2a-step-${n}-docs`}>
                {STEP_DOCS_LABELS[slug] ?? "Learn more"}
              </DocsLink>
            ))}
          </span>
        </div>
        <div className="space-y-3">{children}</div>
      </Card>
    </section>
  );
}

/** A project's workflows: served or not, the skill, what it takes, and how to open one that is not. */
function ProjectWorkflows({
  project,
  exposures,
  showName,
}: {
  project: ProjectDto;
  exposures: Exposure[] | undefined;
  showName: boolean;
}) {
  return (
    <div className="space-y-2" data-testid={`a2a-project-${project.slug}`}>
      {showName && <h3 className="text-sm font-medium">{project.name}</h3>}
      {!exposures ? (
        <p className="text-xs text-ink-muted">Reading its workflows…</p>
      ) : exposures.length === 0 ? (
        <p className="text-xs text-ink-muted">
          No workflow in this project yet.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table aria-label={`Workflows of ${project.name}`}>
            <thead>
              <tr>
                <th>Workflow</th>
                <th>Skill</th>
                <th>Takes</th>
                <th>Required fields</th>
              </tr>
            </thead>
            <tbody>
              {exposures.map((exposure) => (
                <WorkflowRow
                  key={exposure.workflow}
                  project={project.slug}
                  exposure={exposure}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p
        className="text-xs text-ink-muted"
        data-testid={`a2a-file-limit-${project.slug}`}
      >
        Files a message may carry, each and all together: up to{" "}
        {formatMiB(a2aFileLimit(project.config))}.{" "}
        <Link href={`/p/${project.slug}/settings/agents#a2a`}>
          Change it in its settings
        </Link>
      </p>
    </div>
  );
}

function WorkflowRow({
  project,
  exposure,
}: {
  project: string;
  exposure: Exposure;
}) {
  const yaml = `/p/${project}/workflows/${encodeURIComponent(exposure.workflow)}/yaml`;
  if (exposure.exposed) {
    const required = exposure.fields
      .filter((field) => field.required)
      .map((field) => field.name);
    return (
      <tr data-testid={`a2a-workflow-${exposure.workflow}`}>
        <td>{exposure.workflow}</td>
        <td>
          <Code>{exposure.skillId}</Code>
        </td>
        <td className="text-xs">{exposure.inputModes.join(", ")}</td>
        <td className="text-xs">
          {required.length > 0 ? required.join(", ") : "none"}
        </td>
      </tr>
    );
  }
  return (
    <tr data-testid={`a2a-workflow-${exposure.workflow}`}>
      <td>{exposure.workflow}</td>
      <td colSpan={3} className="text-xs">
        <details>
          <summary className="cursor-pointer text-ink-muted">
            Not served — add this block to serve it
          </summary>
          <div className="mt-2 space-y-2">
            <Extrait
              code={a2aBlock(exposure.workflow, exposure.description)}
              testId={`a2a-block-${exposure.workflow}`}
            />
            <Link href={yaml} data-testid={`a2a-yaml-${exposure.workflow}`}>
              Open its YAML
            </Link>
          </div>
        </details>
      </td>
    </tr>
  );
}

/** The secondary way: a token that acts as you, 30 days, to try the door before an application does. */
function PersonalToken({
  project,
  onIssued,
}: {
  project: string | null;
  onIssued: (token: ApiTokenCreated) => void;
}) {
  const [issued, setIssued] = useState<ApiTokenCreated | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function issue() {
    setError(null);
    try {
      const token = await api.createToken({
        name: "A2A client",
        scopes: ["a2a"],
        project,
        expires_in_days: 30,
      });
      setIssued(token);
      onIssued(token);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "token refused");
    }
  }

  return (
    <div
      className="space-y-2 border-t border-line pt-3 text-sm"
      data-testid="a2a-personal"
    >
      <p className="text-ink-muted">
        <strong className="text-ink">To try it yourself</strong>, a token that
        acts as you: scope <Code>a2a</Code>, 30 days
        {project ? (
          <>
            , bound to <Code>{project}</Code>
          </>
        ) : null}
        . It opens the A2A door only; the REST API and the MCP door refuse it.
      </p>
      {error && <ErrorNote>{error}</ErrorNote>}
      {issued ? (
        <p className="break-all">
          Token, shown once: <Code data-testid="a2a-token">{issued.token}</Code>
        </p>
      ) : (
        <Button onClick={() => void issue()}>Create an A2A token for me</Button>
      )}
      <p className="text-xs text-ink-muted">
        Or <Code>{personalTokenCommand(project)}</Code>
      </p>
    </div>
  );
}

/** The three ways to call, in tabs: the CLI, curl in A2A 0.3.0, A2A 1.0. */
function SnippetTabs({
  origin,
  org,
  skill,
  token,
}: {
  origin: string;
  org: string;
  skill: Served | null;
  token: string | null;
}) {
  const snippets = a2aSnippets({ origin, org, skill, token });
  const [active, setActive] = useState<SnippetId>("cli");
  const shown =
    snippets.find((snippet) => snippet.id === active) ?? snippets[0]!;
  return (
    <div className="space-y-2">
      <div
        role="tablist"
        aria-label="Ways to call"
        className="flex gap-6 overflow-x-auto border-b border-line pb-px"
      >
        {snippets.map((snippet) => (
          <button
            key={snippet.id}
            type="button"
            role="tab"
            aria-selected={snippet.id === shown.id}
            aria-controls="a2a-snippet-panel"
            onClick={() => setActive(snippet.id)}
            className={tabClasses(snippet.id === shown.id)}
          >
            {snippet.label}
          </button>
        ))}
      </div>
      <div
        id="a2a-snippet-panel"
        role="tabpanel"
        aria-label={shown.label}
        className="space-y-2"
      >
        <p className="text-xs text-ink-muted">Paste into {shown.where}</p>
        <Extrait code={shown.code} testId="a2a-snippet" />
      </div>
    </div>
  );
}
