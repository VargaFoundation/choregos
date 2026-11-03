import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, within } from "@testing-library/react";
import { Suspense } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SettingsOverviewPage from "@/app/p/[slug]/settings/page";
import { groupOfAnchor, settingsHref, SETTINGS_GROUPS } from "@/components/project-settings/groups";
import { SettingsNav } from "@/components/project-settings/settings-nav";

/**
 * A project's settings in groups (S55-07): twelve sections on one endless page, all grey, become six
 * groups with an icon and a hue, each a page. Every old anchor still lands where its section lives.
 */
const replace = vi.fn();
const path = { current: "/p/dev/settings" };
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }), usePathname: () => path.current }));
vi.mock("@/components/project-settings/readiness-panel", () => ({ ReadinessPanel: () => null }));

beforeEach(() => {
  replace.mockReset();
  window.location.hash = "";
});

describe("the settings' groups", () => {
  it("hold every section of the page once", () => {
    const ids = SETTINGS_GROUPS.flatMap((group) => group.sections.map((section) => section.id));
    expect(ids.sort()).toEqual(
      [
        "a2a",
        "agents-models",
        "connections",
        "danger-zone",
        "general",
        "guardrails",
        "knowledge-memory",
        "network",
        "notifications",
        "people",
        "tools",
      ].sort(),
    );
  });

  it("send an anchor, an alias the API writes, or a group's name to its page", () => {
    expect(groupOfAnchor("tools")?.id).toBe("agents");
    expect(groupOfAnchor("agents")?.id).toBe("agents");
    expect(groupOfAnchor("policy")?.id).toBe("security");
    expect(groupOfAnchor("nowhere")).toBeUndefined();
    expect(settingsHref("dev", "tools")).toBe("/p/dev/settings/agents#tools");
    expect(settingsHref("dev", "agents")).toBe("/p/dev/settings/agents#agents-models");
    expect(settingsHref("dev", "network")).toBe("/p/dev/settings/security#network");
    expect(settingsHref("dev", "danger-zone")).toBe("/p/dev/settings/danger");
    expect(settingsHref("dev", "nowhere")).toBe("/p/dev/settings");
  });

  it("each wear an icon and a hue, the danger zone in red", () => {
    path.current = "/p/dev/settings/agents";
    render(<SettingsNav slug="dev" />);
    const nav = screen.getByRole("navigation", { name: "Settings groups" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual([
      "Overview",
      "General",
      "Connections",
      "Agents",
      "Guardrails",
      "People",
      "Danger zone",
    ]);
    expect(within(nav).getByRole("link", { name: "Agents" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Agents" }).querySelector("span")!.className).toContain("bg-cat-8-soft");
    expect(within(nav).getByRole("link", { name: "Danger zone" }).querySelector("span")!.className).toContain("bg-failed-soft");
  });
});

describe("the settings' overview", () => {
  async function overview() {
    await act(async () => {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <Suspense fallback={null}>
            <SettingsOverviewPage params={Promise.resolve({ slug: "dev" })} />
          </Suspense>
        </QueryClientProvider>,
      );
    });
  }

  it("sends an old link to a section's anchor to the group that holds it", async () => {
    window.location.hash = "#notifications";
    await overview();
    expect(replace).toHaveBeenCalledWith("/p/dev/settings/connections#notifications");
  });

  it("stays put without an anchor", async () => {
    await overview();
    expect(await screen.findByTestId("settings-group-security")).toHaveTextContent("Guardrails");
    expect(replace).not.toHaveBeenCalled();
  });
});
