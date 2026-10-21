import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Suspense } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import KnowledgeBasePage from "@/app/knowledge/[slug]/page";
import { api } from "@/lib/api";
import { accessLabel, grantKey, grantName, grantsBody, permissionsOf, roleHint } from "@/lib/knowledge";
import type { KnowledgeBase, KnowledgeGrant } from "@/lib/types";

vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "varga" }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      knowledgeBase: vi.fn(),
      members: vi.fn(),
      projects: vi.fn(),
      putKnowledgeGrants: vi.fn(),
      updateKnowledgeBase: vi.fn(),
      distillKnowledgeBase: vi.fn(),
    },
  };
});

const ALL = { edit: true, distill: true, share: true, archive: true, widen: true };
const NOTHING = { edit: false, distill: false, share: false, archive: false, widen: false };

const base: KnowledgeBase = {
  slug: "architecture",
  name: "Architecture framework",
  scope: "projects",
  projects: ["billing-api"],
  groups: [],
  serving_mode: "files",
  status: "active",
  document_count: 2,
  documents_by_status: { ready: 1, failed: 1 },
  documents: [
    { id: "d1", filename: "zoning.pptx", title: "Network zoning principles", mime: "x", size_bytes: 29000, digest: "sha256:a", source: "upload", status: "ready" },
    { id: "d2", filename: "scan.pdf", title: "scan.pdf", mime: "application/pdf", size_bytes: 4000, digest: "sha256:b", source: "upload", status: "failed", error: "no text" },
  ],
  sources: [],
  restricted: false,
  can: ALL,
  my_role: "owner",
  grants: [{ subject_type: "group", subject: "architects", display: "architects", role: "editor" }],
};

function show(value: KnowledgeBase) {
  vi.mocked(api.knowledgeBase).mockResolvedValue(value);
  return act(async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Suspense fallback="…">
          <KnowledgeBasePage params={Promise.resolve({ slug: "architecture" })} />
        </Suspense>
      </QueryClientProvider>,
    );
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.members).mockResolvedValue([
    { user_id: "u-ana", email: "ana@varga.dev", org: "varga", role: "developer" },
    { user_id: "u-ana", email: "ana@varga.dev", org: "varga", project_slug: "billing-api", role: "viewer" },
    { user_id: "u-bo", email: "bo@varga.dev", org: "varga", role: "viewer" },
  ] as never);
  vi.mocked(api.projects).mockResolvedValue({ items: [{ slug: "checkout-web", name: "Checkout web" }] } as never);
});

describe("what the access words say", () => {
  it("takes `can` from the server, and allows what an older server does not describe", () => {
    expect(permissionsOf({ can: NOTHING }).edit).toBe(false);
    expect(permissionsOf({}).share).toBe(true);
  });

  it("says who reads a base, and what a role lets one do", () => {
    expect(accessLabel({ restricted: true, scope: "org", projects: [], groups: [] })).toMatch(/through a grant.*still served/);
    expect(accessLabel({ restricted: false, scope: "org", projects: [], groups: [] })).toBe("readable by whole organisation");
    expect(roleHint("editor")).toMatch(/distils/);
    expect(roleHint(null)).toMatch(/reach/);
  });

  it("names a grant by its display and sends only what the server reads", () => {
    const grant: KnowledgeGrant = { subject_type: "user", subject: "u1", display: "Ana", role: "viewer", pending: true, created_by: "x" };
    expect(grantName(grant)).toBe("Ana");
    expect(grantName({ ...grant, display: null })).toBe("u1");
    expect(grantKey(grant)).toBe("user:u1");
    expect(grantsBody([grant])).toEqual([{ subject_type: "user", subject: "u1", role: "viewer" }]);
  });
});

