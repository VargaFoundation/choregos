import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ForksPanel, SocialBar, WatchedAgentsCard } from "@/components/agents/social";
import { api } from "@/lib/api";
import type { Agent, AgentSocial } from "@/lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/agents/developer" }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ me: null, org: "acme" }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    api: {
      ...reel.api,
      watchAgent: vi.fn(),
      unwatchAgent: vi.fn(),
      starAgent: vi.fn(),
      unstarAgent: vi.fn(),
      agentForks: vi.fn(),
      agentNotifications: vi.fn(),
      readAgentNotification: vi.fn().mockResolvedValue({ updated: 1 }),
      readAllAgentNotifications: vi.fn().mockResolvedValue({ updated: 2 }),
    },
  };
});

const social = (over: Partial<AgentSocial> = {}): AgentSocial => ({
  watchers: 3,
  stars: 5,
  forks: 2,
  watching: null,
  starred_by_me: false,
  ...over,
});
const agent = (over: Partial<AgentSocial> = {}) =>
  ({ slug: "developer", display_name: "Developer", kind: "internal", status: "active", latest_version: 2, social: social(over) }) as Agent;
const wrap = (children: React.ReactNode) => <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;

beforeEach(() => vi.clearAllMocks());

describe("SocialBar", () => {
  it("shows the three counters and the forks link", () => {
    render(wrap(<SocialBar org="acme" agent={agent()} forksHref="/agents/developer?tab=forks" />));
    expect(screen.getByTestId("watchers")).toHaveTextContent("3");
    expect(screen.getByTestId("stars")).toHaveTextContent("5");
    expect(screen.getByRole("link", { name: /Forks 2/ })).toHaveAttribute("href", "/agents/developer?tab=forks");
  });

  it("stars, then unstars once starred", async () => {
    vi.mocked(api.starAgent).mockResolvedValue(social({ stars: 6, starred_by_me: true }));
    render(wrap(<SocialBar org="acme" agent={agent()} forksHref="#" />));
    fireEvent.click(screen.getByTestId("star"));
    await waitFor(() => expect(api.starAgent).toHaveBeenCalledWith("acme", "developer"));
    vi.mocked(api.unstarAgent).mockResolvedValue(social());
    render(wrap(<SocialBar org="acme" agent={agent({ starred_by_me: true })} forksHref="#" />));
    fireEvent.click(screen.getAllByTestId("star")[1]!);
    await waitFor(() => expect(api.unstarAgent).toHaveBeenCalledWith("acme", "developer"));
  });

  it("watches at a level from the menu and offers to unwatch only when watching", async () => {
    vi.mocked(api.watchAgent).mockResolvedValue(social({ watching: "releases" }));
    render(wrap(<SocialBar org="acme" agent={agent()} forksHref="#" />));
    fireEvent.click(screen.getByTestId("watch"));
    expect(screen.queryByRole("menuitem", { name: "Unwatch" })).toBeNull();
    fireEvent.click(screen.getByRole("menuitemradio", { name: /Releases only/ }));
    await waitFor(() => expect(api.watchAgent).toHaveBeenCalledWith("acme", "developer", "releases"));
  });

  it("unwatches", async () => {
    vi.mocked(api.unwatchAgent).mockResolvedValue(social());
    render(wrap(<SocialBar org="acme" agent={agent({ watching: "all" })} forksHref="#" />));
    expect(screen.getByTestId("watch")).toHaveTextContent("Watching");
    fireEvent.click(screen.getByTestId("watch"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Unwatch" }));
    await waitFor(() => expect(api.unwatchAgent).toHaveBeenCalledWith("acme", "developer"));
  });

  it("says why a gesture was refused", async () => {
    vi.mocked(api.starAgent).mockRejectedValue(new Error("refused"));
    render(wrap(<SocialBar org="acme" agent={agent()} forksHref="#" />));
    fireEvent.click(screen.getByTestId("star"));
    expect(await screen.findByRole("alert")).toHaveTextContent("refused");
  });
});

describe("ForksPanel", () => {
  it("lists the forks with how far they moved", async () => {
    vi.mocked(api.agentForks).mockResolvedValue({
      items: [
        { slug: "my-dev", display_name: "My dev", scope: "personal", owner: "lea@acme.dev", forked_from_version: 1, latest_version: 3, diverged: true, lifecycle: "active", created_at: null },
      ],
    });
    render(wrap(<ForksPanel org="acme" agent={agent()} />));
    const row = await screen.findByTestId("fork-my-dev");
    expect(within(row).getByText("diverged")).toBeInTheDocument();
    expect(within(row).getByText("v3")).toBeInTheDocument();
  });

  it("says when nobody forked it", async () => {
    vi.mocked(api.agentForks).mockResolvedValue({ items: [] });
    render(wrap(<ForksPanel org="acme" agent={agent()} />));
    expect(await screen.findByText("No fork yet")).toBeInTheDocument();
  });
});

describe("WatchedAgentsCard", () => {
  it("lists the items, marks one read, then all", async () => {
    vi.mocked(api.agentNotifications).mockResolvedValue({
      unread: 2,
      items: [
        { id: "n1", kind: "production_moved", agent: "developer", version: 2, actor: "a@acme.dev", created_at: new Date().toISOString(), read_at: null },
        { id: "n2", kind: "forked", agent: "developer", fork: "my-dev", created_at: new Date().toISOString(), read_at: null },
      ],
    });
    render(wrap(<WatchedAgentsCard org="acme" />));
    expect(await screen.findByText(/moved production v2/)).toBeInTheDocument();
    expect(screen.getByText(/was forked as my-dev/)).toBeInTheDocument();
    fireEvent.click(within(screen.getByTestId("agent-news-n1")).getByRole("button", { name: "Mark read" }));
    await waitFor(() => expect(api.readAgentNotification).toHaveBeenCalledWith("acme", "n1"));
    fireEvent.click(screen.getByRole("button", { name: "Mark all read" }));
    await waitFor(() => expect(api.readAllAgentNotifications).toHaveBeenCalledWith("acme"));
  });
});
