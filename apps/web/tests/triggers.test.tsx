import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Triggers } from "@/components/project-settings/triggers";
import { api } from "@/lib/api";
import type { Trigger } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      triggers: vi.fn(),
      rotateTriggerToken: vi.fn(),
      fireTrigger: vi.fn(),
      project: vi.fn(),
      updateProject: vi.fn(),
    },
  };
});

const trigger = (name: string, extra: Partial<Trigger> = {}): Trigger => ({
  name,
  url: `https://choregos.example/api/v1/webhooks/triggers/acme/desk/${name}`,
  title: "{/summary}",
  workflow: null,
  key: null,
  labels: [],
  fields: {},
  when: {},
  auth: "token",
  has_token: false,
  token_created_at: null,
  ...extra,
});

const project = {
  id: "p-1",
  slug: "desk",
  name: "Desk",
  config_revision: "r-1",
  config: { slug: "desk", org: "acme", notify: { emails: ["ops@acme.test"] }, triggers: [{ name: "incident", title: "{/number}" }] },
};

const view = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Triggers slug="desk" />
    </QueryClientProvider>,
  );

describe("a project's HTTP triggers (S51-02)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.project).mockResolvedValue(project as never);
  });

  it("lists each trigger with its URL, its workflow or the routing, and whether it has a token", async () => {
    vi.mocked(api.triggers).mockResolvedValue([
      trigger("incident", { workflow: "incident", has_token: true }),
      trigger("signed", { auth: "signature" }),
    ]);
    view();

    const incident = await screen.findByTestId("trigger-incident");
    expect(incident).toHaveTextContent("workflow incident");
    expect(incident).toHaveTextContent("token set");
    expect(within(incident).getByTestId("trigger-url-incident")).toHaveTextContent(
      "POST https://choregos.example/api/v1/webhooks/triggers/acme/desk/incident",
    );
    const signed = screen.getByTestId("trigger-signed");
    expect(signed).toHaveTextContent("the project's routing");
    expect(signed).toHaveTextContent("no token yet");
    expect(signed).toHaveTextContent("signed");
  });

  it("an answer that is not a list shows no trigger instead of breaking the settings page", async () => {
    vi.mocked(api.triggers).mockResolvedValue({} as never);
    view();

    expect(await screen.findByTestId("triggers-none")).toHaveTextContent("No trigger declared.");
  });

  it("shows a minted token once, with the signing secret of a signed trigger", async () => {
    vi.mocked(api.triggers).mockResolvedValue([trigger("signed", { auth: "signature" })]);
    vi.mocked(api.rotateTriggerToken).mockResolvedValue({
      name: "signed",
      url: "u",
      token: "chtrg_abc",
      signing_secret: "chsig_def",
    });
    view();

    fireEvent.click(await screen.findByRole("button", { name: "Mint the token" }));

    const shown = await screen.findByTestId("trigger-token-signed");
    expect(shown).toHaveTextContent("chtrg_abc");
    expect(shown).toHaveTextContent("chsig_def");
    expect(api.rotateTriggerToken).toHaveBeenCalledWith("desk", "signed");
  });

  it("fires a trigger by hand with a JSON object, and refuses anything else before calling", async () => {
    vi.mocked(api.triggers).mockResolvedValue([trigger("incident", { workflow: "incident" })]);
    vi.mocked(api.fireTrigger).mockResolvedValue({
      accepted: true,
      duplicate: false,
      outcome: "created_work_item",
      work_item_id: "wi-1",
      work_item_key: "DESK-incident.42",
      workflow: "incident",
    });
    view();

    const box = await screen.findByLabelText("Fire by hand with a payload");
    fireEvent.change(box, { target: { value: "[1, 2]" } });
    fireEvent.click(screen.getByRole("button", { name: "Fire" }));
    expect(await screen.findByText("The payload must be a JSON object.")).toBeInTheDocument();
    expect(api.fireTrigger).not.toHaveBeenCalled();

    fireEvent.change(box, { target: { value: '{"sys_id": "42"}' } });
    fireEvent.click(screen.getByRole("button", { name: "Fire" }));

    const fired = await screen.findByTestId("trigger-fired-incident");
    expect(fired).toHaveTextContent("started a new work item — DESK-incident.42 in incident");
    expect(api.fireTrigger).toHaveBeenCalledWith("desk", "incident", { sys_id: "42" });
  });
});

describe("editing the triggers and the start rules (S51-02)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.triggers).mockResolvedValue([]);
    vi.mocked(api.project).mockResolvedValue(project as never);
    vi.mocked(api.updateProject).mockResolvedValue(project as never);
  });

  it("shows the configuration's lists, refuses what is not a list of objects, and saves both, keeping the rest", async () => {
    view();

    const triggers = await screen.findByLabelText("Triggers (JSON)");
    expect((triggers as HTMLTextAreaElement).value).toContain('"name": "incident"');
    const rules = screen.getByLabelText("Tracker start rules (JSON)");

    fireEvent.change(rules, { target: { value: "{\"item_type\": \"Incident\"}" } });
    expect(screen.getByText("A JSON list: [ … ]")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.change(rules, { target: { value: '[{"item_type": "Incident", "states": ["New"]}]' } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Triggers and start rules saved")).toBeInTheDocument();
    expect(api.updateProject).toHaveBeenCalledWith("desk", {
      config: {
        ...project.config,
        triggers: [{ name: "incident", title: "{/number}" }],
        start_rules: [{ item_type: "Incident", states: ["New"] }],
      },
      base_revision: "r-1",
    });
  });
});
