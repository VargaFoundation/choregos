import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SectionRendue } from "@/components/admin/section";
import { sectionAcmeLayered } from "@/mocks/data";
import type { AdminSection } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...original,
    api: { ...original.api, sectionCall: vi.fn(async () => ({ accent: "teal", product_name: "Acme" })) },
  };
});

function rendre(section: AdminSection) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SectionRendue section={section} org="acme" />
    </QueryClientProvider>,
  );
}

describe("a section whose blocks carry their own permission (S42-02)", () => {
  it("renders the title and the description of each block", async () => {
    rendre(sectionAcmeLayered as AdminSection);
    expect(await screen.findByText("This organisation")).toBeInTheDocument();
    expect(screen.getByText("Applies to Acme only.")).toBeInTheDocument();
    expect(screen.getByText("The whole instance")).toBeInTheDocument();
    expect(screen.getByText(/needs platform administration/)).toBeInTheDocument();
  });

  it("renders fine once the API has filtered the platform block out", async () => {
    const filtree = { ...sectionAcmeLayered, blocks: [sectionAcmeLayered.blocks[0]] } as AdminSection;
    rendre(filtree);
    expect(await screen.findByText("This organisation")).toBeInTheDocument();
    expect(screen.queryByText("The whole instance")).not.toBeInTheDocument();
    expect(within(screen.getByTestId("admin-section-acme-layered")).getAllByRole("button", { name: "Save" })).toHaveLength(1);
  });

  it("shows the description of an action block", () => {
    rendre({
      id: "x",
      title: "x",
      scope: "organisation",
      permission: "member:manage",
      blocks: [{ kind: "action", label: "Rotate", description: "Rotates the key.", path: "/orgs/{org}/k" }],
    } as AdminSection);
    expect(screen.getByText("Rotates the key.")).toBeInTheDocument();
  });
});
