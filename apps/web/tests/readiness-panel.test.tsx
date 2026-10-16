import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { askWho, groupReadiness, localHref, ReadinessPanel } from "@/components/project-settings/readiness-panel";
import type { ProjectReadiness, ReadinessItem } from "@/lib/types";

const session = vi.hoisted(() => ({ current: { org: "acme", me: { memberships: [] as unknown[] } } }));
vi.mock("@/lib/session", () => ({ useSession: () => session.current }));
const { api } = vi.hoisted(() => ({ api: { projectReadiness: vi.fn() } }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, ...api } };
});

const item = (id: string, section: ReadinessItem["section"], state: ReadinessItem["state"], by: ReadinessItem["fix"]["by"] = "project_owner", href?: string): ReadinessItem => ({
  id,
  section,
  title: `title of ${id}`,
  state,
  detail: `detail of ${id}`,
  fix: { by, href },
});
const readiness = (items: ReadinessItem[]): ProjectReadiness => ({
  ready: !items.some((i) => i.state === "todo"),
  summary: {
    done: items.filter((i) => i.state === "done").length,
    check: items.filter((i) => i.state === "check").length,
    todo: items.filter((i) => i.state === "todo").length,
  },
  items,
});

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ReadinessPanel slug="dev" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  session.current = { org: "acme", me: { memberships: [{ org: "acme", role: "project_owner", project_slug: "dev" }] } };
});

describe("the readiness panel (S40-06c)", () => {
  it("groups the items by section, in the settings order, and leaves out what is not needed", () => {
    const groups = groupReadiness([
      item("notifications", "notifications", "todo"),
      item("connector-cd", "connections", "check"),
      item("provisioned", "general", "done"),
      item("connector-ci", "connections", "not_needed"),
    ]);
    expect(groups.map((g) => [g.id, g.items.map((i) => i.id)])).toEqual([
      ["general", ["provisioned"]],
      ["connections", ["connector-cd"]],
      ["notifications", ["notifications"]],
    ]);
  });

  it("counts what is ready and what is to do, and links each open item to its anchor", async () => {
    api.projectReadiness.mockResolvedValue(
      readiness([
        item("provisioned", "general", "done"),
        item("connector-cd", "connections", "check", "project_owner", "/p/dev/settings#connections"),
        item("notifications", "notifications", "todo", "project_owner", "/p/dev/settings#notifications"),
        item("people-release-captain", "people", "todo", "project_owner", "/p/dev/members"),
      ]),
    );
    renderPanel();
    expect((await screen.findByTestId("readiness-summary")).textContent).toBe("1 of 4 ready, 1 to check");
    expect(screen.getByText("2 to do")).toBeTruthy();
    expect(screen.getByTestId("fix-notifications").getAttribute("href")).toBe("#notifications");
    expect(screen.getByTestId("fix-connector-cd").getAttribute("href")).toBe("#connections");
    expect(screen.getByTestId("fix-people-release-captain").getAttribute("href")).toBe("/p/dev/members");
    // A done item has no fix link, and the state is said in words for a screen reader.
    expect(within(screen.getByTestId("readiness-item-provisioned")).queryByText("Fix")).toBeNull();
    expect(screen.getByTestId("readiness-item-notifications").textContent).toContain("(to do)");
  });

  it("says Ready to run when nothing is to do", async () => {
    api.projectReadiness.mockResolvedValue(readiness([item("provisioned", "general", "done"), item("connector-cd", "connections", "check")]));
    renderPanel();
    expect(await screen.findByText("Ready to run")).toBeTruthy();
  });

  it("tells a viewer who cannot fix an item whom to ask, instead of a link", async () => {
    session.current = { org: "acme", me: { memberships: [{ org: "acme", role: "developer", project_slug: "dev" }] } };
    api.projectReadiness.mockResolvedValue(
      readiness([
        item("a", "general", "todo", "project_owner", "/p/dev/settings#general"),
        item("b", "connections", "todo", "org_admin", "/p/dev/settings#connections"),
        item("c", "connections", "todo", "platform_operator"),
        item("d", "people", "check", "external", "/p/dev/members"),
      ]),
    );
    renderPanel();
    await screen.findByTestId("readiness-item-a");
    expect(screen.getByTestId("readiness-item-a").textContent).toContain("Ask a project owner");
    expect(screen.getByTestId("readiness-item-b").textContent).toContain("Ask your organisation admin");
    expect(screen.getByTestId("readiness-item-c").textContent).toContain("Ask the platform operator");
    expect(screen.getByTestId("readiness-item-d").textContent).toContain("Done outside Choregos");
    expect(screen.queryByText("Fix")).toBeNull();
  });

  it("askWho: an organisation admin may fix what a project owner would", () => {
    expect(askWho("project_owner", { orgAdmin: true, projectOwner: false })).toBeNull();
    expect(askWho("org_admin", { orgAdmin: false, projectOwner: true })).toBe("Ask your organisation admin");
  });

  it("localHref keeps other pages and maps the agents anchor to its section", () => {
    expect(localHref("/p/dev/settings#agents", "dev")).toBe("#agents-models");
    expect(localHref("/p/dev/settings", "dev")).toBe("#general");
    expect(localHref("/p/dev/workflows", "dev")).toBe("/p/dev/workflows");
  });
});
