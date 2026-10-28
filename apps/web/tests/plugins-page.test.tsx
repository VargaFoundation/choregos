import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PluginsPage from "@/app/catalogue/plugins/page";
import { ApiError } from "@/lib/api";
import { isPending, shortDigest, trustLabel } from "@/lib/plugins";
import type { InstalledPlugin, MeDto, OrgConnector, PluginApproval, PluginSource } from "@/lib/types";

/**
 * S43-08: Catalogue → Plugins. Install answers 201 or 202 (a second administrator), the approval the API
 * refuses to the one who asked shows its error, upgrade and uninstall say what they did (the kept list),
 * a server says its connector's state with a link to configure it, and a member reads without a gesture.
 */
const { api, session } = vi.hoisted(() => ({
  api: {
    plugins: vi.fn(),
    pluginSources: vi.fn(),
    pluginApprovals: vi.fn(),
    orgConnectors: vi.fn(),
    installPlugin: vi.fn(),
    upgradePlugin: vi.fn(),
    uninstallPlugin: vi.fn(),
    approvePlugin: vi.fn(),
  },
  session: { me: null as MeDto | null },
}));

vi.mock("next/navigation", () => ({ usePathname: () => "/catalogue/plugins", useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: session.me }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, ...api } };
});

const ADMIN: MeDto = {
  id: "u1",
  email: "augustin@acme.dev",
  display_name: "Augustin",
  memberships: [{ org: "acme", role: "org_admin", user_id: "u1", email: "augustin@acme.dev" }],
};
const MEMBER: MeDto = {
  id: "u3",
  email: "marc@acme.dev",
  display_name: "Marc",
  memberships: [{ org: "acme", role: "viewer", user_id: "u3", email: "marc@acme.dev" }],
};

const DIGEST = `sha256:${"ab12".repeat(16)}`;

function installed(name: string, extra: Partial<InstalledPlugin> = {}): InstalledPlugin {
  return {
    name,
    version: "1.0.0",
    source: "varga",
    digest: DIGEST,
    publisher: "Varga Foundation",
    license: "Apache-2.0",
    description: `the ${name} plugin`,
    default: false,
    contents: { agents: [], skills: [], templates: [], evals: [], procedures: [], servers: [] },
    installed_by: "augustin@acme.dev",
    installed_at: "2026-10-25T10:00:00Z",
    latest: null,
    ...extra,
  };
}

const SOURCES: PluginSource[] = [
  {
    name: "varga",
    index: "oci://ghcr.io/vargafoundation/choregos-agents/index:stable",
    trust: { mode: "keyless", identity: "https://github.com/VargaFoundation/choregos-agents/.*", issuer: "https://token.actions.githubusercontent.com" },
    plugins: [
      {
        name: "hello",
        description: "one of everything",
        versions: [
          { version: "1.1.0", core: ">=0.22.0", admissible: false },
          { version: "1.0.0", core: ">=0.20.0", admissible: true },
        ],
      },
      { name: "notes", description: null, versions: [{ version: "0.3.0", core: ">=0.20.0", admissible: true }] },
      { name: "triage", description: null, versions: [{ version: "1.2.0", core: ">=0.20.0", admissible: true }] },
    ],
    error: null,
  },
  {
    name: "partner",
    index: "https://plugins.partner.example/index.yaml",
    trust: { mode: "key", key_fingerprint: "sha256:9c2e" },
    plugins: [],
    error: "fetch: plugins.partner.example did not answer",
  },
];

const SERVICENOW = installed("servicenow", {
  version: "0.1.0",
  default: true,
  installed_by: null,
  latest: "0.2.0",
  contents: {
    agents: ["incident-triager"],
    skills: ["encoded-queries"],
    templates: ["plugin:servicenow/incident-flow"],
    evals: ["incident-triager/triage.yaml"],
    procedures: ["connector.mcp.servicenow"],
    servers: ["servicenow"],
  },
});
const TRIAGE = installed("triage", {
  latest: "1.2.0",
  contents: { agents: ["triage-bot", "triage-lead"], skills: ["sizing"], templates: [], evals: [], procedures: [], servers: [] },
});

const WAITING: PluginApproval = {
  id: "pa-1",
  action: "install",
  source: "varga",
  name: "hello",
  version: "1.0.0",
  digest: DIGEST,
  reason: "it brings the server `hello`",
  default: false,
  requested_by: "augustin@acme.dev",
  status: "pending",
  decided_by: null,
  created_at: "2026-10-28T09:00:00Z",
  decided_at: null,
};

