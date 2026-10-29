// SPDX-License-Identifier: Apache-2.0
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { GatewayKeysCard } from "@/components/admin/gateway-keys";
import { Alert, Button, Card, Code, Empty, EtatDeLecture, Field, Input, StateBadge } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { AgentBackendInfo, ExecutorInfo } from "@/lib/types";

const DOCS = "https://github.com/VargaFoundation/choregos/blob/main/docs";

/** What each executor kind is, in one line, and the procedure that sets it up (all five exist in docs/procedures). */
const EXECUTORS: Record<ExecutorInfo["kind"], string> = {
  agent_sandbox: "A disposable sandbox pod per stage, on the cluster (the default of a real deployment).",
  k8s_job: "A Kubernetes Job per stage, in the runner namespace.",
  tekton: "A Tekton PipelineRun per stage, where Tekton is installed.",
  aca: "An Azure Container Apps job per stage.",
  local_docker: "A container on the worker's own Docker, for the local install.",
};

/** What an executor needs before a deployment can turn it on. */
const NEEDS: Partial<Record<ExecutorInfo["kind"], string>> = {
  agent_sandbox: "Needs the kubernetes-sigs/agent-sandbox operator on the cluster.",
  tekton: "Needs Tekton Pipelines on the cluster.",
  aca: "Needs an Azure Container Apps environment.",
  local_docker: "Only for the local install (docker compose).",
};

/**
 * What the platform runs: the coding agents (backends) and the executors that host their stages.
 * How people sign in to the instance is on the Identity page (S44-04), not here.
 */
export default function PlatformPage() {
  const backends = useQuery({ queryKey: ["backends"], queryFn: () => api.backends() });
  const executors = useQuery({ queryKey: ["executors"], queryFn: () => api.executors() });
  // What runs first, then what this deployment does not offer (S44-04): five rows of the same
  // weight read as if the first one, `agent_sandbox`, were the one in use.
  const ordered = [...(executors.data ?? [])].sort((a, b) => Number(b.enabled !== false) - Number(a.enabled !== false));
  const running = ordered.filter((executor) => executor.enabled !== false);
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Coding agents (backends)">
        <p className="mb-3 text-xs text-ink-muted">
          A backend is the coding agent CLI that the runner image ships and that executes an agent stage (see{" "}
          <a className="underline" href={`${DOCS}/concepts.md#actor`}>
            Actor in the concepts
          </a>
          ).
        </p>
        <p className="mb-3 text-xs text-ink-muted">
          Enabled here = counted as usable by readiness. Which backends a project may use is set by the deployment
          (<Code>CHOREGOS_ALLOWED_AGENT_BACKENDS</Code>, chart <Code>defaultBackend</Code>) and the project&apos;s
          policy.
        </p>
        <ul className="space-y-3 text-xs">
          {(backends.data ?? []).map((backend) => (
            <BackendRow key={backend.name} backend={backend} />
          ))}
        </ul>
        <EtatDeLecture lecture={backends} quoi="the agent backends" />
        {backends.data?.length === 0 && <Empty>No backend registered</Empty>}
      </Card>
      <Card title="Executors (where agent stages run)">
        <p className="mb-3 text-xs text-ink-muted">
          An executor is the place where an agent stage runs. This list is read-only: it shows what this deployment
          runs. Change it in the deployment: chart <Code>global.runner.allowedExecutors</Code> /{" "}
          <Code>CHOREGOS_EXECUTOR_KIND</Code>.
        </p>
        {executors.data && (
          <p className="mb-3 text-sm" data-testid="executors-running">
            {running.length === 0 ? (
              "No executor is turned on in this deployment."
            ) : (
              <>
                Agent stages run on{" "}
                {running.map((executor, index) => (
                  <span key={executor.kind}>
                    {index > 0 && (index === running.length - 1 ? " or " : ", ")}
                    <span className="font-mono" data-donnee>
                      {executor.kind}
                    </span>
                  </span>
                ))}{" "}
                in this deployment.
              </>
            )}
          </p>
        )}
        <ul className="space-y-2 text-xs">
          {ordered.map((executor) => (
            <li key={executor.kind} data-testid={`executor-${executor.kind}`}>
              <div className="flex items-center gap-2">
                <StateBadge
                  state={executor.enabled === false ? "disabled" : "ok"}
                  display={executor.enabled === false ? "Not turned on here" : "In use"}
                  kind={executor.enabled === false ? "blocked" : "terminal"}
                />
                <span className="font-mono" data-donnee>
                  {executor.kind}
                </span>
              </div>
              <p className="text-ink-muted">
                {EXECUTORS[executor.kind]}{" "}
                {executor.enabled === false && NEEDS[executor.kind] ? `${NEEDS[executor.kind]} ` : ""}
                <a className="underline" href={`${DOCS}/procedures/connector.runtime.${executor.kind}.md`}>
                  Set-up procedure
                </a>
              </p>
            </li>
          ))}
        </ul>
        <EtatDeLecture lecture={executors} quoi="the executors" />
        {executors.data?.length === 0 && <Empty>No executor registered</Empty>}
      </Card>
      <div className="lg:col-span-2">
        <GatewayKeysCard />
      </div>
    </div>
  );
}

