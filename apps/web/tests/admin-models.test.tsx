import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ModelsPage from "@/app/admin/models/page";
import { api } from "@/lib/api";
import type { ModelCatalogue } from "@/lib/types";

/** The Models page of the administration (S39-07c, ADR 0060). */
const session = vi.hoisted(() => ({ role: "org_admin" }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ org: "acme", orgs: [{ slug: "acme", name: "Acme", role: session.role }] }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      modelCatalogue: vi.fn(),
      updateModelCatalogueEntry: vi.fn().mockResolvedValue({}),
      putModelCatalogueSettings: vi.fn().mockResolvedValue({}),
      previewModelCuration: vi.fn(),
      syncModelCatalogue: vi.fn().mockResolvedValue({ added: 1, missing: 0, seen: 2 }),
      addModelCatalogueEntry: vi.fn().mockResolvedValue({}),
    },
  };
});

function catalogue(over: Partial<ModelCatalogue> = {}): ModelCatalogue {
  return {
    mode: "open",
    gateway: { type: "litellm", capabilities: ["list_models", "hard_budget"] },
    tiers: { standard: null },
    entries: [
      {
        id: "e1",
        model_name: "platform/standard",
        display_name: "Standard",
        source: "gateway",
        enabled: true,
        allowed_roles: [],
        usages: ["agent"],
        price_override: null,
        gateway: { provider: "anthropic", input_cost_per_1k: 0.003, output_cost_per_1k: 0.015 },
        last_seen_at: "2026-10-15T10:00:00Z",
        missing_since: null,
        notes: "",
      },
      {
        id: "e2",
        model_name: "platform/old",
        display_name: "Old",
        source: "gateway",
        enabled: false,
        allowed_roles: ["review"],
        usages: ["agent"],
        price_override: { input_per_1k: 0.1, output_per_1k: 0.2 },
        gateway: null,
        last_seen_at: null,
        missing_since: "2026-10-10T10:00:00Z",
        notes: "",
      },
    ],
    ...over,
  };
}

function show() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ModelsPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  session.role = "org_admin";
  vi.mocked(api.modelCatalogue).mockResolvedValue(catalogue());
  vi.mocked(api.updateModelCatalogueEntry).mockClear();
  vi.mocked(api.putModelCatalogueSettings).mockClear();
});

describe("the Models page", () => {
  it("lists the entries with their price, their missing badge and the gateway's capabilities", async () => {
    show();
    const row = await screen.findByTestId("model-platform/standard");
    expect(row).toHaveTextContent("anthropic");
    expect(row).toHaveTextContent("0.003 / 0.015");
    expect(screen.getByTestId("model-platform/old")).toHaveTextContent("gone since");
    expect(screen.getByTestId("model-platform/old")).toHaveTextContent("0.1 / 0.2");
    expect(screen.getByTestId("gateway")).toHaveTextContent("litellm");
    expect(screen.getByTestId("gateway")).toHaveTextContent("hard_budget");
  });

  it("toggling a model calls the API with the change", async () => {
    show();
    const box = await screen.findByLabelText("Enable platform/standard");
    fireEvent.click(box);
    await waitFor(() => expect(api.updateModelCatalogueEntry).toHaveBeenCalledWith("acme", "e1", { enabled: false }));
  });

  it("limits a model to a stage role", async () => {
    show();
    fireEvent.click(await screen.findByLabelText("review for platform/standard"));
    await waitFor(() =>
      expect(api.updateModelCatalogueEntry).toHaveBeenCalledWith("acme", "e1", { allowed_roles: ["review"] }),
    );
  });

  it("saves a price override and clears it", async () => {
    show();
    fireEvent.change(await screen.findByLabelText("Input price of platform/standard"), { target: { value: "0.5" } });
    fireEvent.change(screen.getByLabelText("Output price of platform/standard"), { target: { value: "1.5" } });
    fireEvent.click(within(screen.getByTestId("model-platform/standard")).getByRole("button", { name: "Override" }));
    await waitFor(() =>
      expect(api.updateModelCatalogueEntry).toHaveBeenCalledWith("acme", "e1", {
        price_override: { input_per_1k: 0.5, output_per_1k: 1.5 },
      }),
    );
    fireEvent.click(within(screen.getByTestId("model-platform/old")).getByRole("button", { name: "Clear" }));
    await waitFor(() =>
      expect(api.updateModelCatalogueEntry).toHaveBeenCalledWith("acme", "e2", { clear_price_override: true }),
    );
  });

  it("shows what curated mode would refuse before it switches", async () => {
    vi.mocked(api.previewModelCuration).mockResolvedValue({
      refused: [{ kind: "agent", ref: "triage", model: "platform/old" }],
    });
    show();
    fireEvent.click(await screen.findByRole("button", { name: /Switch to curated/ }));
    const preview = await screen.findByTestId("curation-preview");
    expect(preview).toHaveTextContent("would refuse 1 use");
    expect(preview).toHaveTextContent("agent triage (platform/old)");
    expect(api.putModelCatalogueSettings).not.toHaveBeenCalled(); // nothing changed yet
    fireEvent.click(screen.getByRole("button", { name: "Switch to curated" }));
    await waitFor(() =>
      expect(api.putModelCatalogueSettings).toHaveBeenCalledWith("acme", expect.objectContaining({ mode: "curated" })),
    );
  });

  it("sets a tier among the enabled models only", async () => {
    show();
    const tier = await screen.findByLabelText("Model of the standard tier");
    expect(within(tier).getByRole("option", { name: "Standard" })).toBeInTheDocument();
    expect(within(tier).queryByRole("option", { name: "Old" })).toBeNull(); // disabled
    fireEvent.change(tier, { target: { value: "platform/standard" } });
    await waitFor(() =>
      expect(api.putModelCatalogueSettings).toHaveBeenCalledWith("acme", {
        mode: "open",
        tiers: expect.objectContaining({ standard: "platform/standard" }),
      }),
    );
  });

  it("says that budgets are enforced by Choregos only when the gateway cannot cap a key", async () => {
    vi.mocked(api.modelCatalogue).mockResolvedValue(
      catalogue({ gateway: { type: "openai-compatible", capabilities: ["list_models"] } }),
    );
    show();
    expect(await screen.findByText("Budgets are enforced by Choregos only: this gateway cannot cap a key.")).toBeInTheDocument();
  });

  it("does not say it when the gateway can", async () => {
    show();
    await screen.findByTestId("gateway");
    expect(screen.queryByText(/enforced by Choregos only/)).toBeNull();
  });

  it("syncs from the gateway and adds a model by hand", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Sync from gateway" }));
    await waitFor(() => expect(api.syncModelCatalogue).toHaveBeenCalledWith("acme"));
    expect(await screen.findByText(/Synced: 1 added/)).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("provider/model-name"), { target: { value: "wild/chat" } });
    fireEvent.click(screen.getByRole("button", { name: "Add a model manually" }));
    await waitFor(() => expect(api.addModelCatalogueEntry).toHaveBeenCalledWith("acme", { model_name: "wild/chat" }));
  });

  it("is read-only for someone who is not an organisation administrator", async () => {
    session.role = "developer";
    show();
    await screen.findByTestId("model-platform/standard");
    expect(screen.getByLabelText("Enable platform/standard")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Sync from gateway" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Switch to curated/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add a model manually" })).toBeNull();
    expect(screen.queryByLabelText("Input price of platform/standard")).toBeNull();
    expect(screen.getByLabelText("Model of the standard tier")).toBeDisabled();
  });
});
