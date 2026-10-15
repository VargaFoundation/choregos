import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RemoteCredentials, statusLine } from "@/components/remote/credentials";
import { RemoteAgentForm } from "@/components/remote/remote-agent-form";
import { CardChangedWarning, RemoteDefinition } from "@/components/remote/remote-definition";
import { RemoteTaskCard } from "@/components/remote/remote-task";
import { api } from "@/lib/api";
import { remoteTask } from "@/lib/remote-run";
import { agents, remoteCredentials, rovoDiscovery } from "@/mocks/data";
import type { A2aRuntime, RemoteCredential } from "@/lib/types";

vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams() }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      remoteCredentials: vi.fn(),
      createRemoteCredential: vi.fn().mockResolvedValue({ name: "x", type: "bearer", status: "connected" }),
      authorizeRemoteCredential: vi.fn(),
      revokeRemoteCredential: vi.fn().mockResolvedValue(undefined),
      discoverRemoteAgent: vi.fn(),
      createAgent: vi.fn().mockResolvedValue({ slug: "atlassian-rovo" }),
      publishAgentVersion: vi.fn().mockResolvedValue({ version: 2 }),
    },
  };
});

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>);
}

describe("the remote credentials page (ADR 0062)", () => {
  beforeEach(() => {
    vi.mocked(api.remoteCredentials).mockResolvedValue(remoteCredentials);
    vi.mocked(api.authorizeRemoteCredential).mockReset();
    vi.mocked(api.revokeRemoteCredential).mockClear();
    vi.mocked(api.createRemoteCredential).mockClear();
  });

  it("lists each credential with its state and whose permissions it carries", async () => {
    wrap(<RemoteCredentials org="acme" />);
    const table = await screen.findByTestId("remote-credentials");
    expect(within(table).getByText("atlassian-rovo")).toBeInTheDocument();
    expect(screen.getByTestId("status-atlassian-rovo")).toHaveTextContent("Acts as choregos-bot@acme.dev");
    expect(screen.getByTestId("status-partner-agent")).toHaveTextContent("Registered, not yet authorised");
    expect(table.textContent).not.toMatch(/secret|token value|\bat-/i);
  });

  it("Connect sends the browser to the provider's consent page", async () => {
    vi.mocked(api.authorizeRemoteCredential).mockResolvedValue({ redirect_url: "https://auth.example/authorize?state=s" });
    const navigate = vi.fn();
    wrap(<RemoteCredentials org="acme" navigate={navigate} />);
    const row = (await screen.findByText("partner-agent")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("https://auth.example/authorize?state=s"));
    expect(api.authorizeRemoteCredential).toHaveBeenCalledWith("acme", "partner-agent");
  });

  it("a connected credential offers Reconnect, not Connect", async () => {
    wrap(<RemoteCredentials org="acme" />);
    const row = (await screen.findByText("atlassian-rovo")).closest("tr")!;
    expect(within(row).getByRole("button", { name: "Reconnect" })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Connect" })).toBeNull();
  });

  it("an error from the provider stays on the page", async () => {
    vi.mocked(api.authorizeRemoteCredential).mockRejectedValue(new Error("the provider is unreachable"));
    wrap(<RemoteCredentials org="acme" navigate={vi.fn()} />);
    const row = (await screen.findByText("partner-agent")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Connect" }));
    expect(await screen.findByText("the provider is unreachable")).toBeInTheDocument();
  });

  it("revoking asks first, then says what is lost", async () => {
    wrap(<RemoteCredentials org="acme" />);
    const row = (await screen.findByText("atlassian-rovo")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Revoke" }));
    expect(screen.getByTestId("confirmation")).toHaveTextContent(/sealed secrets are deleted/i);
    expect(api.revokeRemoteCredential).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Revoke the credential" }));
    await waitFor(() => expect(api.revokeRemoteCredential).toHaveBeenCalledWith("acme", "atlassian-rovo"));
  });

  it("adds an OAuth credential from a card, and a bearer token is a password field that is sent once", async () => {
    wrap(<RemoteCredentials org="acme" />);
    await screen.findByTestId("remote-credentials");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "atlassian-two" } });
    fireEvent.change(screen.getByLabelText(/Agent card URL/), { target: { value: "https://a2a.atlassian.com/.well-known/agent.json" } });
    fireEvent.click(screen.getByRole("button", { name: "Add the credential" }));
    await waitFor(() =>
      expect(api.createRemoteCredential).toHaveBeenCalledWith("acme", {
        name: "atlassian-two",
        type: "oauth2_authorization_code",
        card_url: "https://a2a.atlassian.com/.well-known/agent.json",
      }),
    );
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "bearer" } });
    const token = screen.getByLabelText("Token");
    expect(token).toHaveAttribute("type", "password");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "static-one" } });
    fireEvent.change(token, { target: { value: "the-typed-value" } });
    fireEvent.click(screen.getByRole("button", { name: "Add the credential" }));
    await waitFor(() => expect(api.createRemoteCredential).toHaveBeenLastCalledWith("acme", { name: "static-one", type: "bearer", token: "the-typed-value" }));
    await waitFor(() => expect(screen.getByLabelText("Name")).toHaveValue(""));
  });

  it("an OAuth credential cannot be added without an https card", async () => {
    wrap(<RemoteCredentials org="acme" />);
    await screen.findByTestId("remote-credentials");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "atlassian-two" } });
    fireEvent.change(screen.getByLabelText(/Agent card URL/), { target: { value: "http://plain.example/card" } });
    expect(screen.getByRole("button", { name: "Add the credential" })).toBeDisabled();
  });

  it("says it plainly when the provider just sent the administrator back", async () => {
    wrap(<RemoteCredentials org="acme" connected="atlassian-rovo" />);
    expect(await screen.findByTestId("connected-note")).toHaveTextContent(/atlassian-rovo.*authorised/);
  });

  it("each status reads as a sentence", () => {
    const base = { name: "x", type: "bearer" } as RemoteCredential;
    expect(statusLine({ ...base, status: "error", last_error: "refused (HTTP 400): reconnect" })).toBe("refused (HTTP 400): reconnect");
    expect(statusLine({ ...base, status: "revoked" })).toBe("Revoked");
  });
});

