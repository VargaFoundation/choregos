// SPDX-License-Identifier: Apache-2.0
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Suspense } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import OutpostPage from "@/app/admin/outposts/[slug]/page";
import { api, ApiError } from "@/lib/api";
import { shortDate } from "@/lib/format";
import type { Outpost, OutpostCallRecord, OutpostEnrolmentCreated, OutpostInstance } from "@/lib/types";

const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: null }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      outpost: vi.fn(),
      outpostInstances: vi.fn(),
      outpostCalls: vi.fn(),
      pauseOutpost: vi.fn(),
      resumeOutpost: vi.fn(),
      revokeOutpost: vi.fn(),
      deleteOutpost: vi.fn(),
      createOutpostEnrolment: vi.fn(),
      approveOutpostInstance: vi.fn(),
      revokeOutpostInstance: vi.fn(),
    },
  };
});

const KEYS = ["3k9fZti-P3Z9Du9RmNmJm-pNxgUQSHfGN_bx7DmXwNw", "kPu0v5q0mTqH1cQ7m9cN2rY8wXhJ4bS6dF3gA1zL0eE"];
const PRINTED = `SHA256:${"f1Ng3rPr1nt".repeat(3)}0123456789`;
const OTHER = `SHA256:${"0th3rK3y".repeat(5)}abc`;
const PENDING = `${"0192a3b4".repeat(4)}${"aaaa1111".repeat(4)}`;
const APPROVED = `${"0192a3b4".repeat(4)}${"bbbb2222".repeat(4)}`;

const outpost = (changes: Partial<Outpost> = {}): Outpost => ({
  slug: "factory-floor",
  display_name: "Factory floor",
  status: "active",
  capabilities: ["tools"],
  epoch: 1,
  online: true,
  last_seen_at: "2026-11-12T09:59:55Z",
  instances: { pending: 1, active: 1, revoked: 0 },
  endpoints: ["tracker"],
  connectors: [],
  door_keys: KEYS,
  created_by: "ada@example.com",
  created_at: "2026-11-01T09:00:00Z",
  ...changes,
});

const instance = (changes: Partial<OutpostInstance> = {}): OutpostInstance => ({
  id: PENDING,
  status: "pending",
  fingerprint: PRINTED,
  daemon_version: "0.27.0",
  protocol: 1,
  capabilities: ["tools"],
  endpoints: [],
  facts: { platform: "linux/amd64", hostname: "outpost-1.tools.svc" },
  enrolled_at: "2026-11-12T09:56:00Z",
  approved_by: null,
  approved_at: null,
  revoked_by: null,
  revoked_at: null,
  last_seen_at: null,
  online: false,
  in_flight: 0,
  ...changes,
});

const approved = (changes: Partial<OutpostInstance> = {}): OutpostInstance =>
  instance({
    id: APPROVED,
    status: "active",
    fingerprint: OTHER,
    endpoints: ["tracker"],
    approved_by: "ada@example.com",
    approved_at: "2026-11-01T09:10:00Z",
    last_seen_at: "2026-11-12T09:59:55Z",
    online: true,
    in_flight: 2,
    ...changes,
  });

const call = (changes: Partial<OutpostCallRecord> = {}): OutpostCallRecord => ({
  id: "call-1",
  kind: "call_tool",
  state: "answered",
  connector: "tracker-at-the-outpost",
  endpoint: "tracker",
  tool: "search_issues",
  write: false,
  instance_id: APPROVED,
  epoch: 1,
  outcome: "result",
  is_error: false,
  refusal: null,
  arguments_sha256: `a1b2c3d4e5f6${"0".repeat(52)}`,
  result_sha256: `f6e5d4c3b2a1${"9".repeat(52)}`,
  project: "billing-api",
  run_id: "run-1",
  action_id: null,
  created_at: "2026-11-12T09:55:00Z",
  claimed_at: "2026-11-12T09:55:01Z",
  finished_at: "2026-11-12T09:55:02Z",
  ...changes,
});

const page = (items: OutpostCallRecord[], next: string | null = null) => ({ items, meta: { has_more: next !== null, next_cursor: next } });