describe("the access card (S42-11)", () => {
  it("an owner edits the grants: change a role, remove one, add a member, save the whole list", async () => {
    vi.mocked(api.putKnowledgeGrants).mockResolvedValue({ grants: [] });
    await show(base);
    const table = await screen.findByTestId("knowledge-grants");
    expect(table).toHaveTextContent("architects");
    fireEvent.change(screen.getByLabelText("Role of architects"), { target: { value: "viewer" } });
    fireEvent.change(screen.getByLabelText("Grant to"), { target: { value: "user" } });
    await waitFor(() => expect(within(screen.getByLabelText("Member")).getAllByRole("option")).toHaveLength(3));
    fireEvent.change(screen.getByLabelText("Member"), { target: { value: "u-bo" } });
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "owner" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    fireEvent.click(screen.getByRole("button", { name: "Save access" }));
    await waitFor(() => expect(api.putKnowledgeGrants).toHaveBeenCalled());
    expect(vi.mocked(api.putKnowledgeGrants).mock.calls[0]).toEqual([
      "varga",
      "architecture",
      [
        { subject_type: "group", subject: "architects", role: "viewer" },
        { subject_type: "user", subject: "u-bo", role: "owner" },
      ],
    ]);
    expect(await screen.findByText("Access saved")).toBeInTheDocument();
  });

  it("lists each member once, adds an e-mail lower-cased and a project by name, and removes a row", async () => {
    await show(base);
    await screen.findByTestId("knowledge-grants");
    fireEvent.change(screen.getByLabelText("Grant to"), { target: { value: "user" } });
    await waitFor(() => expect(screen.getByLabelText("Member")).toBeInTheDocument());
    await waitFor(() => expect(within(screen.getByLabelText("Member")).getAllByRole("option").map((o) => o.textContent)).toEqual(["Choose a member", "ana@varga.dev", "bo@varga.dev"]));
    fireEvent.change(screen.getByLabelText("Grant to"), { target: { value: "email" } });
    fireEvent.change(screen.getByLabelText("E-mail"), { target: { value: "Later@Varga.dev" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(screen.getByTestId("knowledge-grants")).toHaveTextContent("later@varga.dev");
    fireEvent.change(screen.getByLabelText("Grant to"), { target: { value: "project" } });
    await waitFor(() => expect(within(screen.getByLabelText("Project")).getByRole("option", { name: "Checkout web" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Remove architects" }));
    expect(screen.getByTestId("knowledge-grants")).not.toHaveTextContent("architects");
  });

  it("a refused save says why and keeps the draft", async () => {
    vi.mocked(api.putKnowledgeGrants).mockRejectedValue(new Error("unknown member"));
    await show(base);
    fireEvent.change(await screen.findByLabelText("Role of architects"), { target: { value: "owner" } });
    fireEvent.click(screen.getByRole("button", { name: "Save access" }));
    expect(await screen.findByText("unknown member")).toBeInTheDocument();
    expect(screen.getByLabelText("Role of architects")).toHaveValue("owner");
  });

  it("an e-mail with no member yet shows as waiting", async () => {
    await show({ ...base, grants: [{ subject_type: "user", subject: "later@varga.dev", display: "later@varga.dev", role: "editor", pending: true }] });
    expect(await screen.findByTestId("knowledge-grants")).toHaveTextContent("waiting for sign-in");
  });

  it("only an administrator restricts the base; a project owner reads why not", async () => {
    vi.mocked(api.updateKnowledgeBase).mockResolvedValue({ ...base, restricted: true });
    await show(base);
    fireEvent.click(await screen.findByRole("checkbox", { name: /Restricted/ }));
    await waitFor(() => expect(api.updateKnowledgeBase).toHaveBeenCalledWith("varga", "architecture", { restricted: true }));
  });

  it("a project owner cannot widen: no restriction switch, and the owning project is named", async () => {
    await show({ ...base, owner_project: "billing-api", can: { ...ALL, widen: false } });
    const card = await screen.findByTestId("knowledge-access");
    expect(within(card).queryByRole("checkbox")).toBeNull();
    expect(card).toHaveTextContent("Only an organisation administrator changes who a base is served to");
    expect(card).toHaveTextContent("owned by project billing-api");
  });

  it("an editor sees no grants table, only its role", async () => {
    await show({ ...base, can: { ...ALL, share: false, widen: false, archive: false }, my_role: "editor", grants: null });
    const card = await screen.findByTestId("knowledge-access");
    expect(card).toHaveTextContent("you: editor");
    expect(screen.queryByTestId("knowledge-grants")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save access" })).toBeNull();
    expect(screen.getByRole("button", { name: "Distill" })).toBeEnabled();
    expect(screen.getByLabelText("Documents to upload")).toBeInTheDocument();
  });

  it("a reader changes nothing: gestures disabled with the server's sentence, no upload, no source form", async () => {
    const reason = "You read this knowledge base through a viewer grant: editors change it.";
    await show({ ...base, can: NOTHING, my_role: "viewer", grants: null, cannot_reason: reason });
    await screen.findByTestId("knowledge-access");
    const distill = screen.getByRole("button", { name: "Distill" });
    expect(distill).toBeDisabled();
    expect(distill).toHaveAttribute("title", reason);
    for (const del of screen.getAllByRole("button", { name: "Delete" })) expect(del).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Convert again" })).toBeNull();
    expect(screen.queryByLabelText("Documents to upload")).toBeNull();
    expect(screen.getByTestId("knowledge-read-only")).toHaveTextContent(reason);
    expect(screen.queryByRole("button", { name: "add source" })).toBeNull();
    fireEvent.click(distill);
    expect(api.distillKnowledgeBase).not.toHaveBeenCalled();
  });
});