describe("registering a remote agent from its card (ADR 0062)", () => {
  beforeEach(() => {
    vi.mocked(api.discoverRemoteAgent).mockReset().mockResolvedValue(rovoDiscovery);
    vi.mocked(api.createAgent).mockClear();
    vi.mocked(api.publishAgentVersion).mockClear();
  });

  async function read(onDone = vi.fn()) {
    wrap(<RemoteAgentForm org="acme" onDone={onDone} />);
    fireEvent.change(screen.getByLabelText(/Card URL/), { target: { value: "https://a2a.atlassian.com/.well-known/agent.json" } });
    fireEvent.click(screen.getByRole("button", { name: "Read the card" }));
    await screen.findByTestId("card-summary");
    return onDone;
  }

  it("shows what the card says before anything is created", async () => {
    await read();
    expect(api.discoverRemoteAgent).toHaveBeenCalledWith("acme", "https://a2a.atlassian.com/.well-known/agent.json");
    expect(api.createAgent).not.toHaveBeenCalled();
    const skills = screen.getByTestId("card-skills");
    expect(within(skills).getByText("work-in-jira")).toBeInTheDocument();
    expect(within(skills).getByText("work-in-confluence")).toBeInTheDocument();
    expect(screen.getByTestId("card-auth")).toHaveTextContent("OAuth (a person authorises)");
    expect(screen.getByTestId("egress-note")).toHaveTextContent("a2a.atlassian.com");
  });

  it("publishes a remote agent with its skill, credential, time limit and declared cost", async () => {
    const onDone = await read();
    fireEvent.change(screen.getByLabelText("Skill"), { target: { value: "work-in-jira" } });
    fireEvent.change(screen.getByLabelText(/Cost per task/), { target: { value: "0.05" } });
    fireEvent.change(screen.getByLabelText("Time limit (minutes)"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "Be brief." } });
    fireEvent.click(screen.getByRole("button", { name: "Publish the agent" }));
    await waitFor(() => expect(api.createAgent).toHaveBeenCalledTimes(1));
    const [org, body] = vi.mocked(api.createAgent).mock.calls[0]!;
    expect(org).toBe("acme");
    expect(body).toMatchObject({ slug: "atlassian-rovo", kind: "remote", lifecycle: "active" });
    expect(body.spec!.runtime).toEqual({
      kind: "a2a",
      card_url: "https://a2a.atlassian.com/.well-known/agent.json",
      skill: "work-in-jira",
      credential: "atlassian-rovo",
      timeout_minutes: 10,
      declared_cost: { per_task_usd: 0.05, per_minute_usd: null },
      input: "text",
    });
    expect(body.spec!.instructions).toBe("Be brief.");
    expect(body.spec!.runtime).not.toHaveProperty("pinned_card");
    await waitFor(() => expect(onDone).toHaveBeenCalledWith({ slug: "atlassian-rovo" }));
  });

  it("an empty cost field declares nothing, which is not zero", async () => {
    await read();
    fireEvent.click(screen.getByRole("button", { name: "Save as draft" }));
    await waitFor(() => expect(api.createAgent).toHaveBeenCalled());
    const [, body] = vi.mocked(api.createAgent).mock.calls[0]!;
    expect((body.spec!.runtime as A2aRuntime).declared_cost).toEqual({ per_task_usd: null, per_minute_usd: null });
    expect(body.lifecycle).toBe("draft");
    expect(screen.getByTestId("cost-note")).toHaveTextContent(/unmeasured/);
  });

  it("with no credential that fits the card it says so and cannot publish", async () => {
    vi.mocked(api.discoverRemoteAgent).mockResolvedValue({ ...rovoDiscovery, credentials: [], proposed: { ...rovoDiscovery.proposed, spec: { runtime: { kind: "a2a", card_url: "https://a2a.atlassian.com/.well-known/agent.json" } } } });
    await read();
    expect(screen.getByTestId("no-credential")).toHaveTextContent(/administration/);
    expect(screen.getByRole("button", { name: "Publish the agent" })).toBeDisabled();
  });

  it("a card that cannot be read shows why and offers nothing else", async () => {
    vi.mocked(api.discoverRemoteAgent).mockRejectedValue(new Error("the agent card cannot be used: the card answered HTTP 404"));
    wrap(<RemoteAgentForm org="acme" onDone={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/Card URL/), { target: { value: "https://nowhere.example/card.json" } });
    fireEvent.click(screen.getByRole("button", { name: "Read the card" }));
    expect(await screen.findByText(/answered HTTP 404/)).toBeInTheDocument();
    expect(screen.queryByTestId("card-summary")).toBeNull();
  });

  it("leaves a place for a per-type procedure above the card step", () => {
    wrap(<RemoteAgentForm org="acme" onDone={vi.fn()} procedure={<p>Set up Rovo in four steps</p>} />);
    expect(screen.getByTestId("remote-procedure")).toHaveTextContent("Set up Rovo");
  });

  it("only an https card can be read", () => {
    wrap(<RemoteAgentForm org="acme" onDone={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/Card URL/), { target: { value: "http://plain.example/card" } });
    expect(screen.getByRole("button", { name: "Read the card" })).toBeDisabled();
  });

  it("the next version of an existing remote agent is the card read again, published on the same agent", async () => {
    const rovo = agents.find((a) => a.slug === "rovo")!;
    wrap(<RemoteAgentForm org="acme" existing={{ agent: rovo, spec: rovo.versions![0]!.spec }} onDone={vi.fn()} />);
    expect(screen.getByLabelText(/Card URL/)).toHaveValue("https://a2a.atlassian.com/.well-known/agent.json");
    fireEvent.click(screen.getByRole("button", { name: "Read the card" }));
    await screen.findByTestId("card-summary");
    expect(screen.queryByLabelText("Identifier")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Publish version 2" }));
    await waitFor(() => expect(api.publishAgentVersion).toHaveBeenCalledTimes(1));
    const [, slug, spec] = vi.mocked(api.publishAgentVersion).mock.calls[0]!;
    expect(slug).toBe("rovo");
    expect((spec.runtime as A2aRuntime).skill).toBe("work-in-jira");
    expect(api.createAgent).not.toHaveBeenCalled();
  });
});

