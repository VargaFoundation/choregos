import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Suspense } from "react";
import { describe, expect, it, vi } from "vitest";
import WorkflowCataloguePage from "@/app/p/[slug]/workflows/catalogue/page";
import { filterEntries, readinessPreview, sourceLabel } from "@/components/workflows/catalogue";
import { api } from "@/lib/api";
import type { ConnectorDto, WorkflowCatalogueEntry } from "@/lib/types";

const { workflowCatalogue } = vi.hoisted(() => {
  const need = (capability: string) => ({ capability, reasons: [`needs ${capability}`] });
  const base = { version: 1, tags: [] as string[], groups: [] as string[], agents: [] as WorkflowCatalogueEntry["agents"], yaml: "" };
  const entries: WorkflowCatalogueEntry[] = [
    { ...base, source: "core", name: "default-request", title: "default-request", summary: "", needs_repository: false, requires: [need("tracker"), need("runtime"), need("gateway")] },
    { ...base, source: "core", name: "default-simple", title: "default-simple", summary: "", needs_repository: true, requires: [need("tracker"), need("scm"), need("cd")] },
    { ...base, source: "template:joiners-leavers", name: "onboarding", title: "Onboarding", summary: "", needs_repository: false, tags: ["hr"], requires: [need("tracker")] },
    {
      ...base,
      source: "plugin:servicenow",
      name: "incident-intake",
      title: "Incident intake",
      summary: "",
      needs_repository: false,
      requires: [need("tracker")],
      agents: [{ actor: "triager", slug: "snow-triager", version: null, resolution: "unresolved", plugin: null, display_name: null }],
    },
  ];
  return { workflowCatalogue: entries };
});
const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
let repo: { url: string } | undefined;
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      project: vi.fn(async () => ({ id: "p", slug: "f4f", org: "acme", status: "active", config: { slug: "f4f", org: "acme", repo } })),
      workflowCatalogue: vi.fn(async () => workflowCatalogue),
      connectors: vi.fn(async () => []),
      adoptWorkflow: vi.fn(async () => ({})),
    },
  };
});

async function show() {
  document.body.innerHTML = "";
  await act(async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Suspense fallback="…">
          <WorkflowCataloguePage params={Promise.resolve({ slug: "f4f" })} />
        </Suspense>
      </QueryClientProvider>,
    );
  });
}

describe("the workflow catalogue (S49-08, ADR 0108)", () => {
  it("filters and previews from what the console knows", () => {
    expect(filterEntries(workflowCatalogue, "", "without").map((e) => e.name)).toEqual([
      "default-request",
      "onboarding",
      "incident-intake",
    ]);
    expect(filterEntries(workflowCatalogue, "hr", "any").map((e) => e.name)).toEqual(["onboarding"]);
    const simple = workflowCatalogue.find((e) => e.name === "default-simple")!;
    const tracker = { kind: "tracker", type: "jira" } as ConnectorDto;
    expect(readinessPreview(simple, [tracker]).map((l) => [l.capability, l.state])).toEqual([
      ["tracker", "configured"],
      ["scm", "missing"],
      ["cd", "missing"],
    ]);
    expect(sourceLabel("plugin:servicenow")).toBe("Plugin servicenow");
  });

  it("opens on what a project without a repository can run, and adopts as the default", async () => {
    repo = undefined;
    await show();
    const list = await screen.findByRole("list", { name: "Workflow catalogue" });
    await within(list).findByText("default-request");
    expect(within(list).queryByText("default-simple")).toBeNull();
    expect(within(list).queryByRole("button", { name: "Adopt incident-intake" })).toBeNull();
    expect(within(list).getByRole("list", { name: "Agents of incident-intake" })).toHaveTextContent(
      "snow-triager — not available: install its plugin first",
    );

    fireEvent.click(within(list).getByRole("button", { name: "Adopt default-request" }));
    expect(screen.getByRole("list", { name: "What default-request needs here" })).toHaveTextContent("runtime: provided");
    fireEvent.change(screen.getByLabelText("Name in this project"), { target: { value: "requests" } });
    fireEvent.click(screen.getByLabelText("Make it the project's default workflow"));
    fireEvent.click(screen.getByRole("button", { name: "Adopt as requests" }));
    await waitFor(() =>
      expect(api.adoptWorkflow).toHaveBeenCalledWith("f4f", {
        source: "core",
        name: "default-request",
        version: 1,
        as: "requests",
        make_default: true,
      }),
    );
    await waitFor(() => expect(push).toHaveBeenCalledWith("/p/f4f/workflows/requests/map?vue=list"));
  });

  it("with a repository, lists every workflow", async () => {
    repo = { url: "https://github.com/acme/f4f.git" };
    await show();
    const list = await screen.findByRole("list", { name: "Workflow catalogue" });
    await within(list).findByText("default-simple");
    expect(within(list).getByText("default-request")).toBeInTheDocument();
  });
});
