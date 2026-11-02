import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Suspense } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WorkflowsPage from "@/app/p/[slug]/workflows/page";
import { api } from "@/lib/api";
import type { WorkflowRouting } from "@/lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), useSearchParams: () => new URLSearchParams() }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      workflows: vi.fn(),
      workflowRouting: vi.fn(),
      project: vi.fn().mockResolvedValue({ slug: "fon", org: "acme" }),
      putWorkflowRouting: vi.fn(async (_slug: string, routing: unknown) => routing),
    },
  };
});

const WORKFLOWS = [
  { name: "onboarding", version: 1, is_default: true, open_items: 0 },
  { name: "offboarding", version: 2, is_default: false, open_items: 0 },
];

async function open(routing: WorkflowRouting) {
  vi.mocked(api.workflows).mockResolvedValue(WORKFLOWS as never);
  vi.mocked(api.workflowRouting).mockResolvedValue(routing);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    render(
      <QueryClientProvider client={client}>
        <Suspense>
          <WorkflowsPage params={Promise.resolve({ slug: "fon" })} />
        </Suspense>
      </QueryClientProvider>,
    );
  });
  await screen.findByRole("form", { name: "New routing rule" });
}

describe("the routing editor edits a rule in place", () => {
  beforeEach(() => vi.mocked(api.putWorkflowRouting).mockClear());

  it("loads a rule into the form, updates it there, and saves the whole routing", async () => {
    await open({
      default: "onboarding",
      rules: [{ when: { labels_any: ["leaver"], labels_all: [], item_type: null }, workflow: "offboarding" }],
    });
    fireEvent.click(screen.getByRole("button", { name: "Edit rule 1" }));
    const form = screen.getByRole("form", { name: "Edit routing rule 1" });
    expect(within(form).getByLabelText("Labelled (any of)")).toHaveValue("leaver");
    fireEvent.change(within(form).getByLabelText("Labelled (any of)"), { target: { value: "leaver, departure" } });
    fireEvent.change(within(form).getByLabelText("Labelled (all of)"), { target: { value: "hr" } });
    fireEvent.change(within(form).getByLabelText("Of type"), { target: { value: "Task" } });
    // While a rule is being edited, the list does not move under it.
    expect(screen.getByRole("button", { name: "Remove rule 1" })).toBeDisabled();
    fireEvent.click(within(form).getByRole("button", { name: "Update rule 1" }));

    const rules = screen.getByRole("list", { name: "Routing rules" });
    expect(within(rules).getAllByRole("listitem")).toHaveLength(1);
    expect(rules).toHaveTextContent("labelled leaver or departure, labelled hr, of type Task → offboarding");
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save the routing" })));
    expect(api.putWorkflowRouting).toHaveBeenCalledWith("fon", {
      default: "onboarding",
      rules: [
        { when: { labels_any: ["leaver", "departure"], labels_all: ["hr"], item_type: "Task" }, workflow: "offboarding" },
      ],
    });
  });

  it("cancelling an edit leaves the rule as it was", async () => {
    await open({
      default: "onboarding",
      rules: [{ when: { labels_any: ["leaver"], labels_all: [], item_type: null }, workflow: "offboarding" }],
    });
    fireEvent.click(screen.getByRole("button", { name: "Edit rule 1" }));
    fireEvent.change(screen.getByLabelText("Labelled (any of)"), { target: { value: "other" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("form", { name: "New routing rule" })).toBeInTheDocument();
    expect(screen.getByLabelText("Labelled (any of)")).toHaveValue("");
    expect(screen.getByRole("list", { name: "Routing rules" })).toHaveTextContent("labelled leaver → offboarding");
    expect(screen.queryByText("The routing has unsaved changes")).not.toBeInTheDocument();
  });

  it("a rule on labels_all alone can be added", async () => {
    await open({ default: "onboarding", rules: [] });
    fireEvent.change(screen.getByLabelText("Labelled (all of)"), { target: { value: "hr, leaver" } });
    fireEvent.click(screen.getByRole("button", { name: "Add the rule" }));
    expect(screen.getByRole("list", { name: "Routing rules" })).toHaveTextContent(
      "labelled hr and leaver → offboarding",
    );
  });

  it("the default is chosen in the same draft, and a project without one must choose it", async () => {
    await open({ default: "", rules: [] });
    const select = screen.getByLabelText("Default workflow");
    expect(select).toHaveValue("");
    fireEvent.change(screen.getByLabelText("Of type"), { target: { value: "Bug" } });
    fireEvent.click(screen.getByRole("button", { name: "Add the rule" }));
    expect(screen.getByText("Choose a default workflow before saving")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save the routing" })).toBeDisabled();

    fireEvent.change(select, { target: { value: "offboarding" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save the routing" })));
    await waitFor(() =>
      expect(api.putWorkflowRouting).toHaveBeenCalledWith("fon", {
        default: "offboarding",
        rules: [{ when: { labels_any: [], labels_all: [], item_type: "Bug" }, workflow: "onboarding" }],
      }),
    );
  });
});
