import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AgentBuilder, cleanSpec } from "@/components/agents/builder";
import { diffSpecs } from "@/lib/agent-diff";
import { api } from "@/lib/api";
import { effectiveLanguage, languageLabel, languageLine } from "@/lib/language";

/** An agent's author chooses the language its output is written in: English when unsaid. */

vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: null }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      createAgent: vi.fn().mockResolvedValue({ slug: "assistant", display_name: "Assistant", latest_version: 1 }),
      projects: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      platformModels: vi.fn().mockResolvedValue([]),
      backends: vi.fn().mockResolvedValue([]),
      orgConnectors: vi.fn().mockResolvedValue([]),
      skills: vi.fn().mockResolvedValue([]),
      knowledgeBases: vi.fn().mockResolvedValue([]),
      policyCeiling: vi.fn().mockResolvedValue({ ceiling: {} }),
    },
  };
});

describe("what a version's language means", () => {
  it("is English when unsaid, and named by its tag when the console does not know it", () => {
    expect(effectiveLanguage({})).toBe("en");
    expect(effectiveLanguage({ language: "fr" })).toBe("fr");
    expect(languageLine({})).toBe("English (en) (default)");
    expect(languageLine({ language: "pt-BR" })).toBe("Brazilian Portuguese (pt-BR)");
    expect(languageLabel("fil")).toBe("fil");
  });

  it("is left out of a version that does not say it, so its digest does not move", () => {
    expect(cleanSpec({})).not.toHaveProperty("language", expect.anything());
    expect(cleanSpec({ language: null }).language).toBeUndefined();
    expect(cleanSpec({ language: "de" }).language).toBe("de");
  });

  it("is a change between two versions, and unsaid reads as English", () => {
    expect(diffSpecs({}, { language: "en" })).toEqual([]);
    const [change] = diffSpecs({}, { language: "fr" });
    expect(change).toMatchObject({
      group: "instructions",
      field: "language",
      kind: "changed",
      before: "English (en)",
      after: "French (fr)",
    });
  });
});

describe("the builder's output language", () => {
  beforeEach(() => vi.mocked(api.createAgent).mockClear());

  function open() {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AgentBuilder org="acme" mode={{ kind: "create" }} />
      </QueryClientProvider>,
    );
    return screen.getByTestId("agent-builder");
  }

  async function publish(builder: HTMLElement) {
    fireEvent.change(within(builder).getByLabelText("Slug"), { target: { value: "assistant" } });
    fireEvent.click(within(builder).getByRole("button", { name: "Create and activate" }));
    await waitFor(() => expect(api.createAgent).toHaveBeenCalled());
    return vi.mocked(api.createAgent).mock.calls[0]![1].spec!;
  }

  it("by default sends nothing: the agent writes in English as before", async () => {
    const builder = open();
    expect(within(builder).getByLabelText("Output language")).toHaveValue("");
    expect(within(builder).getByRole("option", { name: "The default: English" })).toBeInTheDocument();
    expect((await publish(builder)).language).toBeUndefined();
  });

  it("sends the language chosen", async () => {
    const builder = open();
    fireEvent.change(within(builder).getByLabelText("Output language"), { target: { value: "fr" } });
    expect(within(builder).getByLabelText("Output language")).toHaveValue("fr");
    expect((await publish(builder)).language).toBe("fr");
  });
});
