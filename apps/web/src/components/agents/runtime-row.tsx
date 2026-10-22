// SPDX-License-Identifier: Apache-2.0
import { Badge, type Tone } from "@/components/ui";
import { versionOf } from "@/lib/agent-versions";
import type { Agent, HostedBuild, HostedRuntime, PromptRuntime, VoiceRuntime } from "@/lib/types";

const BUILD: Record<HostedBuild["status"], { tone: Tone; label: string }> = {
  pending: { tone: "waiting", label: "build pending" },
  building: { tone: "running", label: "building" },
  ready: { tone: "succeeded", label: "built" },
  failed: { tone: "danger", label: "build failed" },
};

/** How often the agent's page asks for a build still running. */
export const BUILD_POLL_MS = 3000;

/** The runtime of the agent's latest version, the one a publication just built. */
export function latestRuntime(agent: Pick<Agent, "versions" | "latest_version"> | undefined) {
  return agent ? versionOf(agent, agent.latest_version)?.spec?.runtime : undefined;
}

/** Whether this runtime's build is still running: the page asks again until it is not (S41-12). */
export function buildInProgress(runtime: { kind?: string; build?: HostedBuild | null } | null | undefined): boolean {
  return runtime?.kind === "hosted" && (runtime.build?.status === "pending" || runtime.build?.status === "building");
}

/** The build of a zip source, as the version records it: a version enters no channel before `ready`. */
export function BuildBadge({ build }: { build: HostedBuild }) {
  const { tone, label } = BUILD[build.status];
  return (
    <span data-testid="hosted-build">
      <Badge tone={tone}>{label}</Badge>
      {build.status === "ready" && build.image && <span className="mt-1 block break-all font-mono text-ink-muted">{build.image}</span>}
      {build.status === "failed" && build.error && <span className="mt-1 block text-danger">{build.error}</span>}
    </span>
  );
}

/** A prompt, voice or hosted runtime (ADR 0080), in the "Model and runtime" group of a version. */
export function RuntimeRow({ runtime }: { runtime: PromptRuntime | VoiceRuntime | HostedRuntime }) {
  if (runtime.kind === "prompt")
    return (
      <span data-testid="runtime-prompt">
        Prompt · answers {runtime.response_format === "json_object" ? "a JSON object" : "text"} · tools {runtime.tool_choice ?? "auto"}
      </span>
    );
  if (runtime.kind === "voice")
    return (
      <span data-testid="runtime-voice">
        Voice · {runtime.voice ?? "alloy"} · {runtime.max_session_minutes ?? 15} min at most · sessions only
      </span>
    );
  const source = runtime.source;
  return (
    <span data-testid="runtime-hosted">
      <span className="rounded border border-line px-1.5 py-0.5">
        Hosted · {source.type === "image" ? "image" : `zip, ${source.runtime}`}
      </span>{" "}
      <span className="font-mono text-ink-muted">{(runtime.protocols ?? ["responses"]).map((p) => `/${p}`).join(" ")} :{runtime.port ?? 8088}</span>
      {source.type === "image" ? (
        <span className="mt-1 block break-all font-mono text-ink-muted" data-testid="hosted-image">
          {source.digest ? `${source.ref}@${source.digest}` : `${source.ref} (resolved at publication)`}
        </span>
      ) : (
        <span className="mt-1 block font-mono text-ink-muted">{source.entry_point.join(" ")}</span>
      )}
      {runtime.build && <BuildBadge build={runtime.build} />}
    </span>
  );
}
