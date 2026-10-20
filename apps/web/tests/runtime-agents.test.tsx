import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Suspense, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AgentPage from "@/app/agents/[slug]/page";
import { RuntimeAgentForm, argv } from "@/components/agents/runtime-agent-form";
import { BUILD_POLL_MS, RuntimeRow, buildInProgress } from "@/components/agents/runtime-row";
import { api } from "@/lib/api";
import type { Agent, AgentSpec, HostedBuild, HostedRuntime } from "@/lib/types";

/**
 * Prompt, voice and hosted agents in the console (S41-12, ADR 0080): one form per kind, what it sends,
 * what it refuses to send, the upload of a source zip, and the build of a version shown until it ends.
 */
const nav = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav,
  usePathname: () => "/agents/triager",
  useSearchParams: () => new URLSearchParams("tab=definition"),
}));
vi.mock("next/dynamic", () => ({ default: () => () => null }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: { email: "lea@acme.dev", memberships: [] } }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      agent: vi.fn(),
      agents: vi.fn().mockResolvedValue([]),
      agentMetrics: vi.fn().mockResolvedValue({ agent: "x", days: 30, runs: 0, succeeded: 0, failed: 0, cost_usd: 0 }),
      agentImages: vi.fn().mockResolvedValue([]),
      createAgent: vi.fn().mockResolvedValue({ slug: "triager" }),
      uploadHostedSource: vi.fn(),
    },
  };
});

const SHA = "sha256:" + "a".repeat(64);

function wrap(node: ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>);
}

function type(label: string | RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function named() {
  type("Name", "Triager");
  type("Instructions", "Sort the incoming issues.");
}

function sent(): { slug: string; kind: string; spec: AgentSpec; lifecycle: string } {
  const [org, body] = vi.mocked(api.createAgent).mock.calls.at(-1) as [string, never];
  expect(org).toBe("acme");
  return body;
}

const publish = () => screen.getByRole("button", { name: "Publish the agent" });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.createAgent).mockResolvedValue({ slug: "triager" } as Agent);
});

describe("a prompt agent", () => {
  it("is published as an internal agent whose runtime is the prompt loop", async () => {
    const onDone = vi.fn();
    wrap(<RuntimeAgentForm org="acme" kind="prompt" onDone={onDone} />);
    named();
    fireEvent.change(screen.getByLabelText("Answer"), { target: { value: "json_object" } });
    fireEvent.change(screen.getByLabelText("Tools"), { target: { value: "required" } });
    fireEvent.click(publish());
    await waitFor(() => expect(onDone).toHaveBeenCalledWith({ slug: "triager" }));
    const body = sent();
    expect(body.slug).toBe("triager");
    expect(body.kind).toBe("internal");
    expect(body.lifecycle).toBe("active");
    expect(body.spec.instructions).toBe("Sort the incoming issues.");
    expect(body.spec.runtime).toEqual({ kind: "prompt", response_format: "json_object", tool_choice: "required" });
    expect(body.spec).not.toHaveProperty("model");
  });

  it("is not sent without instructions, and says what is missing", () => {
    wrap(<RuntimeAgentForm org="acme" kind="prompt" onDone={vi.fn()} />);
    type("Name", "Triager");
    expect(publish()).toBeDisabled();
    expect(screen.getByTestId("missing")).toHaveTextContent("instructions");
  });

  it("is not sent with an identifier the API would refuse", () => {
    wrap(<RuntimeAgentForm org="acme" kind="prompt" onDone={vi.fn()} />);
    named();
    type("Identifier", "Triager_1");
    expect(publish()).toBeDisabled();
    expect(screen.getByTestId("missing")).toHaveTextContent("an identifier");
  });

  it("Save as draft sends the same agent, as a draft", async () => {
    wrap(<RuntimeAgentForm org="acme" kind="prompt" onDone={vi.fn()} />);
    named();
    fireEvent.click(screen.getByRole("button", { name: "Save as draft" }));
    await waitFor(() => expect(api.createAgent).toHaveBeenCalled());
    expect(sent().lifecycle).toBe("draft");
  });

  it("a refusal stays on the page", async () => {
    vi.mocked(api.createAgent).mockRejectedValue(new Error("the model is not in the catalogue"));
    const onDone = vi.fn();
    wrap(<RuntimeAgentForm org="acme" kind="prompt" onDone={onDone} />);
    named();
    fireEvent.click(publish());
    expect(await screen.findByRole("alert")).toHaveTextContent("the model is not in the catalogue");
    expect(onDone).not.toHaveBeenCalled();
  });
});

