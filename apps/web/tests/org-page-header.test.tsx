// SPDX-License-Identifier: Apache-2.0
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { adminGroups, GROUP_HUES, orgSection } from "@/components/shell/navigation";
import { OrgPageHeader } from "@/components/org-page-header";
import { HUE_CLASSES } from "@/lib/workflow-hue";

/**
 * An organisation's page wears the icon and the hue of its entry in the sidebar (S55-11, ADR 0120):
 * Agents, Skills, Spend or the catalogues are told apart before their titles are read, the way a
 * project's sections are.
 */
const path = { current: "/agents" };
vi.mock("next/navigation", () => ({ usePathname: () => path.current }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ me: null, orgs: [], org: "acme" }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, adminSections: vi.fn().mockResolvedValue([]) } };
});

function renderAt(pathname: string, props: Partial<Parameters<typeof OrgPageHeader>[0]> = {}) {
  path.current = pathname;
  return render(<OrgPageHeader title={props.title ?? orgSection(pathname)?.entry.label} {...props} />);
}

describe("the header of an organisation's page (S55-11)", () => {
  it.each([
    ["/", "Projects", GROUP_HUES.work],
    ["/agents", "Agents", GROUP_HUES.define],
    ["/skills/catalogue", "Skills", GROUP_HUES.define],
    ["/knowledge", "Knowledge", GROUP_HUES.define],
    ["/memories", "Memories", GROUP_HUES.define],
    ["/catalogue/mcp-servers", "MCP servers", GROUP_HUES.define],
    ["/catalogue/connectors", "Connector types", GROUP_HUES.define],
    ["/catalogue/plugins", "Plugins", GROUP_HUES.define],
    ["/integrations", "Integrations", GROUP_HUES.configure],
    ["/costs", "Spend", GROUP_HUES.configure],
  ])("%s is titled %s, on the hue of its family", (pathname, label, hue) => {
    renderAt(pathname);
    expect(screen.getByRole("heading", { level: 1, name: label })).toBeInTheDocument();
    const tile = screen.getByTestId("page-header-icon");
    expect(tile).toHaveClass(HUE_CLASSES[hue].soft, HUE_CLASSES[hue].ink);
  });

  it("keeps a page's own title, eyebrow, description and actions", () => {
    renderAt("/skills", {
      title: "Skill library",
      eyebrow: "What agents know how to do",
      description: "A skill is a folder.",
      actions: <button type="button">Upload</button>,
    });
    expect(screen.getByRole("heading", { level: 1, name: "Skill library" })).toBeInTheDocument();
    expect(screen.getByText("What agents know how to do")).toBeInTheDocument();
    expect(screen.getByText("A skill is a folder.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload" })).toBeInTheDocument();
  });

  it("a project's page is not an organisation's: it has its own header", () => {
    expect(orgSection("/p/fon/board")).toBeUndefined();
    expect(orgSection("/projects/new")).toBeUndefined();
    renderAt("/projects/new", { title: "New project" });
    expect(screen.getByRole("heading", { level: 1, name: "New project" })).toBeInTheDocument();
    expect(screen.queryByTestId("page-header-icon")).toBeNull();
  });

  it.each([
    ["/admin", "Administration"],
    ["/admin/members", "Members"],
    ["/admin/audit", "Audit"],
  ])("the administration's %s is titled %s, on the hue of what one configures", async (pathname, label) => {
    path.current = pathname;
    const { default: AdminLayout } = await import("@/app/admin/layout");
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AdminLayout>
          <p>Content</p>
        </AdminLayout>
      </QueryClientProvider>,
    );
    expect(screen.getByRole("heading", { level: 1, name: label })).toBeInTheDocument();
    const configure = HUE_CLASSES[GROUP_HUES.configure];
    expect(screen.getByTestId("page-header-icon")).toHaveClass(configure.soft, configure.ink);
  });

  it("the administration's sidebar wears the same hue, its plugins' sections included", () => {
    const [group] = adminGroups([{ id: "scim", title: "SCIM provisioning" }]);
    expect(group!.hue).toBe(GROUP_HUES.configure);
    expect(group!.entries.map((e) => e.label)).toContain("SCIM provisioning");
  });

  it.each([
    ["/agents/onboarding-coordinator", "Onboarding coordinator"],
    ["/agents/new", "New agent"],
    ["/agents/new/remote", "Remote agent (A2A)"],
    ["/skills/sizing", "sizing"],
    ["/memories/security-lessons", "Security lessons"],
  ])("a detail page %s wears its section's icon and hue under its own title (S55-12)", (pathname, title) => {
    renderAt(pathname, { title });
    expect(screen.getByRole("heading", { level: 1, name: title })).toBeInTheDocument();
    const define = HUE_CLASSES[GROUP_HUES.define];
    expect(screen.getByTestId("page-header-icon")).toHaveClass(define.soft, define.ink);
  });
});

