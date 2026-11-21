import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SkillsPage from "@/app/skills/page";
import { api } from "@/lib/api";

/** The installed skills are read one page at a time and "More" asks for the next (ADR 0206). */
vi.mock("next/navigation", () => ({
  usePathname: () => "/skills",
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: { email: "ada@acme.dev" } }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, skillsPage: vi.fn() } };
});

const skill = (slug: string) => ({ slug, description: `About ${slug}`, latest_version: 1, used_by: [], risk: "low", review_status: "approved" });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.skillsPage).mockImplementation(async (_org, params) =>
    params?.cursor === "alpha"
      ? ({ items: [skill("beta")], meta: { has_more: false, next_cursor: null } } as never)
      : ({ items: [skill("alpha")], meta: { has_more: true, next_cursor: "alpha" } } as never),
  );
});

describe("the installed skills", () => {
  it("shows the first page, and More reads the next by its cursor", async () => {
    await act(async () => {
      render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <SkillsPage />
        </QueryClientProvider>,
      );
    });
    const table = await screen.findByTestId("installed-skills");
    expect(within(table).getByRole("link", { name: "alpha" })).toBeInTheDocument();
    expect(within(table).queryByRole("link", { name: "beta" })).toBeNull();
    expect(api.skillsPage).toHaveBeenCalledWith("acme", { cursor: undefined, limit: 50 });
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    await waitFor(() => expect(within(table).getByRole("link", { name: "beta" })).toBeInTheDocument());
    expect(within(table).getByRole("link", { name: "alpha" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "More" })).toBeNull();
  });
});