const CONNECTOR = { name: "servicenow", kind: "mcp", type: "mcp", status: "unconfigured" } as OrgConnector;

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  session.me = ADMIN;
  api.plugins.mockResolvedValue({ items: [SERVICENOW, TRIAGE] });
  api.pluginSources.mockResolvedValue({ sources: SOURCES });
  api.pluginApprovals.mockResolvedValue({ items: [WAITING] });
  api.orgConnectors.mockResolvedValue([CONNECTOR]);
});

function page() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PluginsPage />
    </QueryClientProvider>,
  );
}

describe("the plugins page's pure parts", () => {
  it("tells a 202 from a 201 by its status, shortens a digest and says a trust policy", () => {
    expect(isPending({ status: "pending_approval", approval_id: "x", reason: "r" })).toBe(true);
    expect(isPending(SERVICENOW)).toBe(false);
    expect(shortDigest(DIGEST)).toBe("sha256:ab12ab12ab12");
    expect(trustLabel(SOURCES[0]!.trust)).toMatch(/choregos-agents.*token\.actions/);
    expect(trustLabel(SOURCES[1]!.trust)).toContain("sha256:9c2e");
    expect(trustLabel({ mode: "none" })).toMatch(/Offline/);
  });
});

describe("Catalogue → Plugins", () => {
  it("lists sources, what they offer, and each installed plugin with its contents and server", async () => {
    page();
    const card = await screen.findByTestId("installed-servicenow");
    expect(card).toHaveTextContent("Default");
    expect(card).toHaveTextContent("0.2.0 available");
    expect(card).toHaveTextContent("sha256:ab12ab12ab12");
    expect(card).not.toHaveTextContent(DIGEST);
    expect(card).toHaveTextContent("the platform (a default plugin)");
    expect(within(card).getByTestId("contents-templates")).toHaveTextContent("plugin:servicenow/incident-flow");
    expect(within(card).getByTestId("contents-procedures")).toHaveTextContent("connector.mcp.servicenow");
    const server = await within(card).findByTestId("server-servicenow");
    await waitFor(() => expect(server).toHaveTextContent("unconfigured"));
    expect(within(server).getByRole("link", { name: "Configure" })).toHaveAttribute("href", "/admin/connectors?connector=servicenow");

    expect(screen.getByTestId("trust-varga")).toHaveTextContent("keyless");
    expect(screen.getByTestId("source-partner")).toHaveTextContent("did not answer");
    const hello = screen.getByTestId("entry-varga-hello");
    expect(hello).toHaveTextContent("needs core >=0.22.0");
    expect(within(hello).getAllByRole("option").map((o) => o.textContent)).toEqual(["1.0.0"]);
    // Installed already: upgraded or uninstalled above, not installed again.
    const triage = screen.getByTestId("entry-varga-triage");
    expect(within(triage).queryByRole("button", { name: "Install" })).toBeNull();
    expect(triage).toHaveTextContent("Installed (1.0.0)");
  });

  it("an install answered 201 says it is installed", async () => {
    api.installPlugin.mockResolvedValue(installed("notes", { version: "0.3.0" }));
    page();
    const notes = await screen.findByTestId("entry-varga-notes");
    fireEvent.click(within(notes).getByRole("button", { name: "Install" }));
    await waitFor(() => expect(api.installPlugin).toHaveBeenCalledWith("acme", { source: "varga", name: "notes", version: "0.3.0" }));
    expect(await screen.findByText("notes 0.3.0 is installed.")).toBeInTheDocument();
    expect(screen.queryByTestId("outcome-pending")).toBeNull();
  });

  it("an install answered 202 waits for a second administrator, with the reasons", async () => {
    api.installPlugin.mockResolvedValue({
      status: "pending_approval",
      approval_id: "pa-2",
      reason: "it brings the server `hello`; its skill `greeting` is high risk (script)",
    });
    page();
    const hello = await screen.findByTestId("entry-varga-hello");
    fireEvent.click(within(hello).getByRole("button", { name: "Install" }));
    const note = await screen.findByTestId("outcome-pending");
    expect(screen.getByText("Waiting for a second administrator", { selector: "p" })).toBeInTheDocument();
    expect(note).toHaveTextContent("Nothing is installed yet: hello 1.0.0");
    expect(note).toHaveTextContent("its skill `greeting` is high risk (script)");
    expect(screen.queryByText(/^hello 1\.0\.0 is installed\./)).toBeNull();
  });

  it("approving shows the API's refusal to the administrator who asked, and installs for another", async () => {
    api.approvePlugin.mockRejectedValueOnce(
      new ApiError(403, { title: "Forbidden", detail: "the administrator who asked cannot approve" }),
    );
    page();
    const row = await screen.findByTestId("approval-pa-1");
    expect(row).toHaveTextContent("You asked for it");
    expect(row).toHaveTextContent("it brings the server `hello`");
    fireEvent.click(within(row).getByRole("button", { name: "Approve and install" }));
    expect(await within(row).findByRole("alert")).toHaveTextContent("the administrator who asked cannot approve");

    api.approvePlugin.mockResolvedValueOnce({ ...WAITING, status: "approved", decided_by: "second@acme.dev" });
    fireEvent.click(within(row).getByRole("button", { name: "Approve and install" }));
    expect(await screen.findByText("hello 1.0.0 is installed.")).toBeInTheDocument();
    expect(api.approvePlugin).toHaveBeenLastCalledWith("acme", "pa-1");
  });

  it("upgrades to the latest version and names what was offered instead of overwritten", async () => {
    api.upgradePlugin.mockResolvedValue({ plugin: { ...TRIAGE, version: "1.2.0", latest: null }, previous_version: "1.0.0", offered: ["triage-lead"] });
    page();
    const card = await screen.findByTestId("installed-triage");
    fireEvent.click(within(card).getByRole("button", { name: "Upgrade to 1.2.0" }));
    await waitFor(() => expect(api.upgradePlugin).toHaveBeenCalledWith("acme", "triage", { version: "1.2.0" }));
    expect(await screen.findByText(/moved from 1\.0\.0 to 1\.2\.0/)).toHaveTextContent("not overwritten: triage-lead");
  });

  it("an upgrade answered 202 waits too", async () => {
    api.upgradePlugin.mockResolvedValue({ status: "pending_approval", approval_id: "pa-3", reason: "it brings the server `servicenow`" });
    page();
    const card = await screen.findByTestId("installed-servicenow");
    fireEvent.click(within(card).getByRole("button", { name: "Upgrade to 0.2.0" }));
    expect(await screen.findByTestId("outcome-pending")).toHaveTextContent("servicenow 0.2.0 needs another administrator");
  });

  it("uninstalls after a confirmation and shows what was removed and what was kept", async () => {
    api.uninstallPlugin.mockResolvedValue({
      name: "triage",
      version: "1.0.0",
      removed: { agents: ["triage-bot"], skills: [] },
      kept: { agents: ["triage-lead"], skills: ["sizing"] },
    });
    page();
    const card = await screen.findByTestId("installed-triage");
    fireEvent.click(within(card).getByRole("button", { name: "Uninstall" }));
    expect(api.uninstallPlugin).not.toHaveBeenCalled();
    fireEvent.click(within(within(card).getByTestId("confirmation")).getByRole("button", { name: "Uninstall" }));
    const note = await screen.findByTestId("outcome-uninstalled");
    expect(api.uninstallPlugin).toHaveBeenCalledWith("acme", "triage");
    expect(note).toHaveTextContent("Removed — agents: triage-bot; skills: none.");
    expect(note).toHaveTextContent("Kept as the organisation's own");
    expect(note).toHaveTextContent("agents: triage-lead; skills: sizing.");
  });

  it("a member reads everything, and every gesture is disabled with the reason", async () => {
    session.me = MEMBER;
    page();
    await screen.findByTestId("installed-servicenow");
    await screen.findByTestId("approval-pa-1");
    const reason = screen.getByTestId("plugins-rights");
    expect(reason).toHaveTextContent("Only an organisation administrator");
    const gestures = [
      ...screen.getAllByRole("button", { name: /^(Install|Uninstall|Upgrade to .*|Approve and install)$/ }),
    ];
    expect(gestures.length).toBeGreaterThanOrEqual(6);
    for (const button of gestures) {
      expect(button).toBeDisabled();
      expect(button).toHaveAttribute("aria-describedby", "plugins-not-admin");
    }
    expect(screen.queryByRole("link", { name: "Configure" })).toBeNull();
    expect(screen.getByRole("link", { name: "View its connector" })).toBeInTheDocument();
    expect(api.installPlugin).not.toHaveBeenCalled();
  });
});
