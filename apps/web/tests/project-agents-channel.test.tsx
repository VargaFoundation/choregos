import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Suspense } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProjectAgentsPage from "@/app/p/[slug]/agents/page";
import { ApiError, api } from "@/lib/api";
import type { Agent } from "@/lib/types";

/** A project chooses which channel its agents run, and pins by channel or by revision (S40-02c). */
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: { email: "lea@acme.dev", memberships: [] } }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      projectAgents: vi.fn().mockResolvedValue([]),
      agents: vi.fn(),
      implicitAgents: vi.fn().mockResolvedValue([]),
      workflowAgents: vi.fn().mockResolvedValue([]),
      projectAgentChannel: vi.fn(),
      setProjectAgentChannel: vi.fn(),
      pinProjectAgent: vi.fn().mockResolvedValue({}),
    },
  };
});

const reviewer = { slug: "reviewer", display_name: "Reviewer", kind: "internal", status: "active", latest_version: 3, scope: "org" } as Agent;

async function open() {
  const params = Promise.resolve({ slug: "dev" });
  await act(async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Suspense fallback="…">
          <ProjectAgentsPage params={params} />
        </Suspense>
      </QueryClientProvider>,
    );
  });
  return screen.findByTestId("project-channel");
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.agents).mockResolvedValue([reviewer]);
  vi.mocked(api.projectAgentChannel).mockResolvedValue({ channel: "stable" });
  vi.mocked(api.setProjectAgentChannel).mockResolvedValue({ channel: "candidate" });
});

describe("the project's channel", () => {
  it("switches the project to testing versions", async () => {
    const box = await open();
    const select = await within(box).findByLabelText("Versions this project runs");
    await waitFor(() => expect(select).toBeEnabled());
    expect(select).toHaveValue("stable");
    fireEvent.change(select, { target: { value: "candidate" } });
    await waitFor(() => expect(api.setProjectAgentChannel).toHaveBeenCalledWith("dev", "candidate"));
  });

  it("is held back with the API's sentence when the caller may not", async () => {
    vi.mocked(api.setProjectAgentChannel).mockRejectedValue(new ApiError(403, { detail: "needs the project_owner role on dev" }));
    const box = await open();
    const select = await within(box).findByLabelText("Versions this project runs");
    await waitFor(() => expect(select).toBeEnabled());
    fireEvent.change(select, { target: { value: "candidate" } });
    expect(await within(box).findByTestId("project-channel-refused")).toHaveTextContent("needs the project_owner role on dev");
    expect(select).toBeDisabled();
  });
});

describe("pinning", () => {
  async function pin(mode: string) {
    await open();
    const agent = await screen.findByLabelText("Agent");
    await waitFor(() => expect(within(agent).getByRole("option", { name: "Reviewer" })).toBeInTheDocument());
    fireEvent.change(agent, { target: { value: "reviewer" } });
    fireEvent.change(screen.getByLabelText("Follow"), { target: { value: mode } });
    fireEvent.click(screen.getByRole("button", { name: "Pin" }));
  }

  it("follows production by default", async () => {
    await pin("stable");
    await waitFor(() => expect(api.pinProjectAgent).toHaveBeenCalledWith("dev", "reviewer", { channel: "stable", overrides: {} }));
  });

  it("follows testing", async () => {
    await pin("candidate");
    await waitFor(() => expect(api.pinProjectAgent).toHaveBeenCalledWith("dev", "reviewer", { channel: "candidate", overrides: {} }));
  });

  it("holds a fixed revision", async () => {
    await open();
    const agent = await screen.findByLabelText("Agent");
    await waitFor(() => expect(within(agent).getByRole("option", { name: "Reviewer" })).toBeInTheDocument());
    fireEvent.change(agent, { target: { value: "reviewer" } });
    fireEvent.change(screen.getByLabelText("Follow"), { target: { value: "fixed" } });
    fireEvent.change(screen.getByLabelText("Revision"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Pin" }));
    await waitFor(() => expect(api.pinProjectAgent).toHaveBeenCalledWith("dev", "reviewer", { version: 2, overrides: {} }));
  });
});