const issued = (changes: Partial<OutpostEnrolmentCreated> = {}): OutpostEnrolmentCreated => ({
  id: "enrolment-1",
  token: `choe_${"0192a3b4".repeat(4)}${"t0k3N-_".repeat(6)}x`,
  expires_at: "2026-11-12T10:15:00Z",
  control_plane: "https://outposts.example.com",
  door_keys: KEYS,
  ...changes,
});

async function view(slug = "factory-floor") {
  const params = Promise.resolve({ slug });
  let rendered!: ReturnType<typeof render>;
  await act(async () => {
    rendered = render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Suspense fallback={null}>
          <OutpostPage params={params} />
        </Suspense>
      </QueryClientProvider>,
    );
    await params;
  });
  return rendered;
}

function given(state: { outpost?: Outpost; instances?: OutpostInstance[]; calls?: OutpostCallRecord[] } = {}) {
  vi.mocked(api.outpost).mockResolvedValue(state.outpost ?? outpost());
  vi.mocked(api.outpostInstances).mockResolvedValue({ items: state.instances ?? [instance(), approved()] });
  vi.mocked(api.outpostCalls).mockResolvedValue(page(state.calls ?? []));
}

const pendingBlock = async () => within(await screen.findByTestId(`pending-instance-${PENDING}`));
const approve = (block: ReturnType<typeof within>) => block.getByRole("button", { name: "Approve the instance" });

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-12T10:00:00Z"));
});
afterEach(() => vi.useRealTimers());

