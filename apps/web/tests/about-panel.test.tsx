import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TopNav } from "@/app/top-nav";
import { AboutPanel, aboutSupportText } from "@/components/edition";
import { BarreLaterale } from "@/components/coquille/barre-laterale";
import { api } from "@/lib/api";
import { ThemeProvider } from "@/lib/theme";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ me: null, orgs: [], org: "acme", choisirOrg: vi.fn(), deconnecter: vi.fn() }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    api: {
      ...reel.api,
      projects: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      workItems: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      orgActions: vi.fn().mockResolvedValue([]),
      edition: vi.fn(),
    },
  };
});

const COMMUNITY = {
  edition: "community" as const,
  features: [],
  version: "0.17.16",
  publisher: null,
  edition_version: null,
  licence: "Apache-2.0",
  edition_licence: null,
  build: { revision: "0123abc4567def890123abc4567def890123abc4" },
};
const ENTERPRISE = {
  ...COMMUNITY,
  edition: "enterprise" as const,
  features: ["saml"],
  publisher: "Acme Corp",
  edition_version: "0.2.0",
  edition_licence: "Acme Corp EULA",
};

function wrap(children: React.ReactNode) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      <ThemeProvider>{children}</ThemeProvider>
    </QueryClientProvider>
  );
}

describe("the About panel (S42-12)", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    delete document.documentElement.dataset.sidebar;
    vi.mocked(api.edition).mockReset();
  });

  it("opens from the sidebar and says what the community edition runs", async () => {
    vi.mocked(api.edition).mockResolvedValue(COMMUNITY);
    render(wrap(<BarreLaterale />));
    fireEvent.click(within(screen.getByRole("complementary", { name: "Sidebar" })).getByRole("button", { name: "About" }));
    const panel = await screen.findByRole("dialog", { name: "About" });
    expect(await within(panel).findByText("Community edition")).toBeInTheDocument();
    expect(within(panel).getByText("Choregos")).toBeInTheDocument();
    expect(within(panel).getByText("0.17.16")).toBeInTheDocument();
    expect(within(panel).getByTestId("about-core-licence")).toHaveTextContent("Apache-2.0");
    expect(within(panel).getByText("0123abc4567def890123abc4567def890123abc4")).toBeInTheDocument();
    // No edition to name: none of the enterprise rows.
    for (const row of ["Publisher", "Edition licence", "Edition version"]) expect(within(panel).queryByText(row)).toBeNull();
    expect(within(panel).getByRole("link", { name: "Source repository" })).toHaveAttribute(
      "href",
      "https://github.com/VargaFoundation/choregos",
    );
    expect(within(panel).getByRole("link", { name: "Documentation" })).toHaveAttribute("href", expect.stringContaining("/docs"));
  });

  it("stays reachable from the folded rail: an icon with its name and tooltip", () => {
    document.documentElement.dataset.sidebar = "rail";
    vi.mocked(api.edition).mockResolvedValue(COMMUNITY);
    render(wrap(<BarreLaterale />));
    const bouton = within(screen.getByRole("complementary", { name: "Sidebar" })).getByRole("button", { name: "About" });
    expect(bouton).toHaveAttribute("title", "About");
    expect(bouton.querySelector("svg")).not.toBeNull();
  });

  it("names the edition, its version, its publisher and its licence", async () => {
    vi.mocked(api.edition).mockResolvedValue(ENTERPRISE);
    render(wrap(<AboutPanel />));
    expect(await screen.findByText("Enterprise edition")).toBeInTheDocument();
    expect(screen.getByText("0.2.0")).toBeInTheDocument();
    expect(screen.getByText("Acme Corp")).toBeInTheDocument();
    expect(screen.getByText("Acme Corp EULA")).toBeInTheDocument();
    // The core keeps its own licence beside the edition's.
    expect(screen.getByTestId("about-core-licence")).toHaveTextContent("Apache-2.0");
  });

  it("says so when the build revision is not recorded, and when an older API sends no new field", async () => {
    vi.mocked(api.edition).mockResolvedValue({
      edition: "community",
      features: [],
      version: "0.17.16",
      publisher: null,
      edition_version: null,
    });
    render(wrap(<AboutPanel />));
    expect(await screen.findByText("not recorded")).toBeInTheDocument();
    expect(screen.getByTestId("about-core-licence")).toHaveTextContent("Apache-2.0");
  });

  it("shows the served product name", async () => {
    vi.mocked(api.edition).mockResolvedValue(COMMUNITY);
    render(wrap(<AboutPanel />));
    expect(await screen.findByText("Product")).toBeInTheDocument();
    expect(screen.getByText("Product").nextSibling).toHaveTextContent("Choregos");
  });

  it("copies a plain-text summary for support", async () => {
    vi.mocked(api.edition).mockResolvedValue(ENTERPRISE);
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: write }, configurable: true });
    render(wrap(<AboutPanel />));
    fireEvent.click(await screen.findByRole("button", { name: "Copy for support" }));
    await waitFor(() => expect(write).toHaveBeenCalled());
    expect(write).toHaveBeenCalledWith(
      [
        "Product: Choregos",
        "Core version: 0.17.16",
        "Edition: enterprise",
        "Edition version: 0.2.0",
        "Revision: 0123abc4567def890123abc4567def890123abc4",
      ].join("\n"),
    );
    expect(await screen.findByText("Copied.")).toBeInTheDocument();
  });

  it("says when the clipboard is refused", async () => {
    vi.mocked(api.edition).mockResolvedValue(COMMUNITY);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
      configurable: true,
    });
    render(wrap(<AboutPanel />));
    fireEvent.click(await screen.findByRole("button", { name: "Copy for support" }));
    expect(await screen.findByText(/Could not copy/)).toBeInTheDocument();
  });

  it("the support text of the community edition has no edition version and an unknown revision", () => {
    expect(aboutSupportText("Choregos", { ...COMMUNITY, build: { revision: null } })).toBe(
      "Product: Choregos\nCore version: 0.17.16\nEdition: community\nRevision: unknown",
    );
  });

  it("is also in the mobile menu", async () => {
    vi.mocked(api.edition).mockResolvedValue(COMMUNITY);
    render(wrap(<TopNav />));
    fireEvent.click(screen.getByRole("button", { name: /^Menu/ }));
    fireEvent.click(screen.getByRole("button", { name: "About" }));
    const panel = await screen.findByRole("dialog", { name: "About" });
    expect(await within(panel).findByText("Community edition")).toBeInTheDocument();
  });
});
