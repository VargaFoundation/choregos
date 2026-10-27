import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BarreLaterale } from "@/components/coquille/barre-laterale";
import { SECTION_ICONS, sectionIcon } from "@/components/coquille/navigation";
import schema from "../../../packages/contracts/schemas/ui-manifest.schema.json";

/**
 * S42-15: a plugin's administration section names its icon from the manifest's closed vocabulary.
 * Before, every plugin section was drawn with the same settings icon, so six enterprise entries
 * (Organisations, Platform administrators, Limits, Identity, Instance sign-in, Appearance) looked alike.
 */
vi.mock("next/navigation", () => ({ usePathname: () => "/admin" }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ me: null, orgs: [], org: "acme", choisirOrg: vi.fn(), deconnecter: vi.fn() }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      adminSections: vi.fn().mockResolvedValue([
        { id: "ee-organisations", title: "Organisations", icon: "building" },
        { id: "ee-administrateurs", title: "Platform administrators", icon: "shield" },
        { id: "ee-limites", title: "Limits", icon: "gauge" },
        { id: "ee-identite", title: "Identity", icon: "fingerprint" },
        { id: "ee-identite-instance", title: "Instance sign-in", icon: "log-in" },
        { id: "ee-apparence", title: "Appearance", icon: "palette" },
        { id: "older-plugin", title: "Older plugin" },
      ]),
    },
  };
});

const iconOf = (link: HTMLElement) => link.querySelector("svg")?.getAttribute("class") ?? "";

describe("plugin section icons", () => {
  it("draws each declared icon, and the settings icon when none is declared", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <BarreLaterale />
      </QueryClientProvider>,
    );
    const bar = screen.getByRole("complementary", { name: "Sidebar" });
    const titles = ["Organisations", "Platform administrators", "Limits", "Identity", "Instance sign-in", "Appearance"];
    const icons = [];
    for (const title of titles) icons.push(iconOf(await within(bar).findByRole("link", { name: title })));
    expect(new Set(icons).size).toBe(titles.length);
    expect(icons[0]).toContain("lucide-building");
    expect(iconOf(within(bar).getByRole("link", { name: "Older plugin" }))).toContain("lucide-settings");
  });

  it("knows every name of the contract's vocabulary, and falls back on an unknown one", () => {
    expect(Object.keys(SECTION_ICONS).sort()).toEqual([...schema.properties.icon.enum].sort());
    expect(sectionIcon("rocket")).toBe(SECTION_ICONS.settings);
    expect(sectionIcon(undefined)).toBe(SECTION_ICONS.settings);
  });
});