describe("a voice agent", () => {
  it("needs a realtime model", () => {
    wrap(<RuntimeAgentForm org="acme" kind="voice" onDone={vi.fn()} />);
    named();
    expect(publish()).toBeDisabled();
    expect(screen.getByTestId("missing")).toHaveTextContent("a realtime model");
  });

  it("sends its voice, its greeting and a call of 120 minutes at most", async () => {
    wrap(<RuntimeAgentForm org="acme" kind="voice" onDone={vi.fn()} />);
    named();
    type("Model", "openai/gpt-realtime");
    type("Voice", "verse");
    type("Greeting", "Hello, how can I help?");
    fireEvent.change(screen.getByLabelText("Turns"), { target: { value: "none" } });
    type("Longest call (minutes)", "500");
    fireEvent.click(publish());
    await waitFor(() => expect(api.createAgent).toHaveBeenCalled());
    const { spec } = sent();
    expect(spec.model).toBe("openai/gpt-realtime");
    expect(spec.runtime).toEqual({
      kind: "voice",
      voice: "verse",
      greeting: "Hello, how can I help?",
      turn_detection: "none",
      max_session_minutes: 120,
    });
  });

  it("says it is spoken to in a session, never run as a step", () => {
    wrap(<RuntimeAgentForm org="acme" kind="voice" onDone={vi.fn()} />);
    expect(screen.getByTestId("voice-note")).toHaveTextContent("never run as a workflow step");
  });
});

