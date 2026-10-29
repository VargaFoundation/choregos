import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import IdentityPage from "@/app/admin/identity/page";
import { api } from "@/lib/api";
import { withoutCoreRows } from "@/lib/identity";
import type { AdminSection } from "@/lib/types";

/**
 * S44-04: one Identity page. The instance's sign-in was a card on the Platform page and, again, a
 * plugin section reading the same route; a plugin's own identity section was a third entry.
 */
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));

const instanceRows: AdminSection = {
  id: "acme-instance-sign-in",
  title: "Instance sign-in",
  icon: "log-in",
  scope: "platform",
  permission: "platform:admin",
  blocks: [
    { kind: "table", title: "Sign-in", list: "/platform/identity", columns: [{ key: "label", label: "Setting" }] },
  ],
};
const sso: AdminSection = {
  id: "acme-sso",
  title: "Single sign-on",
  icon: "fingerprint",
  scope: "organisation",
  permission: "member:manage",
  description: "Your identity provider signs your people in.",
  blocks: [{ kind: "action", label: "Rotate the SCIM token", path: "/orgs/{org}/scim/rotate" }],
};
const limits: AdminSection = {
  id: "acme-limits",
  title: "Limits",
  icon: "gauge",
  scope: "platform",
  permission: "platform:admin",
  blocks: [{ kind: "action", label: "Reset", path: "/limits/reset" }],
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      platformIdentity: vi.fn().mockResolvedValue({
        items: [{ key: "oidc.issuer", label: "OIDC issuer", value: "https://sso.example.test", source: "settings" }],
      }),
      adminSections: vi.fn(),
    },
  };
});

describe("the Identity page (S44-04)", () => {
  it("shows the instance's sign-in once, and the plugin's identity sections, not the others", async () => {
    vi.mocked(api.adminSections).mockResolvedValue([instanceRows, sso, limits]);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <IdentityPage />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("https://sso.example.test")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Single sign-on" })).toBeInTheDocument();
    expect(screen.getByText("Your identity provider signs your people in.")).toBeInTheDocument();
    // The plugin's instance section only repeated the card: it is not drawn a second time.
    expect(screen.queryByRole("heading", { name: "Instance sign-in" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Limits" })).toBeNull();
  });

  it("drops only the blocks that read the core's sign-in route", () => {
    const mixed: AdminSection = {
      ...instanceRows,
      blocks: [
        ...instanceRows.blocks,
        { kind: "form", title: "Edit", schema: {}, read: "/ee/identity?x=1", write: "/ee/identity" },
        { kind: "form", title: "Same", schema: {}, read: "/platform/identity?fresh=1", write: "/ee/x" },
      ],
    };
    expect(withoutCoreRows(mixed).blocks.map((b) => ("title" in b ? b.title : ""))).toEqual(["Edit"]);
  });
});
