import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OrganisationCard } from "@/components/admin/organisation-card";
import { ApiError, api } from "@/lib/api";

/** The Organisation card on /admin (S40-24): rename, currency, and the slug for a platform administrator. */
const session = vi.hoisted(() => ({ choisirOrg: vi.fn() }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({
    org: "varga",
    orgs: [{ slug: "varga", name: "Varga Foundation", role: "org_admin", currency: "EUR" }],
    choisirOrg: session.choisirOrg,
  }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, updateOrg: vi.fn(), platformIdentity: vi.fn(), orgs: vi.fn().mockResolvedValue([]) } };
});

const assign = vi.fn();
const user = {
  click: async (el: Element) => fireEvent.click(el),
  clear: async (el: Element) => fireEvent.change(el, { target: { value: "" } }),
  type: async (el: Element, text: string) => fireEvent.change(el, { target: { value: (el as HTMLInputElement).value + text } }),
  selectOptions: async (el: Element, value: string) => fireEvent.change(el, { target: { value } }),
};

function wrap(canEdit = true) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <OrganisationCard org="varga" canEdit={canEdit} />
    </QueryClientProvider>,
  );
}

describe("the Organisation card", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.platformIdentity).mockRejectedValue(new ApiError(403, { title: "Forbidden" }));
    vi.mocked(api.updateOrg).mockResolvedValue({ slug: "varga", name: "Diametral", role: "org_admin", currency: "USD" } as never);
    Object.defineProperty(window, "location", { value: { assign, origin: "http://console.test" }, configurable: true });
  });

  it("is prefilled, and says what the slug is for", async () => {
    wrap();
    expect((await screen.findByLabelText("Display name") as HTMLInputElement).value).toBe("Varga Foundation");
    expect((screen.getByLabelText("Currency") as HTMLSelectElement).value).toBe("EUR");
    expect(screen.getByText(/used in every URL, MCP address and identity-provider group/i)).toBeTruthy();
    expect(screen.getByText("varga")).toBeTruthy();
  });

  it("saves exactly the name and the currency", async () => {
    wrap();
    const name = await screen.findByLabelText("Display name");
    await user.clear(name);
    await user.type(name, "Diametral");
    await user.selectOptions(screen.getByLabelText("Currency"), "USD");
    await user.click(screen.getByRole("button", { name: "Save the organisation" }));
    await waitFor(() => expect(api.updateOrg).toHaveBeenCalledWith("varga", { name: "Diametral", currency: "USD" }));
    expect(await screen.findByText("Organisation saved")).toBeTruthy();
  });

  it("refuses an empty name without calling the API, and shows a refusal inline", async () => {
    wrap();
    const name = await screen.findByLabelText("Display name");
    await user.clear(name);
    expect((screen.getByRole("button", { name: "Save the organisation" }) as HTMLButtonElement).disabled).toBe(true);
    await user.type(name, "X");
    vi.mocked(api.updateOrg).mockRejectedValue(new ApiError(403, { title: "Forbidden", detail: "needs the org_admin role" }));
    await user.click(screen.getByRole("button", { name: "Save the organisation" }));
    expect(await screen.findByText(/org_admin role|Forbidden/)).toBeTruthy();
  });

  it("is read-only for someone who does not administer the organisation", async () => {
    wrap(false);
    expect(((await screen.findByLabelText("Display name")) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText("Currency") as HTMLSelectElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "Save the organisation" })).toBeNull();
  });

  it("hides the slug action from anyone who is not a platform administrator", async () => {
    wrap();
    await waitFor(() => expect(api.platformIdentity).toHaveBeenCalled());
    await screen.findByLabelText("Display name");
    expect(screen.queryByRole("button", { name: "Change the slug" })).toBeNull();
  });

  describe("for a platform administrator", () => {
    beforeEach(() => {
      vi.mocked(api.platformIdentity).mockResolvedValue({ items: [] } as never);
    });

    it("lists what a slug change breaks, validates the slug, then follows the organisation", async () => {
      wrap();
      await user.click(await screen.findByRole("button", { name: "Change the slug" }));
      const box = screen.getByTestId("confirmation");
      for (const broken of [/console URLs already copied/, /\/mcp\/projects\/varga:<project>/, /\/a2a\/orgs\/varga/, /choregos:varga:<group>/, /--org varga/]) {
        expect(box.textContent).toMatch(broken);
      }
      expect(box.textContent).toMatch(/global memories follow/);
      await user.type(screen.getByRole("textbox", { name: /New slug/ }), "Bad Slug");
      await user.click(screen.getByRole("button", { name: "Change the slug", hidden: false }));
      expect(await screen.findByText(/lowercase letters, digits and dashes/)).toBeTruthy();
      expect(api.updateOrg).not.toHaveBeenCalled();
      const input = screen.getByRole("textbox", { name: /New slug/ });
      await user.clear(input);
      await user.type(input, "diametral");
      await user.click(screen.getByRole("button", { name: "Change the slug" }));
      await waitFor(() => expect(api.updateOrg).toHaveBeenCalledWith("varga", { slug: "diametral" }));
      expect(session.choisirOrg).toHaveBeenCalledWith("diametral");
      expect(assign).toHaveBeenCalledWith("http://console.test/admin");
    });

    it("shows a taken slug as a 409 inline and stays on the organisation", async () => {
      vi.mocked(api.updateOrg).mockRejectedValue(new ApiError(409, { title: "Conflict", detail: "organisation `taken` already exists" }));
      wrap();
      await user.click(await screen.findByRole("button", { name: "Change the slug" }));
      await user.type(screen.getByRole("textbox", { name: /New slug/ }), "taken");
      await user.click(screen.getByRole("button", { name: "Change the slug" }));
      expect(await screen.findByText(/already exists/)).toBeTruthy();
      expect(session.choisirOrg).not.toHaveBeenCalled();
      expect(assign).not.toHaveBeenCalled();
    });
  });
});
