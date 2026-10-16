import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { Suspense } from "react";
import { describe, expect, it, vi } from "vitest";
import SettingsPage from "@/app/p/[slug]/settings/page";
import { manquantEnPremier } from "@/components/connecteurs";
import { configurationEnClair, workflowsQuiExigent } from "@/components/project-settings/connector-card";
import { resumeDeLaPolitique } from "@/components/project-settings/guardrails-section";
import { connectorTypes } from "@/mocks/data";
import type { ConnectorDto, ProjectRequirement } from "@/lib/types";

const connector = (kind: string, type: string, status: string, extra: Partial<ConnectorDto> = {}) =>
  ({ id: `c-${kind}`, kind, type, config: {}, secret_refs: {}, status, ...extra }) as unknown as ConnectorDto;
const requirement = (capability: string, extra: Partial<ProjectRequirement> = {}): ProjectRequirement => ({
  capability,
  reasons: [`default-simple: needs ${capability}`],
  connector: null,
  default_type: null,
  ...extra,
});

const requirements = [
  requirement("tracker"),
  requirement("scm"),
  requirement("cd"),
  requirement("runtime", { default_type: "agent_sandbox" }),
];
const connectors = [
  connector("tracker", "github-issues", "ok", { config: { repo: "acme/box" } }),
  connector("cd", "demo", "unknown"),
];

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    api: {
      ...reel.api,
      projectRequirements: vi.fn(async () => requirements),
      connectors: vi.fn(async () => connectors),
      connectorTypes: vi.fn(async () => connectorTypes),
      executors: vi.fn(async () => [
        { kind: "agent_sandbox", enabled: true },
        { kind: "k8s_job", enabled: true },
        { kind: "tekton", enabled: false },
      ]),
      policy: vi.fn(async () => ({ id: "p", name: "solo", version: 1, yaml: "kind: Policy\n", is_active: true, json: { budgets: { per_ticket_usd: 25 } } })),
      modelMatrix: vi.fn(async () => ({ entries: [] })),
      projectTools: vi.fn(async () => ({ tools: [], allows_all: false })),
      models: vi.fn(async () => ({ profiles: {}, allow_unvalidated: false, inherited: {} })),
      platformModels: vi.fn(async () => []),
      projectOperations: vi.fn(async () => []),
      orgConnectors: vi.fn(async () => []),
      githubStatus: vi.fn(async () => ({
        configured: true,
        multi_org_claim: "implicit",
        installations: [
          { id: 7, account_login: "acme", account_type: "Organization", repository_selection: "all", suspended: false, claimed: true, settings_url: "https://github.com/x" },
        ],
      })),
    },
  };
});
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));
vi.mock("@/components/operations-du-projet", () => ({ OperationsDuProjet: () => null }));
vi.mock("next/dynamic", () => ({ default: () => () => null }));

async function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    render(
      <QueryClientProvider client={client}>
        <Suspense fallback={null}>
          <SettingsPage params={Promise.resolve({ slug: "dev" })} />
        </Suspense>
      </QueryClientProvider>,
    );
  });
}

describe("the project settings page (S40-03b)", () => {
  it("renders its sections in order, each with a heading", async () => {
    await renderPage();
    await screen.findByTestId("connecteur-tracker");
    const titles = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(titles.filter((t) => ["Connections", "Agents & models", "Guardrails", "Tools"].includes(t ?? ""))).toEqual([
      "Connections",
      "Agents & models",
      "Guardrails",
      "Tools",
    ]);
  });

  it("puts a required connector nothing provides before the others", async () => {
    await renderPage();
    const cards = (await screen.findAllByTestId(/^connecteur-/)).map((card) => card.getAttribute("data-testid"));
    expect(cards.slice(0, 1)).toEqual(["connecteur-scm"]);
  });

  it("shows the demo badge on a demo CD, and says the runtime is provided by the platform", async () => {
    await renderPage();
    const cd = await screen.findByTestId("connecteur-cd");
    expect(within(cd).getByText("Demo stand-in")).toBeTruthy();
    expect(within(cd).getByText("Not tested yet")).toBeTruthy();
    const runtime = screen.getByTestId("connecteur-runtime");
    expect(within(runtime).getByText("Provided by the platform")).toBeTruthy();
  });

  it("a GitHub card says which account's installation it goes through (no installation id)", async () => {
    await renderPage();
    const card = await screen.findByTestId("connecteur-tracker");
    expect(await within(card).findByText("via the Choregos App on acme")).toBeTruthy();
    expect(card.textContent).not.toMatch(/installation id/i);
  });

  it("opens a drawer to configure a connector", async () => {
    await renderPage();
    const scm = await screen.findByTestId("connecteur-scm");
    fireEvent.click(within(scm).getByRole("button", { name: "Configure" }));
    expect(screen.getAllByRole("dialog", { hidden: true }).some((d) => d.hasAttribute("open"))).toBe(true);
  });

  it("the runtime form lists only the executors the deployment runs", async () => {
    await renderPage();
    const runtime = await screen.findByTestId("connecteur-runtime");
    fireEvent.click(within(runtime).getByRole("button", { name: "Configure" }));
    const options = within(runtime).getAllByRole("option", { hidden: true }).map((o) => o.textContent);
    expect(options).toEqual(["Kubernetes · agent-sandbox (warm pool)", "Kubernetes Job"]);
  });
});

describe("the pieces of the page", () => {
  it("reads where a connector points", () => {
    expect(configurationEnClair({ repo: "acme/box" })).toBe("acme/box");
    expect(configurationEnClair({ gitops_repo: "acme/gitops", repo: "" })).toBe("acme/gitops");
    expect(configurationEnClair({})).toBe("");
  });

  it("names the workflows that need a capability", () => {
    expect(workflowsQuiExigent(["default-simple: a", "hotfix: b", "default-simple: c", "no prefix here"])).toEqual([
      "default-simple",
      "hotfix",
    ]);
  });

  it("orders required-and-missing first, keeping the rest in order", () => {
    const parSorte = new Map(requirements.map((r) => [r.capability, r]));
    expect(manquantEnPremier(["tracker", "scm", "cd", "runtime"], parSorte, connectors)).toEqual(["scm", "tracker", "cd", "runtime"]);
  });

  it("summarises a policy", () => {
    expect(resumeDeLaPolitique({ budgets: { per_ticket_usd: 25 }, release_train: { prod: { mode: "train" } } })).toBe(
      "budget $25 per ticket · release prod: train",
    );
    expect(resumeDeLaPolitique(undefined)).toBe("defaults of the organisation");
  });
});
