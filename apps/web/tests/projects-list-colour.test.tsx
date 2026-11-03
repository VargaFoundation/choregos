import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ProjectsPage from "@/app/page";
import { hueOf } from "@/lib/workflow-hue";

/**
 * The projects list tells its projects apart (S55-10, ADR 0120): each wears its initial on the hue of
 * its slug, the same on every visit; the figures above carry their icons.
 */
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: null }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      projects: vi.fn().mockResolvedValue({
        items: [
          { id: "1", slug: "billing-api", name: "Billing API", status: "active", stats: {} },
          { id: "2", slug: "fon", name: "fondation", status: "active", stats: {} },
        ],
        meta: { has_more: false },
      }),
    },
  };
});

describe("the projects list", () => {
  it("gives each project its initial on the hue of its slug", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ProjectsPage />
      </QueryClientProvider>,
    );
    const billing = await screen.findByTestId("project-avatar-billing-api");
    expect(billing).toHaveTextContent("B");
    expect(billing.className).toContain(`bg-cat-${hueOf("billing-api")}-soft`);
    expect(screen.getByTestId("project-avatar-fon")).toHaveTextContent("F");
    expect(screen.getByRole("heading", { name: "Billing API" })).toBeInTheDocument();
  });
});
