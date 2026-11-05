import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OrgToolScriptsCard } from "@/components/admin/tool-scripts";
import { ApiError, api } from "@/lib/api";

/**
 * The organisation's Tool scripts card on /admin (S57-05b, ADR 0122 §6): Enabled, Disabled or the
 * deployment's default, saved by its administrator with `PATCH /orgs/{org}` and nothing else of the
 * organisation; read-only for anyone else.
 */
const session = vi.hoisted(() => ({ orgs: [] as { slug: string; name: string; tool_scripts?: "enabled" | "disabled" | null }[] }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "varga", orgs: session.orgs }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, updateOrg: vi.fn() } };
});

function wrap(tool_scripts: "enabled" | "disabled" | null, canEdit = true) {
  session.orgs = [{ slug: "varga", name: "Varga Foundation", tool_scripts }];
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <OrgToolScriptsCard org="varga" canEdit={canEdit} />
    </QueryClientProvider>,
  );
}

const select = () => screen.getByLabelText("Tool scripts") as HTMLSelectElement;
const pick = (value: string) => fireEvent.change(select(), { target: { value } });
const save = () => fireEvent.click(screen.getByRole("button", { name: "Save tool scripts" }));

describe("the organisation's Tool scripts card", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.updateOrg).mockResolvedValue({ slug: "varga", name: "Varga Foundation" } as never);
  });

  it("offers the three choices and shows the deployment's default when the organisation has none", () => {
    wrap(null);
    expect(select().value).toBe("");
    expect([...select().options].map((o) => o.textContent)).toEqual(["Deployment default", "Enabled", "Disabled"]);
    expect(screen.getByTestId("org-tool-scripts-meaning")).toHaveTextContent("follows the deployment's default");
    expect(screen.getByTestId("org-tool-scripts-meaning")).toHaveTextContent("this page does not see it");
    expect((screen.getByRole("button", { name: "Save tool scripts" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("is prefilled with the stored choice and says what it means", () => {
    wrap("enabled");
    expect(select().value).toBe("enabled");
    expect(screen.getByTestId("org-tool-scripts-meaning")).toHaveTextContent("Each project whose policy offers them");
  });

  it("saves exactly the organisation's choice", async () => {
    wrap(null);
    pick("disabled");
    expect(screen.getByTestId("org-tool-scripts-meaning")).toHaveTextContent("No run of this organisation is offered them");
    save();
    await waitFor(() => expect(api.updateOrg).toHaveBeenCalledWith("varga", { tool_scripts: "disabled" }));
    expect(await screen.findByText("Tool scripts saved")).toBeTruthy();
  });

  it("returns to the deployment's default with null", async () => {
    wrap("enabled");
    pick("");
    save();
    await waitFor(() => expect(api.updateOrg).toHaveBeenCalledWith("varga", { tool_scripts: null }));
  });

  it("shows a refusal inline", async () => {
    vi.mocked(api.updateOrg).mockRejectedValue(new ApiError(403, { title: "Forbidden", detail: "switched by its org_admin" }));
    wrap(null);
    pick("enabled");
    save();
    expect(await screen.findByText(/org_admin|Forbidden/)).toBeTruthy();
  });

  it("is read-only for someone who does not administer the organisation", () => {
    wrap("disabled", false);
    expect(select().disabled).toBe(true);
    expect(select().value).toBe("disabled");
    expect(screen.queryByRole("button", { name: "Save tool scripts" })).toBeNull();
    expect(screen.getByText(/Only an organisation administrator changes this setting/)).toBeTruthy();
  });
});
