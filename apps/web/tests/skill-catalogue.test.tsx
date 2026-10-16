import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import SkillCataloguePage from "@/app/skills/catalogue/page";
import { NO_FILTERS, withToggle } from "@/lib/catalogue";
import { confirmationLines, filterSkillEntries, installAction } from "@/lib/skill-catalogue";
import type { SkillCatalogue, SkillCatalogueEntry } from "@/lib/types";
import file from "../src/mocks/skill-catalogue.json";

const { push, install } = vi.hoisted(() => ({ push: vi.fn(), install: vi.fn() }));

vi.mock("next/navigation", () => ({ usePathname: () => "/skills/catalogue", useRouter: () => ({ push }) }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      skillCatalogue: vi.fn(async () => structuredClone(file) as unknown as SkillCatalogue),
      installSkillCatalogueEntry: install,
    },
  };
});

const catalogue = file as unknown as SkillCatalogue;
const byId = (id: string) => catalogue.entries.find((e) => e.id === id)!;

function page() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <SkillCataloguePage />
    </QueryClientProvider>,
  );
}

describe("the skills catalogue's pure parts", () => {
  it("filters by text, category, bundled, permissive and risk", () => {
    const all = catalogue.entries;
    expect(all.length).toBeGreaterThanOrEqual(70);
    expect(filterSkillEntries(all, { ...NO_FILTERS, q: "contract review" }).map((e) => e.id)).toContain("review-contract");
    const legal = filterSkillEntries(all, { ...NO_FILTERS, category: "legal" });
    expect(legal.length).toBeGreaterThan(1);
    expect(legal.every((e) => e.categories.includes("legal"))).toBe(true);
    const bundled = filterSkillEntries(all, withToggle(NO_FILTERS, "bundled", true));
    expect(bundled.every((e) => e.delivery === "bundled")).toBe(true);
    expect(bundled.length).toBeLessThan(all.length);
    const permissive = filterSkillEntries(all, withToggle(NO_FILTERS, "permissive", true));
    expect(permissive.length).toBeLessThan(all.length);
    expect(permissive.every((e) => e.licence_class === "permissive")).toBe(true);
    const safe = filterSkillEntries(all, withToggle(NO_FILTERS, "lowrisk", true));
    expect(safe.every((e) => e.risk !== "high")).toBe(true);
  });

  it("the button says what it does, and is off when nothing can be done", () => {
    const entry = (state: SkillCatalogueEntry["state"]) => ({ ...byId("write-spec"), state });
    expect(installAction(entry("available"))).toEqual({ label: "Install", disabled: false });
    expect(installAction(entry("update"))).toEqual({ label: "Update", disabled: false });
    expect(installAction(entry("installed")).disabled).toBe(true);
    expect(installAction(entry("pending_review")).disabled).toBe(true);
    expect(installAction(entry("blocked")).disabled).toBe(true);
  });

  it("the confirmation says a high-risk skill needs a second administrator, and that git means a fetch", () => {
    const high = byId("systematic-debugging");
    expect(high.risk).toBe("high");
    const lines = confirmationLines(high);
    expect(lines.join(" ")).toMatch(/second administrator will have to approve/);
    expect(lines.join(" ")).toMatch(/Fetched from GitHub at install/);
    const bundled = confirmationLines(byId("write-spec")).join(" ");
    expect(bundled).not.toMatch(/second administrator/);
    expect(bundled).toMatch(/Licence Apache-2.0 \(permissive\)/);
    const copyleft = catalogue.entries.find((e) => e.licence_class === "copyleft")!;
    expect(confirmationLines(copyleft).join(" ")).toMatch(/copyleft licence/);
  });
});

describe("the catalogue page", () => {
  it("each card shows its risk, licence, delivery and stars, and the facets narrow the list", async () => {
    page();
    const spec = await screen.findByTestId("entry-write-spec");
    expect(within(spec).getByText("Low risk")).toBeInTheDocument();
    expect(within(spec).getByText(/Permissive · Apache-2.0/)).toBeInTheDocument();
    expect(within(spec).getByText("Bundled")).toBeInTheDocument();
    expect(spec).toHaveTextContent("stars as of 2026-10-15");
    const debugging = screen.getByTestId("entry-systematic-debugging");
    expect(within(debugging).getByText("High risk")).toBeInTheDocument();
    expect(within(debugging).getByText("From git")).toBeInTheDocument();
    expect(screen.getByText(`${catalogue.entries.length} skills`)).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Hide high risk"));
    expect(screen.queryByTestId("entry-systematic-debugging")).toBeNull();
    fireEvent.click(screen.getByLabelText("Hide high risk"));
    fireEvent.change(screen.getByLabelText("Search the catalogue"), { target: { value: "no-such-skill-anywhere" } });
    expect(screen.getByText("Nothing matches")).toBeInTheDocument();
  });

  it("the drawer lists what is taken on, installs by id, and lands on the skill", async () => {
    install.mockResolvedValue({ slug: "write-spec", latest_version: 1 });
    page();
    fireEvent.click(within(await screen.findByTestId("entry-write-spec")).getByRole("button", { name: "Install" }));
    const drawer = screen.getByRole("dialog");
    expect(drawer).toHaveTextContent("Install Write spec");
    expect(within(drawer).getByTestId("confirmation")).toHaveTextContent("Licence Apache-2.0 (permissive)");
    fireEvent.click(within(drawer).getByRole("button", { name: "Install" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/skills/write-spec"));
    expect(install).toHaveBeenCalledWith("acme", "write-spec");
  });

  it("a high-risk skill's drawer says it needs a second administrator", async () => {
    page();
    fireEvent.click(within(await screen.findByTestId("entry-systematic-debugging")).getByRole("button", { name: "Install" }));
    expect(within(screen.getByRole("dialog")).getByTestId("confirmation")).toHaveTextContent(
      "a second administrator will have to approve it",
    );
  });

  it("a refusal stays in the drawer", async () => {
    install.mockReset();
    install.mockRejectedValue(new Error("the organisation already has a skill named `runbook`: it is left untouched"));
    page();
    fireEvent.click(within(await screen.findByTestId("entry-runbook")).getByRole("button", { name: "Install" }));
    const drawer = screen.getByRole("dialog");
    fireEvent.click(within(drawer).getByRole("button", { name: "Install" }));
    expect(await within(drawer).findByText(/left untouched/)).toBeInTheDocument();
    expect(push).not.toHaveBeenCalledWith("/skills/runbook");
  });
});