function BackendRow({ backend }: { backend: AgentBackendInfo }) {
  const client = useQueryClient();
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const change = useMutation({
    mutationFn: (enabled: boolean) =>
      api.putPlatformBackend({
        name: backend.name,
        enabled,
        disabled_reason: enabled ? null : reason.trim(),
      }),
    onSuccess: () => {
      setAsking(false);
      setReason("");
      void client.invalidateQueries({ queryKey: ["backends"] });
    },
  });
  const refused = change.error instanceof ApiError && change.error.status === 403;
  const conformance = backend.conformance;
  const hasConformance = conformance?.total != null;
  return (
    <li data-testid={`backend-${backend.name}`} className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <StateBadge
          state={backend.enabled ? "ok" : "disabled"}
          display={backend.enabled ? "Enabled" : "Disabled"}
          kind={backend.enabled ? "terminal" : "blocked"}
        />
        <span className="font-mono">{backend.name}</span>
        <span className="text-ink-muted">{backend.version ?? ""}</span>
        {!asking && (
          <Button
            size="sm"
            aria-label={`${backend.enabled ? "Disable" : "Enable"} ${backend.name}`}
            onClick={() => (backend.enabled ? setAsking(true) : change.mutate(true))}
            disabled={change.isPending}
          >
            {backend.enabled ? "Disable" : "Enable"}
          </Button>
        )}
      </div>
      {backend.capabilities.length > 0 && (
        <p className="text-ink-muted">Capabilities: {backend.capabilities.join(", ")}</p>
      )}
      {hasConformance && (
        <p className="text-ink-muted">
          Conformance: {conformance.passed ?? 0}/{conformance.total} passed
          {conformance.last_run_at ? `, last run ${conformance.last_run_at}` : ""}
          {conformance.report_url && (
            <>
              {" "}
              <a className="underline" href={conformance.report_url}>
                Report
              </a>
            </>
          )}
          {(conformance.failures?.length ?? 0) > 0 && <> Failures: {conformance.failures?.join(", ")}</>}
        </p>
      )}
      {!backend.enabled && backend.disabled_reason && (
        <p className="text-ink-muted">Disabled because: {backend.disabled_reason}</p>
      )}
      {asking && (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (reason.trim()) change.mutate(false);
          }}
        >
          <Field label={`Why disable ${backend.name}?`} className="min-w-48 flex-1">
            {(p) => <Input {...p} value={reason} onChange={(e) => setReason(e.target.value)} />}
          </Field>
          <Button type="submit" tone="primary" size="sm" disabled={!reason.trim() || change.isPending}>
            Confirm disable
          </Button>
          <Button size="sm" onClick={() => setAsking(false)}>
            Cancel
          </Button>
        </form>
      )}
      {change.isError && (
        <Alert tone="danger">
          {refused ? "Only a platform administrator can change this." : `Could not save: ${change.error.message}`}
        </Alert>
      )}
    </li>
  );
}
