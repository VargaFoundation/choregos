import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Suspense } from "react";
import { describe, expect, it, vi } from "vitest";
import NewWorkflowPage from "@/app/p/[slug]/workflows/new/page";
import { ApiError, api } from "@/lib/api";

const pousser = vi.fn();
let search = new URLSearchParams();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pousser }), useSearchParams: () => search }));
vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    api: {
      ...reel.api,
      workflowTemplates: vi.fn().mockResolvedValue([
        { name: "default-simple", version: 1, display: "Simple", yaml: "metadata: { name: default-simple, version: 1 }\n" },
        { name: "default-request", version: 1, display: "Request", yaml: "metadata: { name: default-request, version: 1 }\n" },
      ]),
      workflowRouting: vi.fn().mockResolvedValue({ default: "default-simple", rules: [{ workflow: "offboarding", labels_any: ["leaver"] }] }),
      putWorkflowRouting: vi.fn(async (_slug: string, routing: unknown) => routing),
      workflows: vi.fn().mockResolvedValueOnce([]).mockResolvedValue([{ name: "offboarding", version: 1, is_default: false, open_items: 0 }]),
      createWorkflow: vi.fn(),
      putWorkflowNamed: vi.fn(),
    },
  };
});

describe("créer un workflow (#336)", () => {
  it("crée sans jamais republier, et un nom pris entre-temps se dit sous le champ", async () => {
    vi.mocked(api.createWorkflow).mockRejectedValue(new ApiError(409, { detail: "`offboarding` already exists" }));
    await act(async () => {
      render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <Suspense fallback="…">
            <NewWorkflowPage params={Promise.resolve({ slug: "rh" })} />
          </Suspense>
        </QueryClientProvider>,
      );
    });
    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "offboarding" } });
    const publier = await screen.findByRole("button", { name: "Publish offboarding" });
    await waitFor(() => expect(publier).toBeEnabled());
    fireEvent.click(publier);
    await waitFor(() => expect(api.createWorkflow).toHaveBeenCalledWith("rh", "offboarding", expect.any(String)));
    expect(api.putWorkflowNamed).not.toHaveBeenCalled();
    expect(await screen.findByText(/A workflow named offboarding already exists/)).toBeInTheDocument();
    expect(pousser).not.toHaveBeenCalled();
  });

  it("from the readiness fix, publishes default-request and makes it the default, keeping the rules", async () => {
    search = new URLSearchParams("template=default-request&default=1");
    vi.mocked(api.createWorkflow).mockResolvedValue({} as never);
    await act(async () => {
      render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <Suspense fallback="…">
            <NewWorkflowPage params={Promise.resolve({ slug: "f4f" })} />
          </Suspense>
        </QueryClientProvider>,
      );
    });
    expect(await screen.findByLabelText("Name")).toHaveValue("default-request");
    expect(screen.getByLabelText("Make it the project's default workflow")).toBeChecked();
    const publish = await screen.findByRole("button", { name: "Publish default-request" });
    await waitFor(() => expect(publish).toBeEnabled());
    fireEvent.click(publish);
    await waitFor(() =>
      expect(api.putWorkflowRouting).toHaveBeenCalledWith("f4f", {
        default: "default-request",
        rules: [{ workflow: "offboarding", labels_any: ["leaver"] }],
      }),
    );
    expect(api.createWorkflow).toHaveBeenCalledWith("f4f", "default-request", expect.stringContaining("default-request"));
  });
});
