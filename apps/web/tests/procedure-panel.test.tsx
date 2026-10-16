import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProcedurePanel } from "@/components/procedures/procedure-panel";
import { api } from "@/lib/api";
import { markSeen, presetOfMcpUrl, hostOf, wasSeen } from "@/lib/procedures";
import { proceduresApi } from "@/mocks/procedures";
import type { Procedure } from "@/lib/types";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, listProcedures: vi.fn(), getProcedure: vi.fn() } };
});

/** The real procedures, as the mock API serves them: the panel is tested against what ships. */
function serve() {
  vi.mocked(api.listProcedures).mockImplementation(async (query = {}) => {
    const params = new URLSearchParams(Object.entries(query).filter(([, v]) => v) as [string, string][]);
    return proceduresApi("GET", `/procedures?${params}`) as never;
  });
  vi.mocked(api.getProcedure).mockImplementation(async (id) => proceduresApi("GET", `/procedures/${id}`) as never);
}

const view = (ui: React.ReactElement) =>
  render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  document.body.className = "";
  serve();
});

describe("the setup procedure panel (S39-23b)", () => {
  it("opens by itself the first time, at the first section, with the procedure's title", async () => {
    view(<ProcedurePanel target="connector:documents/sharepoint" />);
    const panel = await screen.findByTestId("procedure-panel");
    expect(within(panel).getByRole("heading", { level: 2 })).toHaveTextContent("Connect SharePoint and OneDrive");
    const sections = within(panel).getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(sections.slice(0, 2)).toEqual(["Before you start", "At Microsoft"]);
    expect(within(panel).getByTestId("procedure-section-fails")).toHaveTextContent("AADSTS7000215");
    expect(wasSeen("connector.documents.sharepoint")).toBe(true);
  });

  it("does not open again for someone who has seen it, but stays one click away", async () => {
    markSeen("connector.documents.sharepoint");
    view(<ProcedurePanel target="connector:documents/sharepoint" />);
    const button = await screen.findByRole("button", { name: "How to set this up" });
    expect(screen.queryByTestId("procedure-panel")).toBeNull();
    fireEvent.click(button);
    expect(screen.getByTestId("procedure-panel")).toBeInTheDocument();
    expect(button).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(screen.getByTestId("procedure-close"));
    expect(screen.queryByTestId("procedure-panel")).toBeNull();
  });

  it("opens by itself only when asked to", async () => {
    view(<ProcedurePanel target="connector:documents/sharepoint" autoOpen={false} />);
    await screen.findByRole("button", { name: "How to set this up" });
    expect(screen.queryByTestId("procedure-panel")).toBeNull();
    expect(wasSeen("connector.documents.sharepoint")).toBe(false);
  });

  it("shows this platform's webhook address in a copy box", async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: write }, configurable: true });
    view(<ProcedurePanel target="connector:tracker/jira" />);
    const box = await screen.findByTestId("procedure-value-webhook_url:jira");
    expect(box).toHaveTextContent("https://choregos.example.com/api/v1/webhooks/jira");
    expect(box).toHaveTextContent("Webhook URL for jira");
    fireEvent.click(within(box).getByRole("button", { name: /Copy/ }));
    await waitFor(() => expect(write).toHaveBeenCalledWith("https://choregos.example.com/api/v1/webhooks/jira"));
    await within(box).findByRole("button", { name: /Copy/ }).then((b) => expect(b).toHaveTextContent("Copied"));
  });

  it("lists the fields, the hosts to open and the vendor's own documents as links", async () => {
    view(<ProcedurePanel target="connector:documents/sharepoint" />);
    const panel = await screen.findByTestId("procedure-panel");
    expect(within(panel).getByTestId("procedure-fields")).toHaveTextContent("client_secret");
    expect(within(panel).getByTestId("procedure-egress")).toHaveTextContent("graph.microsoft.com");
    const link = within(within(panel).getByTestId("procedure-vendor-docs")).getAllByRole("link")[0]!;
    expect(link.getAttribute("href")).toMatch(/^https:\/\//);
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  it("renders a link that is not among the vendor documents as plain text", async () => {
    const crafted = {
      id: "connector.demo.thing",
      title: "A thing",
      version: 1,
      audience: "org_admin",
      applies_to: { connector: "demo/thing" },
      link: null,
      reviewed: "2026-10-16",
      sections: [
        {
          key: "before",
          title: "Before you start",
          markdown: "[ok](https://vendor.example/docs) and [bad](https://evil.example/x) and [js](javascript:alert(1))",
        },
      ],
      values: {},
      egress: [],
      fields: {},
      vendor_docs: [{ title: "Vendor", url: "https://vendor.example/docs" }],
      origin: "shipped",
    } as unknown as Procedure;
    vi.mocked(api.listProcedures).mockResolvedValue([crafted] as never);
    vi.mocked(api.getProcedure).mockResolvedValue(crafted);
    view(<ProcedurePanel target="connector:demo/thing" />);
    const section = await screen.findByTestId("procedure-section-before");
    expect(within(section).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["https://vendor.example/docs"]);
    expect(section).toHaveTextContent("ok and bad and js");
  });

  it("switches to a preset when the lookup names one", async () => {
    const generic = view(<ProcedurePanel target="connector:mcp/mcp" autoOpen={false} />);
    fireEvent.click(await screen.findByRole("button", { name: "How to set this up" }));
    expect(screen.getByTestId("procedure-panel")).toHaveTextContent("Connect an MCP server");
    generic.unmount();
    view(<ProcedurePanel target="connector:mcp/mcp" preset="atlassian-rovo" autoOpen={false} />);
    fireEvent.click(await screen.findByRole("button", { name: "How to set this up" }));
    const panel = screen.getByTestId("procedure-panel");
    expect(panel).toHaveTextContent("Rovo MCP Server");
    expect(panel).toHaveTextContent("https://mcp.atlassian.com/v1/mcp");
  });

  it("renders nothing when there is no procedure, or when the API cannot be reached", async () => {
    vi.mocked(api.listProcedures).mockResolvedValue([]);
    const none = view(<ProcedurePanel target="connector:nothing/here" />);
    await waitFor(() => expect(api.listProcedures).toHaveBeenCalled());
    expect(none.container).toBeEmptyDOMElement();
    none.unmount();
    vi.mocked(api.listProcedures).mockRejectedValue(new Error("down"));
    const down = view(<ProcedurePanel target="connector:documents/sharepoint" />);
    await waitFor(() => expect(api.listProcedures).toHaveBeenCalledTimes(2));
    expect(down.container).toBeEmptyDOMElement();
  });

  it("skips the lookup when the caller names the procedure", async () => {
    view(<ProcedurePanel target="connector:mcp/mcp" procedure="connector.mcp.mcp.atlassian-rovo" autoOpen={false} />);
    await screen.findByRole("button", { name: "How to set this up" });
    expect(api.listProcedures).not.toHaveBeenCalled();
    expect(api.getProcedure).toHaveBeenCalledWith("connector.mcp.mcp.atlassian-rovo");
  });

  it("keeps one panel open at a time, and makes room for it on the page", async () => {
    view(
      <>
        <ProcedurePanel target="source:confluence" autoOpen={false} />
        <ProcedurePanel target="connector:documents/confluence" label="Set up the connector" autoOpen={false} />
      </>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "How to set this up" }));
    expect(screen.getByTestId("procedure-panel")).toHaveTextContent("Confluence space or page tree");
    expect(document.body.classList.contains("procedure-open")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Set up the connector" }));
    expect(screen.getAllByTestId("procedure-panel")).toHaveLength(1);
    expect(screen.getByTestId("procedure-panel")).toHaveTextContent("Connect Confluence Cloud");
    fireEvent.click(screen.getByTestId("procedure-close"));
    expect(document.body.classList.contains("procedure-open")).toBe(false);
  });

  it("works every time when the browser's storage is blocked", async () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    view(<ProcedurePanel target="connector:documents/sharepoint" />);
    expect(await screen.findByTestId("procedure-panel")).toBeInTheDocument();
    spy.mockRestore();
    set.mockRestore();
  });
});

describe("what picks a preset", () => {
  it("knows Atlassian's Rovo MCP address and nothing else", () => {
    expect(presetOfMcpUrl("https://mcp.atlassian.com/v1/mcp")).toBe("atlassian-rovo");
    expect(presetOfMcpUrl("https://mcp.example.com/mcp")).toBeUndefined();
    expect(presetOfMcpUrl(undefined)).toBeUndefined();
    expect(presetOfMcpUrl("not a url")).toBeUndefined();
  });

  it("reads the host of an agent card address", () => {
    expect(hostOf("https://A2A.Atlassian.com/.well-known/agent.json")).toBe("a2a.atlassian.com");
    expect(hostOf("")).toBeUndefined();
  });
});
