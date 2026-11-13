// SPDX-License-Identifier: Apache-2.0
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import OutpostsPage from "@/app/admin/outposts/page";
import { api, ApiError } from "@/lib/api";
import type { Outpost } from "@/lib/types";

const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: null }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, outposts: vi.fn(), createOutpost: vi.fn() } };
});

const outpost = (changes: Partial<Outpost> = {}): Outpost => ({
  slug: "factory-floor",
  display_name: "Factory floor",
  status: "active",
  capabilities: ["tools"],
  epoch: 0,
  online: true,
  last_seen_at: "2026-11-12T09:59:55Z",
  instances: { pending: 1, active: 2, revoked: 3 },
  endpoints: ["tracker", "warehouse"],
  connectors: ["tracker-at-the-outpost"],
  control_plane: null,
  door_keys: [],
  created_by: "ada@example.com",
  created_at: "2026-11-01T09:00:00Z",
  ...changes,
});

function view() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <OutpostsPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-12T10:00:00Z"));
});
afterEach(() => vi.useRealTimers());

describe("the Outposts page (ADR 0147)", () => {
  it("lists each outpost with its status, its instances by state and whether it is there", async () => {
    vi.mocked(api.outposts).mockResolvedValue({
      items: [
        outpost(),
        outpost({ slug: "cold-store", display_name: "Cold store", status: "paused", online: false, last_seen_at: "2026-11-12T05:00:00Z", instances: { pending: 0, active: 1, revoked: 0 }, endpoints: [], connectors: [] }),
        outpost({ slug: "new-one", display_name: "new-one", online: false, last_seen_at: null, instances: { pending: 0, active: 0, revoked: 0 } }),
      ],
      enabled: true,
      door_signing_key: true,
    });
    view();
    const factory = within(await screen.findByTestId("outpost-factory-floor"));
    expect(api.outposts).toHaveBeenCalledWith("acme");
    expect(factory.getByRole("link", { name: "Factory floor" })).toHaveAttribute("href", "/admin/outposts/factory-floor");
    expect(factory.getByText("Active")).toBeInTheDocument();
    expect(factory.getByText("2 approved")).toBeInTheDocument();
    expect(factory.getByText("1 pending")).toBeInTheDocument();
    expect(factory.getByText("3 revoked")).toBeInTheDocument();
    expect(factory.getByText("Online")).toBeInTheDocument();
    expect(factory.getByText("tracker, warehouse")).toBeInTheDocument();
    expect(factory.getByText("tracker-at-the-outpost")).toBeInTheDocument();

    const cold = within(screen.getByTestId("outpost-cold-store"));
    expect(cold.getByText("Paused")).toBeInTheDocument();
    expect(cold.getByText("Last seen 5 hours ago")).toBeInTheDocument();
    expect(cold.queryByText("Online")).toBeNull();
    expect(within(screen.getByTestId("outpost-new-one")).getByText("Never seen")).toBeInTheDocument();
  });

  it("says there is none yet, and how to start", async () => {
    vi.mocked(api.outposts).mockResolvedValue({ items: [], enabled: true, door_signing_key: true });
    view();
    expect(await screen.findByText("No outpost yet")).toBeInTheDocument();
    expect(screen.queryByTestId("outposts")).toBeNull();
  });

  it("shows the API's refusal to whoever does not manage outposts", async () => {
    vi.mocked(api.outposts).mockRejectedValue(new ApiError(403, { detail: "the outposts of acme are managed by its administrator (`outpost:manage`)" }));
    view();
    expect(await screen.findByRole("alert")).toHaveTextContent("the outposts of acme are managed by its administrator (`outpost:manage`)");
  });

  it("creates an outpost from a slug and a name, and opens its page, where its first instance is enrolled", async () => {
    vi.mocked(api.outposts).mockResolvedValue({ items: [], enabled: true, door_signing_key: true });
    vi.mocked(api.createOutpost).mockResolvedValue(outpost({ slug: "cold-store" }));
    view();
    const create = await screen.findByRole("button", { name: "Create the outpost" });
    expect(create).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Slug"), { target: { value: "Cold Store" } });
    expect(create).toBeDisabled();
    expect(screen.getByText(/not starting with a hyphen/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Slug"), { target: { value: "cold-store" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: " Cold store " } });
    fireEvent.click(create);
    await waitFor(() => expect(api.createOutpost).toHaveBeenCalledWith("acme", { slug: "cold-store", display_name: "Cold store" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/admin/outposts/cold-store"));
  });

  it("sends no name when none is given: the API uses the slug", async () => {
    vi.mocked(api.outposts).mockResolvedValue({ items: [], enabled: true, door_signing_key: true });
    vi.mocked(api.createOutpost).mockResolvedValue(outpost({ slug: "cold-store" }));
    view();
    fireEvent.change(await screen.findByLabelText("Slug"), { target: { value: "cold-store" } });
    fireEvent.click(screen.getByRole("button", { name: "Create the outpost" }));
    await waitFor(() => expect(api.createOutpost).toHaveBeenCalledWith("acme", { slug: "cold-store" }));
  });

  it("a deployment where outposts are off says so, in the API's own words", async () => {
    const words = "outposts are switched off on this deployment (`CHOREGOS_OUTPOSTS`): its operator turns them on";
    vi.mocked(api.outposts).mockResolvedValue({ items: [], enabled: true, door_signing_key: true });
    vi.mocked(api.createOutpost).mockRejectedValue(new ApiError(409, { detail: words, code: "outposts_disabled" }));
    view();
    fireEvent.change(await screen.findByLabelText("Slug"), { target: { value: "cold-store" } });
    fireEvent.click(screen.getByRole("button", { name: "Create the outpost" }));
    const said = await screen.findByTestId("outposts-deployment-refusal");
    expect(said).toHaveTextContent("Outposts are switched off on this deployment");
    // The API's sentence as it is: a name between backticks reads as code, the words are the API's.
    expect(said).toHaveTextContent("outposts are switched off on this deployment (CHOREGOS_OUTPOSTS): its operator turns them on");
    expect(within(said).getByText("CHOREGOS_OUTPOSTS").tagName).toBe("CODE");
    expect(push).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Create the outpost" })).toBeEnabled();
  });

  it("says before anyone tries that outposts are switched off, and offers no creation (S65-24)", async () => {
    vi.mocked(api.outposts).mockResolvedValue({ items: [outpost()], enabled: false, door_signing_key: true });
    view();
    const said = await screen.findByTestId("outposts-deployment-readiness");
    expect(said).toHaveTextContent("Outposts are switched off");
    expect(said).toHaveTextContent("What exists can still be read, revoked and deleted");
    fireEvent.change(screen.getByLabelText("Slug"), { target: { value: "cold-store" } });
    expect(screen.getByRole("button", { name: "Create the outpost" })).toBeDisabled();
    expect(api.createOutpost).not.toHaveBeenCalled();
  });

  it("says before anyone tries that no token can be issued, and still lets an outpost be created (S65-24)", async () => {
    vi.mocked(api.outposts).mockResolvedValue({ items: [], enabled: true, door_signing_key: false });
    view();
    const said = await screen.findByTestId("outposts-deployment-readiness");
    expect(said).toHaveTextContent("No enrolment token can be issued");
    expect(said).toHaveTextContent("no signing key");
    fireEvent.change(screen.getByLabelText("Slug"), { target: { value: "cold-store" } });
    expect(screen.getByRole("button", { name: "Create the outpost" })).toBeEnabled();
  });

  it("says nothing of the deployment when it is ready", async () => {
    vi.mocked(api.outposts).mockResolvedValue({ items: [outpost()], enabled: true, door_signing_key: true });
    view();
    await screen.findByTestId("outpost-factory-floor");
    expect(screen.queryByTestId("outposts-deployment-readiness")).toBeNull();
  });

  it("a slug that is taken is the API's refusal, shown as an error", async () => {
    vi.mocked(api.outposts).mockResolvedValue({ items: [outpost()], enabled: true, door_signing_key: true });
    vi.mocked(api.createOutpost).mockRejectedValue(new ApiError(409, { detail: "`factory-floor` is already an outpost of acme", code: "outpost_exists" }));
    view();
    fireEvent.change(await screen.findByLabelText("Slug"), { target: { value: "factory-floor" } });
    fireEvent.click(screen.getByRole("button", { name: "Create the outpost" }));
    expect(await screen.findByTestId("outposts-refusal")).toHaveTextContent("factory-floor is already an outpost of acme");
    expect(screen.queryByTestId("outposts-deployment-refusal")).toBeNull();
  });
});
