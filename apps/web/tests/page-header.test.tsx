import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { Suspense } from "react";
import { act } from "react";
import { describe, expect, it, vi } from "vitest";
import {
  GROUP_HUES,
  projectSection,
  projectTrail,
} from "@/components/shell/navigation";
import { PageHeader } from "@/components/page-header";
import { WorkflowChip } from "@/components/workflow-chip";
import { workflowHue } from "@/lib/workflow-hue";
import { SquareKanban } from "lucide-react";

/**
 * Every project page says where one stands (S55-02, ADR 0120): the section's name in its family's
 * colour under the project's name — not the project's name over every page —, a fixed page named by
 * the console rather than shown as a raw path segment, and a workflow in its own colour.
 */
const path = { current: "/p/fon/board" };
vi.mock("next/navigation", () => ({ usePathname: () => path.current }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      project: vi.fn().mockResolvedValue({ slug: "fon", name: "FON" }),
    },
  };
});

async function renderLayout(pathname: string) {
  path.current = pathname;
  const { default: ProjectLayout } = await import("@/app/p/[slug]/layout");
  await act(async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Suspense fallback={null}>
          <ProjectLayout params={Promise.resolve({ slug: "fon" })}>
            <p>content</p>
          </ProjectLayout>
        </Suspense>
      </QueryClientProvider>,
    );
  });
}

describe("a project page's header", () => {
  it("names the section in its family's colour, under the project's name", async () => {
    await renderLayout("/p/fon/board");
    const title = await screen.findByRole("heading", {
      level: 1,
      name: "Board",
    });
    const header = title.closest("header")!;
    expect(await within(header).findByText("FON")).toBeInTheDocument();
    expect(screen.getByTestId("page-header-icon").className).toContain(
      `bg-cat-${GROUP_HUES.work}-soft`,
    );
  });

  it("is the project itself on its overview", async () => {
    await renderLayout("/p/fon");
    expect(
      await screen.findByRole("heading", { level: 1, name: "FON" }),
    ).toBeInTheDocument();
  });

  it("leaves a ticket's page to its own trail and title (S55-05)", async () => {
    await renderLayout("/p/fon/items/w1");
    expect(await screen.findByText("content")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    expect(screen.queryByTestId("page-header-icon")).toBeNull();
  });

  it("wears the hue of the family a section belongs to", () => {
    expect(projectSection("fon", "/p/fon/items/w1")?.entry.label).toBe("Board");
    expect(projectSection("fon", "/p/fon/workflows/fon-access/map")?.hue).toBe(
      GROUP_HUES.define,
    );
    expect(projectSection("fon", "/p/fon/settings")?.hue).toBe(
      GROUP_HUES.configure,
    );
  });
});

describe("the trail below a section", () => {
  it("names a fixed page instead of showing its path segment", () => {
    expect(projectTrail("fon", "/p/fon/workflows/library")?.[1]).toEqual({
      label: "Library",
    });
    expect(projectTrail("fon", "/p/fon/workflows/new")?.[1]).toEqual({
      label: "New workflow",
    });
  });
});

describe("PageHeader", () => {
  it("draws its icon on a neutral tint when it has no hue", () => {
    render(<PageHeader icon={SquareKanban} title="Board" />);
    expect(screen.getByTestId("page-header-icon").className).toContain(
      "bg-surface-muted",
    );
  });
});

describe("WorkflowChip", () => {
  it("names the workflow and its version in the workflow's own hue", () => {
    render(
      <WorkflowChip
        name="fon-access"
        version={3}
        href="/p/fon/workflows/fon-access"
      />,
    );
    const link = screen.getByRole("link", { name: /fon-access/ });
    expect(link).toHaveAttribute("href", "/p/fon/workflows/fon-access");
    expect(link).toHaveTextContent("v3");
    expect(link.className).toContain(
      `bg-cat-${workflowHue("fon-access")}-soft`,
    );
  });
});
