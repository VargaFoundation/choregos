import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SchemaForm, type JsonSchema } from "@/components/schema-form";
import type { GitHubAppStatus } from "@/lib/types";

vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));

const { api } = vi.hoisted(() => ({
  api: { githubStatus: vi.fn(), githubRefresh: vi.fn(), githubRepositories: vi.fn(), githubInstallLink: vi.fn() },
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, ...api } };
});

const installation = {
  id: 7,
  account_login: "acme",
  account_type: "Organization",
  repository_selection: "all",
  suspended: false,
  claimed: true,
  settings_url: "https://github.com/x",
} as const;
const status = (extra: Partial<GitHubAppStatus> = {}): GitHubAppStatus => ({
  configured: true,
  multi_org_claim: "implicit",
  installations: [installation],
  ...extra,
});
const repos = [
  { full_name: "acme/billing-api", private: true, default_branch: "main", installation_id: 7 },
  { full_name: "acme/checkout-web", private: false, default_branch: "main", installation_id: 7 },
];

const schema: JsonSchema = {
  type: "object",
  required: ["repo"],
  properties: { repo: { type: "string", format: "github-repo", description: "The repository" } },
};

function Harness({ initial = {} }: { initial?: Record<string, unknown> }) {
  const [value, setValue] = useState<Record<string, unknown>>(initial);
  return (
    <>
      <SchemaForm schema={schema} value={value} onChange={setValue} />
      <output data-testid="value">{JSON.stringify(value)}</output>
    </>
  );
}
function renderField(initial?: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <Harness initial={initial} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  api.githubStatus.mockResolvedValue(status());
  api.githubRepositories.mockImplementation(async (_org: string, q = "") => ({
    items: repos.filter((r) => r.full_name.includes(q)),
    meta: { has_more: false },
  }));
});

describe("a repository field with format github-repo (S40-04c)", () => {
  it("is rendered by the schema form as a picker, and picking sets owner/repo", async () => {
    renderField();
    fireEvent.click(await screen.findByRole("button", { name: /acme\/checkout-web/ }));
    expect(screen.getByTestId("value").textContent).toBe('{"repo":"acme/checkout-web"}');
    expect(document.body.textContent).not.toMatch(/installation id/i);
  });

  it("filters the list by what is typed", async () => {
    renderField();
    await screen.findByRole("button", { name: /acme\/billing-api/ });
    fireEvent.change(screen.getByPlaceholderText("Search repositories"), { target: { value: "checkout" } });
    await waitFor(() => expect(screen.queryByRole("button", { name: /acme\/billing-api/ })).toBeNull());
    expect(api.githubRepositories).toHaveBeenLastCalledWith("acme", "checkout");
  });

  it("with no installation, offers to install and to refresh instead of an empty list", async () => {
    api.githubStatus.mockResolvedValue(status({ installations: [] }));
    api.githubInstallLink.mockResolvedValue({ url: "https://github.com/apps/choregos-bot/installations/new?state=s" });
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, pathname: "/p/dev/settings", search: "", assign });
    renderField();
    expect(await screen.findByText(/not installed on any GitHub account of this organisation/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Install on GitHub" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://github.com/apps/choregos-bot/installations/new?state=s"));
    expect(api.githubInstallLink).toHaveBeenCalledWith("acme", { project: "dev", return_to: "/p/dev/settings#connections" });
    api.githubRefresh.mockResolvedValue(status());
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(api.githubRefresh).toHaveBeenCalledWith("acme"));
    vi.unstubAllGlobals();
  });

  it("lets a person type the repository, and warns when its owner has no installation", async () => {
    renderField();
    await screen.findByRole("button", { name: /acme\/billing-api/ });
    fireEvent.click(screen.getByRole("button", { name: "Type it instead" }));
    const input = screen.getByPlaceholderText("owner/repo");
    fireEvent.change(input, { target: { value: "acme/other" } });
    expect(screen.queryByText(/is not installed on/)).toBeNull();
    fireEvent.change(input, { target: { value: "othercorp/app" } });
    expect(await screen.findByText(/not installed on othercorp for this organisation/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Install on GitHub" })).toBeTruthy();
    expect(screen.getByTestId("value").textContent).toBe('{"repo":"othercorp/app"}');
  });

  it("falls back to typing when the deployment has no GitHub App", async () => {
    api.githubStatus.mockResolvedValue(status({ configured: false, installations: [] }));
    renderField();
    expect(await screen.findByText(/no GitHub App/)).toBeTruthy();
    expect(screen.getByPlaceholderText("owner/repo")).toBeTruthy();
    expect(api.githubRepositories).not.toHaveBeenCalled();
  });
});