describe("a remote agent, on its page and in its runs", () => {
  it("its version shows the endpoint, the pinned card digest, the credential and the declared cost", () => {
    const rovo = agents.find((a) => a.slug === "rovo")!;
    wrap(<RemoteDefinition spec={rovo.versions![0]!.spec} version={1} />);
    const card = screen.getByTestId("remote-definition");
    expect(within(card).getByText("Remote · A2A")).toBeInTheDocument();
    expect(screen.getByTestId("remote-endpoint")).toHaveTextContent("https://a2a.atlassian.com/v1/rovo");
    expect(screen.getByTestId("remote-digest")).toHaveTextContent(/sha256:[0-9a-f]{12}….*A2A 0\.3\.0/);
    expect(screen.getByTestId("remote-cost")).toHaveTextContent("0.05 USD per task");
    expect(card).toHaveTextContent("atlassian-rovo");
  });

  it("an agent that declared nothing says it is booked as unmeasured", () => {
    wrap(<RemoteDefinition spec={{ runtime: { kind: "a2a", card_url: "https://a.test/card" } }} version={3} />);
    expect(screen.getByTestId("remote-cost")).toHaveTextContent("booked as unmeasured");
  });

  it("the drift warning names the way out", () => {
    wrap(<CardChangedWarning slug="rovo" />);
    const warning = screen.getByTestId("card-changed");
    expect(warning).toHaveTextContent(/card changed/i);
    expect(within(warning).getByRole("link")).toHaveAttribute("href", "/agents/rovo?tab=definition");
  });

  it("the run shows the task, its states and how it ended", () => {
    const task = remoteTask([
      { seq: 2, type: "remote.started", ts: "2026-10-16T10:00:00Z", payload: { endpoint: "https://a2a.atlassian.com/v1/rovo", skill: "work-in-jira", card_digest: rovoDiscovery.card.digest, protocol_version: "0.3.0" } },
      { seq: 3, type: "remote.update", ts: "2026-10-16T10:00:01Z", payload: { state: "working", task_id: "task-7f3a" } },
      { seq: 4, type: "remote.update", ts: "2026-10-16T10:00:09Z", payload: { state: "completed", task_id: "task-7f3a" } },
      { seq: 5, type: "remote.finished", ts: "2026-10-16T10:00:09Z", payload: { state: "completed", task_id: "task-7f3a", duration_s: 74, reason: null } },
    ])!;
    wrap(<RemoteTaskCard task={task} />);
    expect(screen.getByTestId("remote-task-id")).toHaveTextContent("task-7f3a");
    expect(within(screen.getByTestId("remote-states")).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByTestId("remote-final")).toHaveTextContent(/completed after/);
    expect(screen.queryByTestId("remote-reason")).toBeNull();
  });

  it("a card that drifted shows no task and tells what to do", () => {
    const task = remoteTask([{ seq: 2, type: "remote.card_changed", ts: "2026-10-16T10:00:00Z", payload: { pinned: "sha256:aaaaaaaaaaaaaaaa", now: "sha256:bbbbbbbbbbbbbbbb", endpoint: "https://a.test/rpc" } }])!;
    wrap(<RemoteTaskCard task={task} />);
    expect(screen.getByTestId("remote-task-id")).toHaveTextContent("none: nothing was sent");
    expect(screen.getByTestId("remote-reason")).toHaveTextContent(/publish a new version/i);
    expect(screen.getByTestId("remote-drift")).toHaveTextContent(/Pinned.*now/);
  });
});
