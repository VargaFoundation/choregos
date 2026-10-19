import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MiseEnRoute } from "@/components/mise-en-route";
import type { ProjectReadiness } from "@/lib/types";

const { api } = vi.hoisted(() => ({ api: { projectReadiness: vi.fn() } }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, ...api } };
});

const item = (id: string, state: "done" | "check" | "todo" | "not_needed", href?: string) => ({
  id,
  section: "connections" as const,
  title: `title of ${id}`,
  state,
  detail: `detail of ${id}`,
  fix: { href, by: "project_owner" as const },
});
const readiness = (items: ProjectReadiness["items"]): ProjectReadiness => ({
  ready: !items.some((i) => i.state === "todo"),
  summary: {
    done: items.filter((i) => i.state === "done").length,
    check: items.filter((i) => i.state === "check").length,
    todo: items.filter((i) => i.state === "todo").length,
  },
  items,
});
function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MiseEnRoute slug="dev" />
    </QueryClientProvider>,
  );
}

describe("the getting-started card of the overview (S40-06c)", () => {
  beforeEach(() => vi.resetAllMocks());

  it("lists what the API says, with its fix link, and counts the done items", async () => {
    api.projectReadiness.mockResolvedValue(
      readiness([item("provisioned", "done"), item("connector-cd", "todo", "/p/dev/settings#connections"), item("connector-ci", "not_needed")]),
    );
    renderCard();
    expect((await screen.findByTestId("etape-connector-cd")).getAttribute("data-state")).toBe("todo");
    expect(screen.getByText("1 of 2 done")).toBeTruthy();
    expect(screen.queryByTestId("etape-connector-ci")).toBeNull();
    expect(screen.getByText("Fix").getAttribute("href")).toBe("/p/dev/settings#connections");
  });

  it("is absent once the project is ready (a check does not keep it open)", async () => {
    api.projectReadiness.mockResolvedValue(readiness([item("provisioned", "done"), item("connector-cd", "check")]));
    renderCard();
    await vi.waitFor(() => expect(api.projectReadiness).toHaveBeenCalledWith("dev"));
    expect(screen.queryByTestId("mise-en-route")).toBeNull();
  });
});
