// SPDX-License-Identifier: Apache-2.0
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

let pathname = "/";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      me: vi.fn().mockRejectedValue(new real.ApiError(401, { title: "Not authenticated" })),
      orgs: vi.fn().mockResolvedValue([]),
    },
  };
});

import { SessionProvider } from "@/lib/session";

function renderAt(path: string) {
  pathname = path;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SessionProvider>
        <p>the page</p>
      </SessionProvider>
    </QueryClientProvider>,
  );
}

describe("SessionProvider without a session (S63-12)", () => {
  afterEach(() => vi.clearAllMocks());

  it("renders nothing on a console page, which the 401 sends to the sign-in page", async () => {
    const { container } = renderAt("/p/infra");
    await waitFor(() => expect(container.textContent).toBe(""));
  });

  it("still renders the embedded component, which asks the person to sign in itself", async () => {
    renderAt("/embed/assistants/varga/coach");
    await waitFor(() => expect(screen.getByText("the page")).toBeInTheDocument());
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.getByText("the page")).toBeInTheDocument();
  });
});
