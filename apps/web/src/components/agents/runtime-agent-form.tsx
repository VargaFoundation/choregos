// SPDX-License-Identifier: Apache-2.0
"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { slugFromName } from "@/components/agents/builder";
import { Alert, Badge, Button, Card, Field, Input, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import type { AgentSpec, HostedRuntime, HostedSource, HostedZipSource, PromptRuntime, VoiceRuntime } from "@/lib/types";

export type RuntimeKind = "prompt" | "voice" | "hosted";

export const RUNTIME_LABEL: Record<RuntimeKind, string> = {
  prompt: "Prompt agent",
  voice: "Voice agent",
  hosted: "Hosted agent",
};

/** The zip runtimes the platform has a base image for; the others are refused at publication (S41-20). */
export const ZIP_RUNTIMES: { value: HostedZipSource["runtime"]; label: string; available: boolean }[] = [
  { value: "python_3_13", label: "Python 3.13", available: true },
  { value: "python_3_12", label: "Python 3.12", available: true },
  { value: "python_3_14", label: "Python 3.14 (not yet)", available: false },
  { value: "node_22", label: "Node 22 (not yet)", available: false },
  { value: "dotnet_10", label: ".NET 10 (not yet)", available: false },
];

const PROTOCOLS = ["responses", "invocations"] as const;
const SLUG = /^[a-z][a-z0-9-]{1,62}$/;
/** The runner's loopback listeners: a container on one of them is refused (ADR 0080). */
const RESERVED_PORTS = new Set([4001, 4002, 4003]);

/** `python main.py --port 8088` → its argv; quotes are not interpreted, as in a Dockerfile's exec form. */
export function argv(text: string): string[] {
  return text.trim().split(/\s+/).filter(Boolean);
}

function whole(text: string, fallback: number): number {
  const value = Math.round(Number(text));
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

/**
 * A prompt, voice or hosted agent (ADR 0080, S41-12): the shapes of Foundry's Agent Service, so that an
 * agent built for it is registered here unchanged. A prompt agent is instructions, a model and tools; a
 * voice agent the same, spoken, through a realtime model; a hosted agent is its own container, from an
 * image the operator's registries allow or from a source zip the platform builds at publication.
 */
export function RuntimeAgentForm({
  org,
  kind,
  onDone,
}: {
  org: string;
  kind: RuntimeKind;
  onDone: (agent: { slug: string }) => void;
}) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [model, setModel] = useState("");
  // prompt
  const [responseFormat, setResponseFormat] = useState<NonNullable<PromptRuntime["response_format"]>>("text");
  const [toolChoice, setToolChoice] = useState<NonNullable<PromptRuntime["tool_choice"]>>("auto");
  // voice
  const [voice, setVoice] = useState("alloy");
  const [greeting, setGreeting] = useState("");
  const [turnDetection, setTurnDetection] = useState<NonNullable<VoiceRuntime["turn_detection"]>>("server_vad");
  const [minutes, setMinutes] = useState("15");
  // hosted
  const [sourceType, setSourceType] = useState<"image" | "zip">("image");
  const [imageRef, setImageRef] = useState("");
  const [zip, setZip] = useState<HostedSource | null>(null);
  const [zipRuntime, setZipRuntime] = useState<HostedZipSource["runtime"]>("python_3_13");
  const [entryPoint, setEntryPoint] = useState("python main.py");
  const [resolution, setResolution] = useState<NonNullable<HostedZipSource["dependency_resolution"]>>("remote_build");
  const [protocols, setProtocols] = useState<string[]>(["responses"]);
  const [port, setPort] = useState("8088");
  const [readiness, setReadiness] = useState("120");
  const [error, setError] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadHostedSource(org, file),
    onSuccess: (source) => {
      setZip(source);
      setError(null);
      if (source.dependencies == null) setResolution("bundled");
    },
    onError: (cause) => {
      setZip(null);
      setError(cause instanceof Error ? cause.message : "refused");
    },
  });

  const hostedRuntime = (): HostedRuntime => ({
    kind: "hosted",
    source:
      sourceType === "image"
        ? { type: "image", ref: imageRef.trim() }
        : { type: "zip", sha256: zip?.sha256 ?? "", runtime: zipRuntime, entry_point: argv(entryPoint), dependency_resolution: resolution },
    protocols: protocols as HostedRuntime["protocols"],
    port: whole(port, 8088),
    readiness_timeout_s: whole(readiness, 120),
  });

  const runtime = (): AgentSpec["runtime"] => {
    if (kind === "prompt") return { kind: "prompt", response_format: responseFormat, tool_choice: toolChoice };
    if (kind === "voice")
      return {
        kind: "voice",
        voice: voice.trim() || "alloy",
        greeting: greeting.trim() || null,
        turn_detection: turnDetection,
        max_session_minutes: Math.min(120, whole(minutes, 15)),
      };
    return hostedRuntime();
  };

  const spec = (): AgentSpec => ({ instructions, ...(model.trim() && { model: model.trim() }), runtime: runtime() });

  const publish = useMutation({
    mutationFn: (lifecycle: "active" | "draft") =>
      api.createAgent(org, { slug, kind: "internal", display_name: name.trim(), description: description.trim() || null, spec: spec(), lifecycle }),
    onSuccess: (agent) => onDone(agent),
    onError: (cause) => setError(cause instanceof Error ? cause.message : "refused"),
  });

  const portNumber = whole(port, 8088);
  const missing: string[] = [];
  if (!name.trim() || !SLUG.test(slug)) missing.push("a name and an identifier");
  if (kind !== "hosted" && !instructions.trim()) missing.push("instructions");
  if (kind === "voice" && !model.trim()) missing.push("a realtime model");
  if (kind === "hosted") {
    if (sourceType === "image" && !imageRef.trim()) missing.push("an image");
    if (sourceType === "zip" && !zip) missing.push("an uploaded zip");
    if (sourceType === "zip" && argv(entryPoint).length === 0) missing.push("an entry point");
    if (protocols.length === 0) missing.push("a protocol");
    if (RESERVED_PORTS.has(portNumber)) missing.push("a port other than 4001-4003");
  }
  const ready = missing.length === 0;

  return (
    <div className="space-y-4" data-testid="runtime-agent-form">
      <Card title="1. The agent" action={<Badge tone="accent">{RUNTIME_LABEL[kind]}</Badge>}>
        <div className="grid max-w-3xl gap-3">
          <Field label="Name">
            {(props) => (
              <Input
                {...props}
                value={name}
                onChange={(e) => {
                  setName(e.target.value);
                  setSlug(slugFromName(e.target.value));
                }}
              />
            )}
          </Field>
          <Field label="Identifier" hint="The name workflows give: agent: <identifier>">
            {(props) => <Input {...props} value={slug} onChange={(e) => setSlug(e.target.value)} className="font-mono" />}
          </Field>
          <Field label="Description">
            {(props) => <Input {...props} value={description} onChange={(e) => setDescription(e.target.value)} />}
          </Field>
          <Field
            label="Instructions"
            hint={kind === "hosted" ? "Sent to the container with the task. The container's own code decides what it does with them." : undefined}
          >
            {(props) => <Textarea {...props} rows={5} value={instructions} onChange={(e) => setInstructions(e.target.value)} />}
          </Field>
          <Field
            label="Model"
            hint={
              kind === "voice"
                ? "A realtime model of the catalogue. The organisation's gateway must declare the realtime capability."
                : kind === "hosted"
                  ? "Whatever model the container names, its calls reach this one, through the runner. Empty: the project's profile."
                  : "Empty: the project's profile."
            }
          >
            {(props) => (
              <Input
                {...props}
                value={model}
                onChange={(e) => setModel(e.target.value)}
                className="font-mono"
                placeholder={kind === "voice" ? "openai/gpt-realtime" : "openai/gpt-4.1-mini"}
              />
            )}
          </Field>
        </div>
      </Card>

      {kind === "prompt" && (
        <Card title="2. How it answers">
          <div className="grid max-w-3xl gap-3 sm:grid-cols-2">
            <Field label="Answer" hint="A JSON object is read as the step's outputs.">
              {(props) => (
                <Select {...props} value={responseFormat} onChange={(e) => setResponseFormat(e.target.value as typeof responseFormat)}>
                  <option value="text">Text</option>
                  <option value="json_object">A JSON object</option>
                </Select>
              )}
            </Field>
            <Field label="Tools">
              {(props) => (
                <Select {...props} value={toolChoice} onChange={(e) => setToolChoice(e.target.value as typeof toolChoice)}>
                  <option value="auto">The model chooses</option>
                  <option value="required">A tool at each turn</option>
                  <option value="none">No tool</option>
                </Select>
              )}
            </Field>
          </div>
          <p className="mt-3 text-xs text-ink-muted">No coding agent and no container: the platform runs the tool loop. Tools and knowledge are added on the agent&apos;s page.</p>
        </Card>
      )}

      {kind === "voice" && (
        <Card title="2. How it speaks">
          <div className="grid max-w-3xl gap-3 sm:grid-cols-2">
            <Field label="Voice">
              {(props) => <Input {...props} value={voice} onChange={(e) => setVoice(e.target.value)} className="font-mono" />}
            </Field>
            <Field label="Turns" hint="Who decides that the caller has finished speaking.">
              {(props) => (
                <Select {...props} value={turnDetection} onChange={(e) => setTurnDetection(e.target.value as typeof turnDetection)}>
                  <option value="server_vad">The model hears silence</option>
                  <option value="none">The caller says so</option>
                </Select>
              )}
            </Field>
            <Field label="Greeting" hint="What the agent says first.">
              {(props) => <Input {...props} value={greeting} onChange={(e) => setGreeting(e.target.value)} />}
            </Field>
            <Field label="Longest call (minutes)" hint="120 at most. The call is closed then.">
              {(props) => <Input {...props} inputMode="numeric" value={minutes} onChange={(e) => setMinutes(e.target.value)} />}
            </Field>
          </div>
          <p className="mt-3 text-xs text-ink-muted" data-testid="voice-note">
            A voice agent is spoken to in a session, never run as a workflow step. Transcripts are kept; audio is relayed and never kept.
          </p>
        </Card>
      )}

      {kind === "hosted" && (
        <Card title="2. Its container">
          <div className="grid max-w-3xl gap-3">
            <Field label="Source">
              {(props) => (
                <Select {...props} value={sourceType} onChange={(e) => setSourceType(e.target.value as "image" | "zip")}>
                  <option value="image">An image</option>
                  <option value="zip">A source zip the platform builds</option>
                </Select>
              )}
            </Field>
            {sourceType === "image" ? (
              <Field label="Image" hint="Under a registry the operator allows. The tag is resolved to its digest at publication, and runs pull that digest.">
                {(props) => (
                  <Input {...props} value={imageRef} onChange={(e) => setImageRef(e.target.value)} className="font-mono" placeholder="ghcr.io/acme/triager:1.2" />
                )}
              </Field>
            ) : (
              <>
                <Field label="Zip" hint="Flat: the agent's files at the root. 64 MiB and 2,000 files at most. A Dockerfile in it is not read.">
                  {(props) => (
                    <input
                      {...props}
                      type="file"
                      accept=".zip,application/zip"
                      className="block text-sm"
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        if (file) upload.mutate(file);
                      }}
                    />
                  )}
                </Field>
                {upload.isPending && <p role="status" className="text-xs text-ink-muted">Uploading and checking the zip…</p>}
                {zip && (
                  <p className="text-xs" data-testid="zip-summary">
                    <span className="font-mono">{zip.sha256.slice(0, 19)}…</span> · {zip.files.length} file{zip.files.length === 1 ? "" : "s"} ·{" "}
                    {zip.dependencies ? `dependencies in ${zip.dependencies}` : "no requirements.txt or pyproject.toml: the zip must carry them"}
                  </p>
                )}
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Runtime">
                    {(props) => (
                      <Select {...props} value={zipRuntime} onChange={(e) => setZipRuntime(e.target.value as typeof zipRuntime)}>
                        {ZIP_RUNTIMES.map((r) => (
                          <option key={r.value} value={r.value} disabled={!r.available}>
                            {r.label}
                          </option>
                        ))}
                      </Select>
                    )}
                  </Field>
                  <Field label="Dependencies">
                    {(props) => (
                      <Select {...props} value={resolution} onChange={(e) => setResolution(e.target.value as typeof resolution)}>
                        <option value="remote_build" disabled={zip !== null && zip.dependencies == null}>
                          Installed when the image is built
                        </option>
                        <option value="bundled">Carried by the zip, under site-packages/</option>
                      </Select>
                    )}
                  </Field>
                </div>
                <Field label="Entry point" hint="The command the image starts, as words separated by spaces.">
                  {(props) => <Input {...props} value={entryPoint} onChange={(e) => setEntryPoint(e.target.value)} className="font-mono" />}
                </Field>
              </>
            )}
            <fieldset className="space-y-1">
              <legend className="text-xs text-ink-muted">What it serves</legend>
              {PROTOCOLS.map((protocol) => (
                <label key={protocol} className="mr-4 inline-flex items-center gap-1.5 text-sm">
                  <input
                    type="checkbox"
                    checked={protocols.includes(protocol)}
                    onChange={(e) => setProtocols((current) => (e.target.checked ? [...current, protocol] : current.filter((p) => p !== protocol)))}
                  />
                  <span className="font-mono">POST /{protocol}</span>
                </label>
              ))}
            </fieldset>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Port" error={RESERVED_PORTS.has(portNumber) ? "4001 to 4003 are the runner's." : undefined}>
                {(props) => <Input {...props} inputMode="numeric" value={port} onChange={(e) => setPort(e.target.value)} />}
              </Field>
              <Field label="Ready within (seconds)" hint="GET /readiness must answer by then.">
                {(props) => <Input {...props} inputMode="numeric" value={readiness} onChange={(e) => setReadiness(e.target.value)} />}
              </Field>
            </div>
            <p className="text-xs text-ink-muted" data-testid="hosted-note">
              The container holds no credential: it reaches the model, its MCP servers and the internet through the runner, on 127.0.0.1.
              {sourceType === "zip" && " The image is built at publication; a version enters no channel before its build is ready."}
            </p>
          </div>
        </Card>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button tone="primary" disabled={!ready || publish.isPending || upload.isPending} onClick={() => publish.mutate("active")}>
          Publish the agent
        </Button>
        <Button disabled={!ready || publish.isPending || upload.isPending} onClick={() => publish.mutate("draft")}>
          Save as draft
        </Button>
        {!ready && <span className="text-xs text-ink-muted" data-testid="missing">Still needed: {missing.join(", ")}.</span>}
      </div>
      {error && (
        <Alert tone="danger" title="Refused">
          {error}
        </Alert>
      )}
    </div>
  );
}