describe("a hosted agent", () => {
  it("from an image sends the reference, its protocols and its port, and no digest", async () => {
    wrap(<RuntimeAgentForm org="acme" kind="hosted" onDone={vi.fn()} />);
    type("Name", "Triager");
    type("Image", "ghcr.io/acme/triager:1.2");
    fireEvent.click(screen.getByLabelText("POST /invocations"));
    fireEvent.click(publish());
    await waitFor(() => expect(api.createAgent).toHaveBeenCalled());
    expect(sent().spec.runtime).toEqual({
      kind: "hosted",
      source: { type: "image", ref: "ghcr.io/acme/triager:1.2" },
      protocols: ["responses", "invocations"],
      port: 8088,
      readiness_timeout_s: 120,
    });
  });

  it("refuses the runner's ports and an agent that serves nothing", () => {
    wrap(<RuntimeAgentForm org="acme" kind="hosted" onDone={vi.fn()} />);
    type("Name", "Triager");
    type("Image", "ghcr.io/acme/triager:1.2");
    type("Port", "4002");
    expect(screen.getByText("4001 to 4003 are the runner's.")).toBeInTheDocument();
    expect(publish()).toBeDisabled();
    type("Port", "8080");
    expect(publish()).toBeEnabled();
    fireEvent.click(screen.getByLabelText("POST /responses"));
    expect(publish()).toBeDisabled();
    expect(screen.getByTestId("missing")).toHaveTextContent("a protocol");
  });

  it("from a zip uploads it first, then sends its hash, its runtime and its entry point", async () => {
    vi.mocked(api.uploadHostedSource).mockResolvedValue({ sha256: SHA, size_bytes: 812, files: ["main.py", "requirements.txt"], dependencies: "requirements.txt" });
    wrap(<RuntimeAgentForm org="acme" kind="hosted" onDone={vi.fn()} />);
    type("Name", "Triager");
    fireEvent.change(screen.getByLabelText("Source"), { target: { value: "zip" } });
    expect(publish()).toBeDisabled();
    expect(screen.getByTestId("missing")).toHaveTextContent("an uploaded zip");
    const archive = new File(["PK"], "agent.zip", { type: "application/zip" });
    fireEvent.change(screen.getByLabelText("Zip"), { target: { files: [archive] } });
    const summary = await screen.findByTestId("zip-summary");
    expect(api.uploadHostedSource).toHaveBeenCalledWith("acme", archive);
    expect(summary).toHaveTextContent("2 files");
    expect(summary).toHaveTextContent("dependencies in requirements.txt");
    fireEvent.change(screen.getByLabelText("Runtime"), { target: { value: "python_3_12" } });
    type("Entry point", "  python   main.py --port 8088 ");
    fireEvent.click(publish());
    await waitFor(() => expect(api.createAgent).toHaveBeenCalled());
    const runtime = sent().spec.runtime as HostedRuntime;
    expect(runtime.source).toEqual({
      type: "zip",
      sha256: SHA,
      runtime: "python_3_12",
      entry_point: ["python", "main.py", "--port", "8088"],
      dependency_resolution: "remote_build",
    });
    expect(runtime).not.toHaveProperty("build");
  });

  it("a zip that declares no dependencies must carry them", async () => {
    vi.mocked(api.uploadHostedSource).mockResolvedValue({ sha256: SHA, size_bytes: 812, files: ["main.py"], dependencies: null });
    wrap(<RuntimeAgentForm org="acme" kind="hosted" onDone={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Source"), { target: { value: "zip" } });
    fireEvent.change(screen.getByLabelText("Zip"), { target: { files: [new File(["PK"], "agent.zip")] } });
    expect(await screen.findByTestId("zip-summary")).toHaveTextContent("the zip must carry them");
    expect(screen.getByLabelText("Dependencies")).toHaveValue("bundled");
    expect(screen.getByRole("option", { name: "Installed when the image is built" })).toBeDisabled();
  });

  it("a refused zip is said, and nothing is published from it", async () => {
    vi.mocked(api.uploadHostedSource).mockRejectedValue(new Error("an entry leaves the archive: ../etc/passwd"));
    wrap(<RuntimeAgentForm org="acme" kind="hosted" onDone={vi.fn()} />);
    type("Name", "Triager");
    fireEvent.change(screen.getByLabelText("Source"), { target: { value: "zip" } });
    fireEvent.change(screen.getByLabelText("Zip"), { target: { files: [new File(["PK"], "agent.zip")] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("an entry leaves the archive");
    expect(screen.queryByTestId("zip-summary")).toBeNull();
    expect(publish()).toBeDisabled();
  });

  it("offers only the runtimes the platform has a base image for", () => {
    wrap(<RuntimeAgentForm org="acme" kind="hosted" onDone={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Source"), { target: { value: "zip" } });
    const options = within(screen.getByLabelText("Runtime")).getAllByRole("option") as HTMLOptionElement[];
    expect(options.filter((o) => !o.disabled).map((o) => o.value)).toEqual(["python_3_13", "python_3_12"]);
  });

  it("says the container holds no credential", () => {
    wrap(<RuntimeAgentForm org="acme" kind="hosted" onDone={vi.fn()} />);
    expect(screen.getByTestId("hosted-note")).toHaveTextContent("holds no credential");
  });

  it("splits an entry point on whitespace, as an exec form", () => {
    expect(argv("  node  dist/main.js ")).toEqual(["node", "dist/main.js"]);
    expect(argv("   ")).toEqual([]);
  });
});

const zipRuntime = (build: HostedBuild | null): HostedRuntime => ({
  kind: "hosted",
  source: { type: "zip", sha256: SHA, runtime: "python_3_13", entry_point: ["python", "main.py"] },
  protocols: ["responses"],
  port: 8088,
  build,
});

describe("a version's runtime", () => {
  it("an image shows the digest runs pull", () => {
    wrap(<RuntimeRow runtime={{ kind: "hosted", source: { type: "image", ref: "ghcr.io/acme/triager:1", digest: SHA } }} />);
    expect(screen.getByTestId("hosted-image")).toHaveTextContent(`ghcr.io/acme/triager:1@${SHA}`);
  });

  it("a ready build shows its image, a failed one its error", () => {
    const { unmount } = wrap(<RuntimeRow runtime={zipRuntime({ status: "ready", key: SHA, image: `registry.local/hosted@${SHA}` })} />);
    expect(screen.getByTestId("hosted-build")).toHaveTextContent("built");
    expect(screen.getByTestId("hosted-build")).toHaveTextContent(`registry.local/hosted@${SHA}`);
    unmount();
    wrap(<RuntimeRow runtime={zipRuntime({ status: "failed", key: SHA, error: "pip install exited 1" })} />);
    expect(screen.getByTestId("hosted-build")).toHaveTextContent("build failed");
    expect(screen.getByTestId("hosted-build")).toHaveTextContent("pip install exited 1");
  });

  it("a build is in progress while pending or building, and only for a hosted runtime", () => {
    expect(buildInProgress(zipRuntime({ status: "pending", key: SHA }))).toBe(true);
    expect(buildInProgress(zipRuntime({ status: "building", key: SHA }))).toBe(true);
    expect(buildInProgress(zipRuntime({ status: "ready", key: SHA }))).toBe(false);
    expect(buildInProgress(zipRuntime({ status: "failed", key: SHA }))).toBe(false);
    expect(buildInProgress(zipRuntime(null))).toBe(false);
    expect(buildInProgress({ kind: "prompt" })).toBe(false);
  });
});

const triager = (build: HostedBuild): Agent =>
  ({
    slug: "triager",
    kind: "internal",
    display_name: "Triager",
    status: "active",
    lifecycle: "active",
    latest_version: 1,
    scope: "org",
    can: { publish: true, manage: true, pin: true, fork_scopes: [] },
    versions: [{ version: 1, spec: { instructions: "Sort.", runtime: zipRuntime(build) }, checksum: "sha256:1", created_by: "lea@acme.dev", pinned_by: [] }],
  }) as Agent;

describe("the agent's page during a build", () => {
  afterEach(() => vi.useRealTimers());

  async function open() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => {
      render(
        <QueryClientProvider client={client}>
          <Suspense fallback="…">
            <AgentPage params={Promise.resolve({ slug: "triager" })} />
          </Suspense>
        </QueryClientProvider>,
      );
    });
  }

  it("asks again until the build has ended, then stops", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.agent)
      .mockResolvedValueOnce(triager({ status: "building", key: SHA }))
      .mockResolvedValue(triager({ status: "ready", key: SHA, image: `registry.local/hosted@${SHA}` }));
    await open();
    await screen.findByTestId("runtime-hosted");
    expect(screen.getByTestId("hosted-build")).toHaveTextContent("building");
    await act(() => vi.advanceTimersByTimeAsync(BUILD_POLL_MS + 100));
    await waitFor(() => expect(screen.getByTestId("hosted-build")).toHaveTextContent("built"));
    const calls = vi.mocked(api.agent).mock.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(BUILD_POLL_MS * 3));
    expect(vi.mocked(api.agent).mock.calls.length).toBe(calls);
  });

  it("a ready version is not asked again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.agent).mockResolvedValue(triager({ status: "ready", key: SHA }));
    await open();
    await screen.findByTestId("runtime-hosted");
    await act(() => vi.advanceTimersByTimeAsync(BUILD_POLL_MS * 3));
    expect(api.agent).toHaveBeenCalledTimes(1);
  });
});