describe("one outpost's page: where it stands", () => {
  it("reads the outpost of the address, in the session's organisation", async () => {
    given();
    await view();
    const summary = within(await screen.findByTestId("outpost-summary"));
    expect(api.outpost).toHaveBeenCalledWith("acme", "factory-floor");
    expect(api.outpostInstances).toHaveBeenCalledWith("acme", "factory-floor");
    expect(summary.getByRole("heading", { name: "Factory floor" })).toBeInTheDocument();
    expect(summary.getByText("Active")).toBeInTheDocument();
    expect(summary.getByTestId("outpost-presence")).toHaveTextContent("Online");
    expect(summary.getByText("1 approved, 1 pending, 0 revoked")).toBeInTheDocument();
    expect(screen.queryByTestId("outpost-paused")).toBeNull();
    expect(screen.queryByTestId("outpost-revoked")).toBeNull();
  });

  it("an outpost that is not there says when it was last seen", async () => {
    given({ outpost: outpost({ online: false, last_seen_at: "2026-11-12T07:00:00Z" }) });
    await view();
    expect(await screen.findByTestId("outpost-presence")).toHaveTextContent("Last seen 3 hours ago");
  });

  it("a paused outpost says so, and is resumed from there", async () => {
    given({ outpost: outpost({ status: "paused" }) });
    vi.mocked(api.resumeOutpost).mockResolvedValue(outpost());
    await view();
    const said = await screen.findByTestId("outpost-paused");
    expect(said).toHaveTextContent("This outpost is paused");
    expect(said).toHaveTextContent("Nothing more is handed to it");
    expect(within(screen.getByTestId("outpost-summary")).getByText("Paused")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    await waitFor(() => expect(api.resumeOutpost).toHaveBeenCalledWith("acme", "factory-floor"));
  });

  it("pausing says what it does before it is done", async () => {
    given();
    vi.mocked(api.pauseOutpost).mockResolvedValue(outpost({ status: "paused" }));
    await view();
    fireEvent.click(await screen.findByRole("button", { name: "Pause" }));
    expect(api.pauseOutpost).not.toHaveBeenCalled();
    expect(screen.getByTestId("confirmation")).toHaveTextContent("a call routed to it is refused until it is resumed");
    fireEvent.click(screen.getByRole("button", { name: "Pause the outpost" }));
    await waitFor(() => expect(api.pauseOutpost).toHaveBeenCalledWith("acme", "factory-floor"));
    expect(screen.queryByRole("button", { name: "Resume" })).toBeNull();
  });

  it("a refused resumption shows the API's words", async () => {
    given({ outpost: outpost({ status: "paused" }) });
    vi.mocked(api.resumeOutpost).mockRejectedValue(new ApiError(409, { detail: "`factory-floor` is revoked: that is final", code: "outpost_revoked" }));
    await view();
    fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
    expect(await screen.findByTestId("outposts-refusal")).toHaveTextContent("factory-floor is revoked: that is final");
  });

  it("an outpost that does not exist is the API's answer", async () => {
    vi.mocked(api.outpost).mockRejectedValue(new ApiError(404, { detail: "Outpost `nowhere` does not exist" }));
    vi.mocked(api.outpostInstances).mockRejectedValue(new ApiError(404, { detail: "Outpost `nowhere` does not exist" }));
    await view("nowhere");
    expect(await screen.findByRole("alert")).toHaveTextContent("Outpost `nowhere` does not exist");
    expect(screen.queryByTestId("outpost-instances")).toBeNull();
  });
});

describe("a pending instance is approved against its fingerprint (ADR 0147 §4)", () => {
  it("shows its fingerprint, and cannot be approved before the person confirmed it is the one the daemon printed", async () => {
    given();
    vi.mocked(api.approveOutpostInstance).mockResolvedValue(approved({ id: PENDING, fingerprint: PRINTED }));
    await view();
    const block = await pendingBlock();
    expect(block.getByTestId("instance-fingerprint")).toHaveTextContent(PRINTED);
    expect(block.getByText("hostname outpost-1.tools.svc, platform linux/amd64")).toBeInTheDocument();
    expect(approve(block)).toBeDisabled();
    fireEvent.click(approve(block));
    expect(api.approveOutpostInstance).not.toHaveBeenCalled();

    const confirmation = block.getByRole("checkbox", { name: /this is the fingerprint choregos-outpost enrol printed/ });
    expect(confirmation).not.toBeChecked();
    fireEvent.click(confirmation);
    expect(approve(block)).toBeEnabled();
    // Taking the confirmation back closes the gesture again.
    fireEvent.click(confirmation);
    expect(approve(block)).toBeDisabled();
    fireEvent.click(approve(block));
    expect(api.approveOutpostInstance).not.toHaveBeenCalled();

    fireEvent.click(confirmation);
    fireEvent.click(approve(block));
    await waitFor(() => expect(api.approveOutpostInstance).toHaveBeenCalledTimes(1));
    // The request carries the fingerprint the person confirmed, for this instance of this outpost.
    expect(api.approveOutpostInstance).toHaveBeenCalledWith("acme", "factory-floor", PENDING, { fingerprint: PRINTED });
    expect(api.outpostInstances).toHaveBeenCalledTimes(2);
  });

  it("a confirmation holds for the fingerprint that was on screen, not for another one", async () => {
    given();
    const rendered = await view();
    const block = await pendingBlock();
    fireEvent.click(block.getByRole("checkbox"));
    expect(approve(block)).toBeEnabled();
    // The same instance read again with another fingerprint: what was confirmed is not what is shown.
    vi.mocked(api.outpostInstances).mockResolvedValue({ items: [instance({ fingerprint: OTHER })] });
    fireEvent.click(within(screen.getByTestId(`instance-${APPROVED}`)).getByRole("button", { name: "Revoke" }));
    vi.mocked(api.revokeOutpostInstance).mockResolvedValue(approved({ status: "revoked" }));
    fireEvent.click(screen.getByRole("button", { name: "Revoke the instance" }));
    await waitFor(() => expect(block.getByTestId("instance-fingerprint")).toHaveTextContent(OTHER));
    expect(block.getByRole("checkbox")).not.toBeChecked();
    expect(approve(block)).toBeDisabled();
    rendered.unmount();
  });

  it("says that approving needs a recent sign-in, and shows the API's refusal as it is", async () => {
    given();
    const words = "the fingerprint sent is not the one this instance enrolled with: compare it with what `choregos-outpost enrol` printed, and approve nothing that differs";
    vi.mocked(api.approveOutpostInstance).mockRejectedValue(new ApiError(409, { detail: words, code: "fingerprint_mismatch" }));
    await view();
    const block = await pendingBlock();
    expect(approve(block)).toHaveAccessibleDescription(/Approving needs a recent sign-in/);
    fireEvent.click(block.getByRole("checkbox"));
    fireEvent.click(approve(block));
    const refusal = await block.findByTestId("outposts-refusal");
    expect(refusal).toHaveTextContent("the fingerprint sent is not the one this instance enrolled with");
    expect(refusal).toHaveTextContent("approve nothing that differs");
    await waitFor(() => expect(approve(block)).toBeEnabled());
  });

  it("a session that is too old is told so while the sign-in opens again, and nothing is approved", async () => {
    given();
    const stepUp = new ApiError(401, {
      detail: "this decision needs an authentication less than 10 min old; yours is 12 min old",
      errors: [{ error: "step_up_required" }],
    });
    vi.mocked(api.approveOutpostInstance).mockRejectedValue(stepUp);
    await view();
    const block = await pendingBlock();
    fireEvent.click(block.getByRole("checkbox"));
    fireEvent.click(approve(block));
    expect(await block.findByTestId("outposts-refusal")).toHaveTextContent("less than 10 min old; yours is 12 min old");
    expect(api.approveOutpostInstance).toHaveBeenCalledTimes(1);
    expect(block.getByText("Pending")).toBeInTheDocument();
  });

  it("a pending instance is revoked after a confirmation that says it is final", async () => {
    given();
    vi.mocked(api.revokeOutpostInstance).mockResolvedValue(instance({ status: "revoked" }));
    await view();
    const block = await pendingBlock();
    fireEvent.click(block.getByRole("button", { name: "Revoke" }));
    expect(api.revokeOutpostInstance).not.toHaveBeenCalled();
    expect(block.getByTestId("confirmation")).toHaveTextContent("This is final");
    expect(block.getByTestId("confirmation")).toHaveTextContent("its key does not come back");
    fireEvent.click(block.getByRole("button", { name: "Revoke the instance" }));
    await waitFor(() => expect(api.revokeOutpostInstance).toHaveBeenCalledWith("acme", "factory-floor", PENDING));
  });

  it("no instance of a revoked outpost is approved, and none is enrolled", async () => {
    given({ outpost: outpost({ status: "revoked" }) });
    await view();
    expect(await screen.findByTestId("outpost-revoked")).toHaveTextContent("That is final");
    const block = await pendingBlock();
    expect(block.queryByRole("button", { name: "Approve the instance" })).toBeNull();
    expect(block.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByTestId("outpost-enrol")).toBeNull();
    expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Resume" })).toBeNull();
  });
});

describe("the instances a person already decided on", () => {
  it("show their state, their fingerprint, what they announce and whether they are there", async () => {
    given({ instances: [approved(), instance({ id: `${"0192a3b4".repeat(4)}${"cccc3333".repeat(4)}`, status: "revoked", revoked_by: "ada@example.com", revoked_at: "2026-11-10T08:00:00Z", last_seen_at: "2026-11-09T08:00:00Z" })] });
    await view();
    const row = within(await screen.findByTestId(`instance-${APPROVED}`));
    expect(row.getByText("Approved")).toBeInTheDocument();
    expect(row.getByText(OTHER)).toBeInTheDocument();
    expect(row.getByText("Online")).toBeInTheDocument();
    expect(row.getByText("tracker")).toBeInTheDocument();
    expect(row.getByText("2")).toBeInTheDocument();
    expect(row.getByText("ada@example.com")).toBeInTheDocument();
    const gone = within(screen.getByTestId(`instance-${"0192a3b4".repeat(4)}${"cccc3333".repeat(4)}`));
    expect(gone.getByText("Revoked")).toBeInTheDocument();
    expect(gone.queryByRole("button", { name: "Revoke" })).toBeNull();
    expect(screen.queryByRole("list", { name: "Instances waiting for approval" })).toBeNull();
  });

  it("an approved instance is revoked after a confirmation that says it is final", async () => {
    given({ instances: [approved()] });
    vi.mocked(api.revokeOutpostInstance).mockResolvedValue(approved({ status: "revoked" }));
    await view();
    const row = within(await screen.findByTestId(`instance-${APPROVED}`));
    fireEvent.click(row.getByRole("button", { name: "Revoke" }));
    expect(row.getByTestId("confirmation")).toHaveTextContent("This is final");
    expect(api.revokeOutpostInstance).not.toHaveBeenCalled();
    fireEvent.click(row.getByRole("button", { name: "Revoke the instance" }));
    await waitFor(() => expect(api.revokeOutpostInstance).toHaveBeenCalledWith("acme", "factory-floor", APPROVED));
  });

  it("an outpost without an instance says how one comes", async () => {
    given({ outpost: outpost({ instances: { pending: 0, active: 0, revoked: 0 } }), instances: [] });
    await view();
    expect(await screen.findByText("No instance yet")).toBeInTheDocument();
  });
});

describe("revoking and deleting an outpost", () => {
  it("a revocation is said to be final before it is done", async () => {
    given();
    vi.mocked(api.revokeOutpost).mockResolvedValue(outpost({ status: "revoked" }));
    await view();
    const summary = within(await screen.findByTestId("outpost-summary"));
    fireEvent.click(summary.getByRole("button", { name: "Revoke" }));
    expect(api.revokeOutpost).not.toHaveBeenCalled();
    const question = summary.getByTestId("confirmation");
    expect(question).toHaveTextContent("Revoke factory-floor? This is final.");
    expect(question).toHaveTextContent("Every instance of it is revoked");
    fireEvent.click(within(question).getByRole("button", { name: "Cancel" }));
    expect(api.revokeOutpost).not.toHaveBeenCalled();
    fireEvent.click(summary.getByRole("button", { name: "Revoke" }));
    fireEvent.click(summary.getByRole("button", { name: "Revoke the outpost" }));
    await waitFor(() => expect(api.revokeOutpost).toHaveBeenCalledWith("acme", "factory-floor"));
  });

  it.each([
    ["outpost_not_revoked", "`factory-floor` is active: revoke it first", [], "factory-floor is active: revoke it first"],
    [
      "outpost_in_use",
      "`factory-floor` still has connectors that route to it: route them elsewhere or delete them",
      ["tracker-at-the-outpost", "warehouse-at-the-outpost"],
      "factory-floor still has connectors that route to it: route them elsewhere or delete them (tracker-at-the-outpost, warehouse-at-the-outpost)",
    ],
  ])("a deletion refused with %s shows the API's refusal as it is", async (code, detail, connectors, shown) => {
    given();
    vi.mocked(api.deleteOutpost).mockRejectedValue(new ApiError(409, { detail, code, connectors }));
    await view();
    const summary = within(await screen.findByTestId("outpost-summary"));
    fireEvent.click(summary.getByRole("button", { name: "Delete" }));
    expect(summary.getByTestId("confirmation")).toHaveTextContent("none of it comes back");
    fireEvent.click(summary.getByRole("button", { name: "Delete the outpost" }));
    expect(await summary.findByRole("alert")).toHaveTextContent(shown);
    expect(push).not.toHaveBeenCalled();
  });

  it("a deleted outpost leads back to the list", async () => {
    given({ outpost: outpost({ status: "revoked" }), instances: [] });
    vi.mocked(api.deleteOutpost).mockResolvedValue(undefined);
    await view();
    const summary = within(await screen.findByTestId("outpost-summary"));
    fireEvent.click(summary.getByRole("button", { name: "Delete" }));
    fireEvent.click(summary.getByRole("button", { name: "Delete the outpost" }));
    await waitFor(() => expect(api.deleteOutpost).toHaveBeenCalledWith("acme", "factory-floor"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/admin/outposts"));
  });
});

describe("enrolling an instance (ADR 0147 §4, ADR 0148 §13)", () => {
  it("creates a token and shows, once, the token, its expiry, the door's origin, the keys to pin and the policy file's lines", async () => {
    given({ instances: [] });
    const created = issued();
    vi.mocked(api.createOutpostEnrolment).mockResolvedValue(created);
    await view();
    const card = within(await screen.findByTestId("outpost-enrol"));
    expect(card.queryByTestId("outpost-enrolment")).toBeNull();
    fireEvent.click(card.getByRole("button", { name: "Create an enrolment token" }));
    const shown = within(await card.findByTestId("outpost-enrolment"));
    expect(api.createOutpostEnrolment).toHaveBeenCalledWith("acme", "factory-floor");
    expect(shown.getByTestId("outpost-enrolment-token")).toHaveTextContent(created.token);
    expect(shown.getByText(/the token will not be shown again/)).toBeInTheDocument();
    expect(shown.getByTestId("outpost-enrolment-expiry")).toHaveTextContent(shortDate(created.expires_at));
    expect(shown.getByTestId("outpost-enrolment-expiry")).toHaveTextContent("in 15 minutes");
    expect(shown.getByTestId("outpost-enrolment-expiry")).toHaveTextContent("it works once");
    expect(shown.getByTestId("outpost-enrolment-origin")).toHaveTextContent("https://outposts.example.com");
    const keys = within(shown.getByTestId("outpost-enrolment-keys")).getAllByRole("listitem");
    expect(keys.map((key) => key.textContent)).toEqual([`${KEYS[0]} signs now`, `${KEYS[1]} announced for a rotation`]);
    expect(shown.getByTestId("outpost-policy-lines").textContent).toBe(
      `control_plane: https://outposts.example.com\ndoor_keys: ["${KEYS[0]}", "${KEYS[1]}"]`,
    );
    expect(shown.getByTestId("outpost-enrol-command").textContent).toBe(`choregos-outpost enrol --token ${created.token}`);
    // One token at a time: a second one is asked for once this one is put away.
    expect(card.queryByRole("button", { name: "Create an enrolment token" })).toBeNull();

    fireEvent.click(shown.getByRole("button", { name: "Done, hide the token" }));
    expect(card.queryByTestId("outpost-enrolment")).toBeNull();
    expect(screen.queryByText(created.token)).toBeNull();
    expect(card.getByRole("button", { name: "Create an enrolment token" })).toBeEnabled();
  });

  it("a deployment that declares no origin for its door says so, and writes none", async () => {
    given({ instances: [] });
    vi.mocked(api.createOutpostEnrolment).mockResolvedValue(issued({ control_plane: null, door_keys: KEYS.slice(0, 1) }));
    await view();
    fireEvent.click(await screen.findByRole("button", { name: "Create an enrolment token" }));
    const shown = within(await screen.findByTestId("outpost-enrolment"));
    expect(shown.getByTestId("outpost-enrolment-origin")).toHaveTextContent("This deployment declares none");
    const lines = shown.getByTestId("outpost-policy-lines").textContent!.split("\n");
    expect(lines[0]).toMatch(/^# control_plane: /);
    expect(lines[1]).toBe(`door_keys: ["${KEYS[0]}"]`);
  });

  it("a door without a signing key says so, in the API's own words", async () => {
    given({ outpost: outpost({ door_keys: [] }), instances: [] });
    const words = "the outposts' door has no signing key on this deployment (`CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY`): there is no key to pin, so no instance is enrolled";
    vi.mocked(api.createOutpostEnrolment).mockRejectedValue(new ApiError(409, { detail: words, code: "door_key_missing" }));
    await view();
    fireEvent.click(await screen.findByRole("button", { name: "Create an enrolment token" }));
    const said = await screen.findByTestId("outposts-deployment-refusal");
    expect(said).toHaveTextContent("The outposts' door has no signing key on this deployment");
    expect(said).toHaveTextContent("there is no key to pin, so no instance is enrolled");
    expect(within(said).getByText("CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY").tagName).toBe("CODE");
    expect(screen.queryByTestId("outpost-enrolment")).toBeNull();
    // The page already said there is nothing to pin.
    expect(screen.getByTestId("outpost-door-keys")).toHaveTextContent("There is no key to pin yet");
    expect(screen.queryByTestId("outpost-door-keys-line")).toBeNull();
  });

  it("a deployment where outposts are off says that, and not the other", async () => {
    given({ instances: [] });
    vi.mocked(api.createOutpostEnrolment).mockRejectedValue(
      new ApiError(409, { detail: "outposts are switched off on this deployment (`CHOREGOS_OUTPOSTS`): its operator turns them on", code: "outposts_disabled" }),
    );
    await view();
    fireEvent.click(await screen.findByRole("button", { name: "Create an enrolment token" }));
    const said = await screen.findByTestId("outposts-deployment-refusal");
    expect(said).toHaveTextContent("Outposts are switched off on this deployment");
    expect(said).not.toHaveTextContent("signing key");
  });

  it("the keys to pin are shown again on the outpost's page, as the policy file's line", async () => {
    given();
    await view();
    expect((await screen.findByTestId("outpost-door-keys-line")).textContent).toBe(`door_keys: ["${KEYS[0]}", "${KEYS[1]}"]`);
  });
});

describe("the recent calls routed to an outpost (ADR 0148 §11)", () => {
  const refused = call({ id: "call-2", state: "refused", tool: "create_issue", write: true, outcome: null, is_error: null, refusal: "write_denied", result_sha256: null });
  const doubt = call({ id: "call-3", state: "in_doubt", tool: "close_issue", write: true, outcome: null, is_error: null, result_sha256: null });
  const listing = call({ id: "call-4", kind: "list_tools", tool: null, connector: null, arguments_sha256: null, project: null });
  const failed = call({ id: "call-5", is_error: true });

  it("shows each call's state in words, the code of a refusal, and digests only", async () => {
    given({ calls: [call(), refused, doubt, listing, failed] });
    await view();
    const answered = within(await screen.findByTestId("call-call-1"));
    expect(api.outpostCalls).toHaveBeenCalledWith("acme", "factory-floor", { state: undefined, cursor: undefined, limit: 50 });
    expect(answered.getByText("search_issues")).toBeInTheDocument();
    expect(answered.getByText("Answered")).toBeInTheDocument();
    expect(answered.getByText("Result")).toBeInTheDocument();
    expect(answered.getByText("a1b2c3d4e5f6")).toHaveAttribute("title", `a1b2c3d4e5f6${"0".repeat(52)}`);
    expect(answered.getByText("f6e5d4c3b2a1")).toHaveAttribute("title", `f6e5d4c3b2a1${"9".repeat(52)}`);
    expect(answered.queryByText("Write")).toBeNull();

    const write = within(screen.getByTestId("call-call-2"));
    expect(write.getByText("Refused")).toBeInTheDocument();
    expect(write.getByText("write_denied").tagName).toBe("CODE");
    expect(write.getByText("Write")).toBeInTheDocument();
    expect(within(screen.getByTestId("call-call-3")).getByText("In doubt")).toBeInTheDocument();
    expect(within(screen.getByTestId("call-call-4")).getByText("Tool listing")).toBeInTheDocument();
    expect(within(screen.getByTestId("call-call-5")).getByText(/the server answered an error/)).toBeInTheDocument();
    expect(screen.getByTestId("outpost-calls")).toHaveTextContent("the platform keeps no arguments and no result of a call");
  });

  it("filters by state, and reads older calls from where the page stopped", async () => {
    vi.mocked(api.outpost).mockResolvedValue(outpost());
    vi.mocked(api.outpostInstances).mockResolvedValue({ items: [] });
    vi.mocked(api.outpostCalls).mockImplementation(async (_org, _slug, params = {}) => {
      if (params.state === "refused") return page([refused]);
      if (params.cursor === "c-2") return page([listing]);
      return page([call()], "c-2");
    });
    await view();
    const card = within(await screen.findByTestId("outpost-calls"));
    await card.findByTestId("call-call-1");
    fireEvent.click(card.getByRole("button", { name: "Older calls" }));
    await card.findByTestId("call-call-4");
    expect(api.outpostCalls).toHaveBeenLastCalledWith("acme", "factory-floor", { state: undefined, cursor: "c-2", limit: 50 });
    expect(card.getByTestId("call-call-1")).toBeInTheDocument();
    expect(card.queryByRole("button", { name: "Older calls" })).toBeNull();

    fireEvent.change(card.getByLabelText("State"), { target: { value: "refused" } });
    await card.findByTestId("call-call-2");
    expect(api.outpostCalls).toHaveBeenLastCalledWith("acme", "factory-floor", { state: "refused", cursor: undefined, limit: 50 });
    expect(card.queryByTestId("call-call-1")).toBeNull();
  });

  it("says so when no call was routed, or none is in the state asked for", async () => {
    given();
    await view();
    const card = within(await screen.findByTestId("outpost-calls"));
    expect(await card.findByText("No call was routed to this outpost yet.")).toBeInTheDocument();
    fireEvent.change(card.getByLabelText("State"), { target: { value: "timed_out" } });
    expect(await card.findByText("No call in this state.")).toBeInTheDocument();
  });

  it("a read that fails shows the API's answer", async () => {
    given();
    vi.mocked(api.outpostCalls).mockRejectedValue(new ApiError(403, { detail: "the outposts of acme are managed by its administrator (`outpost:manage`)" }));
    await view();
    const card = within(await screen.findByTestId("outpost-calls"));
    expect(await card.findByRole("alert")).toHaveTextContent("managed by its administrator");
  });
});
